"""Finite four-app replacement from the original public compose definition."""

import asyncio
import os
import time

from contracts import AFTER, canonical, require
from dashboard_contract import ERP_NETWORKS, NETWORK, NETWORK_ID
from fixed_host_transport import DOCKER, INSPECT, ROLES, ROOT, _capture, collect
from host_backup_control import _directory, _mounts, _read
from host_build_control import _release_fd, _write_tree
from host_migration_control import recorded_facts
from host_release_input import require_step
from sql_policy import parse, sha

API_PROBE = b"""import json,urllib.request
result={}
for path in ('/health','/ready'):
 with urllib.request.urlopen('http://127.0.0.1:8000'+path,timeout=5) as r:
  raw=r.read(8193)
  if len(raw)>8192 or r.status!=200:raise ValueError('health')
  result[path]=json.loads(raw)
print(json.dumps(result,sort_keys=True,separators=(',',':')))
"""


def _file(fd, name, raw):
    child = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
    )
    with os.fdopen(child, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(fd)


def host_compose(spec, journal):
    from compose_candidate import generate

    base = parse(dict(spec._tools)["templates/original-compose.json"])
    root = _directory(ROOT)
    try:
        raw = _read(root, "compose.json", 65536)
        require(
            sha(raw)
            == "5b23a8f8d87d0a044db6a41f97b3eef23cd5fd045e23badcb23861ae31b7bb09",
            "app_original_compose_pin",
        )
        actual = parse(raw)
    finally:
        os.close(root)
    require(actual == base, "app_original_compose_drift")
    before = recorded_facts(spec, journal, "PREFLIGHT")["containers"]
    require(
        all(
            base["services"]["prod-" + r]["image"] == before[r]["image"] for r in ROLES
        ),
        "app_original_images",
    )
    backend, frontend = (
        recorded_facts(spec, journal, "BUILD_" + k)["image"]["id"]
        for k in ("BACKEND", "FRONTEND")
    )
    beat = recorded_facts(spec, journal, "STOP_BEAT")["schedule"]["format"]
    result = generate(base, spec.data["source_commit"], backend, frontend, beat)
    # All networks already exist. External declarations prevent Compose from
    # creating or changing an ERP network if the existing one disappears.
    for key, value in result["networks"].items():
        require(value["name"] in {*ERP_NETWORKS, NETWORK}, "app_network_name")
        result["networks"][key] = {"name": value["name"], "external": True}
    return result


def stage(spec, journal, role):
    compose = host_compose(spec, journal)
    release = _release_fd(journal)
    try:
        if role == "api":
            _file(release, "compose.json", canonical(compose))
            _write_tree(release, "control", dict(spec._tools))
            os.mkdir("guards", 0o700, dir_fd=release)
            os.fsync(release)
        else:
            require(
                _read(release, "compose.json", 65536) == canonical(compose),
                "app_compose_changed",
            )
        if role == "web":
            _write_tree(release, "web-control", {})
            from host_web_control import switch_active

            switch_active(spec, journal, "REPLACE_WEB")
        else:
            guards = os.open(
                "guards", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=release
            )
            try:
                beat_format = (
                    recorded_facts(spec, journal, "STOP_BEAT")["schedule"]["format"]
                    if role == "beat"
                    else None
                )
                permit = {
                    "schema_version": 1,
                    "operation": "start",
                    "source_commit": spec.data["source_commit"],
                    "release_sha256": sha(spec.artifact("release")),
                    "config_sha256": spec.data["config_sha256"],
                    "approval_ref": spec.data["approval_ref"],
                    "schema": AFTER,
                    "allowed_roles": [role],
                    "beat_db_format": beat_format,
                }
                # Load only the frozen public guard; this validates the issued
                # finite role permit, it does not read runtime configuration.
                import types

                guard = types.ModuleType("flow78_frozen_start_guard")
                raw = dict(spec._tools)["frozen_phase1/runtime_guard.py"]
                exec(compile(raw, "runtime_guard.py", "exec"), guard.__dict__)
                guard.validate_permit(
                    permit,
                    "start",
                    spec.data["source_commit"],
                    sha(spec.artifact("release")),
                    spec.data["config_sha256"],
                    role,
                    beat_format,
                )
                _write_tree(guards, role, {"start-permit.json": canonical(permit)})
            finally:
                os.close(guards)
    finally:
        os.close(release)
    return compose


async def infrastructure(baseline):
    rows = {}
    for role in ("db", "redis"):
        row = (await collect("inspect", role))["facts"]
        require(
            row["id"] == baseline[role]
            and row["running"] is True
            and row["oom"] is False,
            "app_infrastructure_changed",
        )
        rows[role] = row
    return rows


async def internal_api(identity):
    result = parse(
        await _capture(
            DOCKER + ("exec", "-i", identity, "python", "-I", "-B", "-"),
            stdin=API_PROBE,
            timeout=15,
        )
    )
    require(
        result
        == {
            "/health": {"status": "ok"},
            "/ready": {"status": "ok", "checks": {"db": "ok", "redis": "ok"}},
        },
        "app_readiness",
    )
    return result


async def created_app(role):
    from contracts import sha as require_id

    require(role in ("api", "web", "worker", "beat"), "created_app_role")
    name = "clothinkai-prod-" + role + "-20261018"
    row = parse(
        await _capture(
            DOCKER + ("inspect", "--type", "container", "--format", INSPECT, name)
        )
    )
    require_id(row["id"])
    require(row["name"] == "/" + name, "created_app_name")
    return row


async def replace_app(spec, journal, baseline, role):
    step = {
        "api": "REPLACE_API",
        "web": "REPLACE_WEB",
        "worker": "START_WORKER",
        "beat": "START_BEAT",
    }[role]
    require_step(spec, journal, step)
    prerequisite = {
        "api": "VERIFY_SCHEMA",
        "web": "REPLACE_API",
        "worker": "ACCEPT_INTERNAL",
        "beat": "VERIFY_WORKER",
    }[role]
    recorded_facts(spec, journal, prerequisite)
    if role == "beat":
        from host_jobs_control import unchanged_beat_store

        unchanged_beat_store(spec, journal)
    before_infra = await infrastructure(baseline)
    old = (await collect("inspect", role))["facts"]
    require(
        old["id"] == baseline[role] and old["restart_policy"] == "no",
        "app_original_identity",
    )
    require(old["running"] is (role == "web"), "app_old_state")
    compose = stage(spec, journal, role)
    expected_image = compose["services"]["prod-" + role]["image"]
    journal.directory.create(
        "replace-" + role + "-intent.json",
        canonical(
            {
                "old_container": old,
                "compose_sha256": sha(canonical(compose)),
                "image_id": expected_image,
            }
        ),
    )
    if role == "web":
        from host_stop_control import process_tree, wait_tree_exit

        processes = process_tree(old["pid"])
        journal.directory.create("old-web-processes.json", canonical(processes))
        await _capture(DOCKER + ("stop", "--time", "120", old["id"]), timeout=135)
        await wait_tree_exit(processes)
    argv = DOCKER + (
        "compose",
        "--project-name",
        "clothinkai-production-20261018",
        "-f",
        ROOT + "/releases/" + spec.data["release_id"] + "/compose.json",
        "up",
        "--no-start",
        "--no-deps",
        "--no-build",
        "--pull",
        "never",
        "--force-recreate",
        "prod-" + role,
    )
    await _capture(argv, timeout=120)
    created = await created_app(role)
    require(
        created["id"] != old["id"]
        and created["image"] == expected_image
        and created["running"] is False
        and created["restart_policy"] == "no",
        "app_created_identity",
    )
    journal.directory.create("new-" + role + "-container.json", canonical(created))
    expected_networks = {
        **ERP_NETWORKS,
        **({NETWORK: NETWORK_ID} if role == "web" else {}),
    }
    # Docker may not allocate endpoint IDs until start. Configured names must
    # already be exact; an allocated ID must match. Running readback is strict.
    require(
        set(created["networks"]) == set(expected_networks)
        and all(
            v in ("", expected_networks[k]) for k, v in created["networks"].items()
        ),
        "app_created_networks",
    )
    started = time.monotonic_ns()
    await _capture(DOCKER + ("start", created["id"]), timeout=30)
    deadline = time.monotonic() + 90
    while True:
        current = (await collect("inspect", role))["facts"]
        require(
            current["id"] == created["id"]
            and current["image"] == expected_image
            and current["running"] is True
            and current["oom"] is False
            and current["restarts"] == 0
            and current["networks"] == expected_networks,
            "app_started_identity",
        )
        if role != "api":
            break
        try:
            await internal_api(current["id"])
            break
        except Exception:
            require(time.monotonic() < deadline, "app_readiness_timeout")
            await asyncio.sleep(1)
    if role == "web":
        from host_web_network import ensure_web_network

        connection = await ensure_web_network(spec, journal, current["id"])
        require(
            connection["status"]
            in ("CONNECTED_VERIFIED", "ALREADY_CONNECTED_VERIFIED"),
            "app_web_network_unknown",
        )
    require(
        await infrastructure(baseline) == before_infra, "app_infrastructure_readback"
    )
    mounts = await _mounts(current["id"])
    expected_mounts = []
    for value in compose["services"]["prod-" + role]["volumes"]:
        parts = value.split(":")
        expected_mounts.append(
            {
                "source": parts[0],
                "destination": parts[1],
                "rw": len(parts) == 2,
                "type": "bind",
            }
        )
    require(
        sorted(
            [v for v in mounts["mounts"] if v["type"] == "bind"],
            key=lambda r: r["destination"],
        )
        == sorted(expected_mounts, key=lambda r: r["destination"]),
        "app_mount_readback",
    )
    result = {
        "mode": "host_observed",
        "role": role,
        "old_container_id": old["id"],
        "new_container": current,
        "image_id": expected_image,
        "source_commit": spec.data["source_commit"],
        "compose_sha256": sha(canonical(compose)),
        "started_monotonic_ns": started,
    }
    journal.directory.create("replace-" + role + "-observed.json", canonical(result))
    return result
