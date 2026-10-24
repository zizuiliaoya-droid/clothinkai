"""Maintenance descriptions and a sandbox-only atomic file-switch model."""

import hashlib
import os
import posixpath
import re
from copy import deepcopy
from urllib.parse import unquote

from contracts import Rejected, exact, require, sha
from journal import regular, sync_parent

PROBES = (
    "/",
    "/api",
    "/api/",
    "/api/auth/login",
    "/api/uploads/presign",
    "/api/callback",
    "/%61pi/callback",
    "/api%2Fcallback",
    "/health",
    "/ready",
)


def freeze_plan(state):
    exact(state, ("backup", "renew"))
    commands = []
    for name in ("backup", "renew"):
        row = state[name]
        exact(
            row,
            (
                "active",
                "enabled",
                "masked",
                "unit_sha256",
                "service_running",
                "queued_jobs",
                "child_processes",
            ),
        )
        sha(row["unit_sha256"])
        require(
            all(
                type(row[k]) is bool
                for k in ("active", "enabled", "masked", "service_running")
            ),
            "timer_state",
        )
        require(
            row["service_running"] is False
            and row["queued_jobs"] == 0
            and row["child_processes"] == 0,
            "timer_inflight",
        )
        require(
            not row["masked"] or (not row["active"] and not row["enabled"]),
            "masked_timer_conflict",
        )
        unit = "clothinkai-production-" + name + ".timer"
        if row["active"]:
            commands.append(("systemctl", "stop", unit))
        if row["enabled"]:
            commands.append(("systemctl", "disable", unit))
    return {
        "commands": tuple(commands),
        "original": deepcopy(state),
        "executable": False,
        "must_observe_idle_after_disable": True,
        "manual_entries_must_be_frozen": True,
    }


def expected_status(path):
    path = posixpath.normpath(unquote(path.split("?", 1)[0]))
    require(
        path != "/progress" and not path.startswith("/progress/"),
        "dashboard_separate_probe",
    )
    return (
        200
        if re.fullmatch(r"/\.well-known/acme-challenge/[A-Za-z0-9_-]{1,128}", path)
        else 503
    )


def probe_results():
    return {path: 503 for path in PROBES}


def verify_closed(facts):
    exact(
        facts,
        (
            "syntax_ok",
            "reload_ok",
            "old_nginx_workers",
            "old_connections",
            "public_status",
            "acme_status",
        ),
    )
    require(
        facts["syntax_ok"] is True and facts["reload_ok"] is True, "nginx_not_loaded"
    )
    require(
        facts["old_nginx_workers"] == 0 and facts["old_connections"] == 0,
        "old_requests_alive",
    )
    require(
        facts["public_status"] == probe_results() and facts["acme_status"] == 200,
        "gate_observation",
    )
    return {"synthetic_gate_verified": True, "live_http_verified": False}


def close_fixture(journal, old_sha, closed, fault=None):
    require(type(closed) is bytes and 0 < len(closed) <= 65536, "closed_config_size")
    active = journal.root / "nginx-active.conf"
    regular(active)
    require(
        active.is_file() and hashlib.sha256(active.read_bytes()).hexdigest() == old_sha,
        "old_config_changed",
    )
    acquired = journal.lock is None
    if acquired:
        journal.acquire()
    try:
        # Preserve only this exact file; never replace the whole legacy control directory.
        original = active.read_bytes()
        info, parent_info = active.stat(), journal.root.stat()
        journal.atomic_artifact("old-active.conf", original)
        from contracts import canonical

        metadata = {
            "mode": info.st_mode & 0o7777,
            "uid": info.st_uid,
            "gid": info.st_gid,
            "original_sha256": old_sha,
            "parent_mode": parent_info.st_mode & 0o7777,
            "parent_uid": parent_info.st_uid,
            "parent_gid": parent_info.st_gid,
        }
        journal.atomic_artifact("old-active.metadata.json", canonical(metadata))
        pending = journal.root / "closed-config.pending"
        regular(pending)
        require(not pending.exists(), "pending_config_exists")
        with pending.open("xb") as output:
            current = os.fstat(output.fileno())
            require(
                (current.st_uid, current.st_gid) == (info.st_uid, info.st_gid),
                "owner_preservation_unknown",
            )
            os.chmod(pending, info.st_mode & 0o7777)
            output.write(closed)
            output.flush()
            os.fsync(output.fileno())
        if fault == "before_replace":
            raise Rejected("replace_interrupted")
        os.replace(pending, active)
        sync_parent(journal.root)
        if fault == "after_replace":
            raise Rejected("result_lost")
        require(active.read_bytes() == closed, "closed_readback")
        return {
            "original_sha256": old_sha,
            "closed_sha256": hashlib.sha256(closed).hexdigest(),
            "mode": "synthetic",
            "live_nginx_reloaded": False,
        }
    finally:
        if acquired:
            journal.release()


def close_dashboard_fixture(journal, original, closed, fault=None):
    """Exercise the board-preserving file boundary without claiming a live reload."""
    from dashboard_contract import original_active, validate_routes

    original_active(original)
    validate_routes(closed)
    return close_fixture(journal, hashlib.sha256(original).hexdigest(), closed, fault)


def restore_dashboard_bytes(original):
    """Recovery must use the exact pre-upgrade, already-board-enabled active."""
    from dashboard_contract import original_active

    return original_active(original)
