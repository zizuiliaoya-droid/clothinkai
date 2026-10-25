"""Fixed TLS open probes and restore-only-original operational settings."""

import asyncio
import http.client
import os
import ssl
import time
from datetime import datetime, timezone

from contracts import canonical, digest, require
from fixed_host_transport import DOCKER, ROOT, _capture, collect, command
from host_app_control import infrastructure, internal_api
from host_backup_control import _directory, _read, _script_pins
from host_gate_control import (
    dashboard_state,
    nginx_workers,
    read_acme,
    wait_old_workers,
)
from host_migration_control import recorded_facts
from host_release_input import require_step
from host_timer_control import observe_timer
from sql_policy import parse, sha


def public_release(spec):
    context = ssl.create_default_context()
    result = {
        "started_monotonic_ns": time.monotonic_ns(),
        "utc": datetime.now(timezone.utc).isoformat(),
    }
    for path in ("/", "/health", "/ready"):
        conn = http.client.HTTPSConnection(
            "47.110.76.9", 443, timeout=5, context=context
        )
        try:
            conn.connect()
            expiry = ssl.cert_time_to_seconds(conn.sock.getpeercert()["notAfter"])
            require(expiry - time.time() >= 172800, "open_certificate_expiry")
            conn.request(
                "GET", path, headers={"Host": "47.110.76.9", "Connection": "close"}
            )
            response = conn.getresponse()
            raw = response.read(1024 * 1024 + 1)
            require(
                response.status == 200 and len(raw) <= 1024 * 1024,
                "open_public_response",
            )
            if path == "/":
                require(
                    raw == spec.package.member("frontend-dist/index.html"),
                    "open_frontend_source",
                )
            else:
                expected = (
                    {"status": "ok"}
                    if path == "/health"
                    else {"status": "ok", "checks": {"db": "ok", "redis": "ok"}}
                )
                require(parse(raw) == expected, "open_public_health")
            result[path] = {"status": 200, "sha256": sha(raw), "tls_not_after": expiry}
        finally:
            conn.close()
    result["completed_monotonic_ns"] = time.monotonic_ns()
    return result


async def open_gate(spec, journal, baseline):
    require_step(spec, journal, "OPEN_GATE")
    recorded_facts(spec, journal, "VERIFY_JOBS")
    accepted = recorded_facts(spec, journal, "ACCEPT_INTERNAL")
    web = (await collect("inspect", "web"))["facts"]
    replacement = recorded_facts(spec, journal, "REPLACE_WEB")
    require(
        web["id"] == replacement["new_container"]["id"]
        and web["image"] == replacement["image_id"],
        "open_web_identity",
    )
    processes = nginx_workers(web["pid"])
    board_before = await dashboard_state(web["id"])
    from host_web_control import switch_active

    journal.directory.create(
        "open-gate-before.json",
        canonical(
            {
                "web": web,
                "processes": processes,
                "acceptance_sha256": digest(accepted),
                "dashboard": board_before,
            }
        ),
    )
    switched = switch_active(spec, journal, "OPEN_GATE")
    for operation in ("nginx_test", "nginx_reload"):
        argv = tuple(
            web["id"] if v == "clothinkai-prod-web-20261018" else v
            for v in command(operation, "web")
        )
        await _capture(argv, timeout=15)
    reload_tick = time.monotonic_ns()
    await wait_old_workers(processes)
    result = {
        "mode": "host_observed",
        "config": switched,
        "reload_monotonic_ns": reload_tick,
        "reload_utc": datetime.now(timezone.utc).isoformat(),
        "web_id": web["id"],
        "old_workers": 0,
    }
    journal.directory.create("open-gate-observed.json", canonical(result))
    return result


async def verify_open(spec, journal, baseline):
    require_step(spec, journal, "VERIFY_OPEN")
    opened = recorded_facts(spec, journal, "OPEN_GATE")
    windows = []
    for index in range(2):
        if index:
            await asyncio.sleep(30)
        windows.append(await asyncio.to_thread(public_release, spec))
    require(
        windows[0]["started_monotonic_ns"] >= opened["reload_monotonic_ns"]
        and windows[1]["started_monotonic_ns"] - windows[0]["completed_monotonic_ns"]
        >= 30_000_000_000,
        "open_stability_window",
    )
    board = await dashboard_state(opened["web_id"])
    challenge = (
        "/.well-known/acme-challenge/flow78-proof-" + spec.data["source_commit"][:12]
    )
    payload = (sha((spec.sha256 + ":acme").encode()) + "\n").encode()
    acme = await asyncio.to_thread(read_acme, challenge, payload)
    result = {
        "mode": "host_observed",
        "windows": windows,
        "dashboard": board,
        "acme": acme,
        "t_open_interval_monotonic_ns": [
            opened["reload_monotonic_ns"],
            windows[0]["completed_monotonic_ns"],
        ],
        "observation_vantage": "target_host_public_origin",
        "pending_business_samples": ["K7-4", "R33"],
        "independent_external_vantage": "pending",
    }
    journal.directory.create("open-verified-observed.json", canonical(result))
    return result


async def restore_restart(spec, journal, baseline):
    require_step(spec, journal, "RESTORE_RESTART")
    original = parse(journal.directory.read("original-restart.json"))
    require(
        digest(original)
        == recorded_facts(spec, journal, "DISABLE_RESTART")["original_sha256"],
        "restart_original_binding",
    )
    observed = {}
    for role, step in (
        ("api", "REPLACE_API"),
        ("web", "REPLACE_WEB"),
        ("worker", "START_WORKER"),
        ("beat", "START_BEAT"),
    ):
        expected = recorded_facts(spec, journal, step)
        before = (await collect("inspect", role))["facts"]
        require(
            before["id"] == expected["new_container"]["id"]
            and before["image"] == expected["image_id"]
            and before["restart_policy"] == "no",
            "restart_new_identity",
        )
        policy = original[role]["restart_policy"]
        require(policy in ("no", "always", "unless-stopped"), "restart_original_policy")
        await _capture(
            DOCKER + ("update", "--restart=" + policy, before["id"]), timeout=15
        )
        after = (await collect("inspect", role))["facts"]
        require(
            after["id"] == before["id"]
            and after["image"] == before["image"]
            and after["restart_policy"] == policy,
            "restart_readback",
        )
        observed[role] = after
    return {
        "mode": "host_observed",
        "containers": observed,
        "original_sha256": digest(original),
    }


async def restore_timers(spec, journal, baseline):
    require_step(spec, journal, "RESTORE_TIMERS")
    original = parse(journal.directory.read("original-timers.json"))
    require(
        digest(original)
        == recorded_facts(spec, journal, "FREEZE_TIMERS")["original_sha256"],
        "timers_original_binding",
    )
    result = {}
    for subject in ("backup", "renew"):
        before = await observe_timer(subject)
        old = original[subject]["timer"]
        require(
            before["timer"]["active"] is False
            and before["timer"]["enabled"] is False
            and before["timer"]["masked"] == old["masked"],
            "timer_restore_initial_state",
        )
        for condition, operation in (
            (old["enabled"], "enable"),
            (old["active"], "start"),
        ):
            if condition:
                await _capture(
                    (
                        "/usr/bin/systemctl",
                        operation,
                        "clothinkai-production-" + subject + ".timer",
                    ),
                    timeout=15,
                )
        after = await observe_timer(subject)
        require(
            all(after["timer"][k] == old[k] for k in ("active", "enabled", "masked")),
            "timer_restore_readback",
        )
        result[subject] = after
    return {
        "mode": "host_observed",
        "timers": result,
        "original_sha256": digest(original),
    }


async def operations_probe(spec, journal):
    _script_pins()
    api = recorded_facts(spec, journal, "REPLACE_API")["new_container"]["id"]
    # Import only: the uploader main() is not called and no credentials are read.
    program = b"import importlib.util; s=importlib.util.spec_from_file_location('flow78_upload_import','/opt/production/upload_backup.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('UPLOAD_IMPORT_OK')"
    require(
        (
            await _capture(
                DOCKER + ("exec", "-i", api, "python", "-I", "-B", "-"),
                stdin=program,
                timeout=15,
            )
        ).strip()
        == b"UPLOAD_IMPORT_OK",
        "uploader_import",
    )
    originals = legacy_operations()
    require(
        originals == recorded_facts(spec, journal, "PREFLIGHT")["legacy_operations"],
        "operations_original_changed",
    )
    certbot = originals["certbot_image"]
    require(
        (await _capture(DOCKER + ("image", "inspect", "--format", "{{.Id}}", certbot)))
        .decode()
        .strip()
        == certbot,
        "certbot_image_present",
    )
    return {
        "uploader_import": True,
        "certbot_image": certbot,
        "renewal_performed": False,
        "originals": originals,
    }


def legacy_operations():
    from pathlib import Path

    from beat_health_candidate import _fixed_read

    raw = _fixed_read(Path(ROOT) / "build/control/renew.py", 32768)
    require(
        sha(raw) == "44d26837ebbaa9396600e990aac9ab5218f4a1fead63f0c73eaf96efffa955e8",
        "renew_original_script",
    )
    directory = _directory(ROOT)
    try:
        image_raw = _read(directory, "built-images.json", 16384)
        images = parse(image_raw)
    finally:
        os.close(directory)
    certbot = images["certbot"]
    require(
        certbot.startswith("sha256:") and len(certbot) == 71,
        "certbot_original_identity",
    )
    return {
        "certbot_image": certbot,
        "built_images_sha256": sha(image_raw),
        "renew_sha256": sha(raw),
    }


async def verify_complete(spec, journal, baseline):
    require_step(spec, journal, "VERIFY_COMPLETE")
    restart = recorded_facts(spec, journal, "RESTORE_RESTART")
    timers = recorded_facts(spec, journal, "RESTORE_TIMERS")
    infra = await infrastructure(baseline)
    snapshots = []
    for index in range(2):
        if index:
            await asyncio.sleep(30)
        rows = {}
        for role, expected in restart["containers"].items():
            row = (await collect("inspect", role))["facts"]
            require(
                all(row[k] == expected[k] for k in ("id", "image", "restart_policy"))
                and row["running"] is True
                and row["restarts"] == 0
                and row["oom"] is False,
                "complete_app_stability",
            )
            rows[role] = row
        for subject in ("backup", "renew"):
            observed = await observe_timer(subject)
            require(
                all(
                    observed["timer"][k] == timers["timers"][subject]["timer"][k]
                    for k in ("active", "enabled", "masked")
                ),
                "complete_timer_stability",
            )
        await internal_api(rows["api"]["id"])
        public = await asyncio.to_thread(public_release, spec)
        board = await dashboard_state(rows["web"]["id"])
        snapshots.append(
            {
                "containers": rows,
                "public": public,
                "dashboard": board,
                "monotonic_ns": time.monotonic_ns(),
            }
        )
    require(
        await infrastructure(baseline) == infra, "complete_infrastructure_stability"
    )
    result = {
        "mode": "host_observed",
        "windows": snapshots,
        "operations": await operations_probe(spec, journal),
        "automatic_rollback_allowed": False,
        "pending_business_samples": ["K7-4", "R33"],
        "independent_external_vantage": "pending",
    }
    journal.directory.create("complete-observed.json", canonical(result))
    return result
