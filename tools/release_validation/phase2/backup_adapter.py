"""Single original-backup invocation description and strict receipt association."""

import hashlib
import re
from contracts import digest, exact, require, sha

MAX_DUMP = 2 * 1024**3


def plan(facts):
    exact(
        facts,
        (
            "api_running",
            "backup_lock_owned",
            "timers_idle",
            "sessions",
            "stored_bytes",
            "free_bytes",
        ),
    )
    require(
        facts["api_running"] is True
        and facts["backup_lock_owned"] is False
        and facts["timers_idle"] is True
        and facts["sessions"] == 0,
        "backup_dependencies",
    )
    require(
        facts["stored_bytes"] < 10 * 1024**3 and facts["free_bytes"] >= 10 * 1024**3,
        "backup_space",
    )
    return {
        "argv": (
            "python3",
            "/srv/clothinkai/production-20261018/build/control/backup.py",
        ),
        "timeout_seconds": 2100,
        "acquire_backup_lock_in_parent": False,
        "automatic_retry": False,
        "executable": False,
    }


def verify_stream(stream, size, expected_sha):
    sha(expected_sha)
    require(type(size) is int and 0 < size <= MAX_DUMP, "backup_size")
    hashed, count = hashlib.sha256(), 0
    while block := stream.read(1024 * 1024):
        count += len(block)
        require(count <= size, "readback_overflow")
        hashed.update(block)
    require(
        count == size and hashed.hexdigest() == expected_sha, "full_readback_mismatch"
    )
    return {"bytes": count, "sha256": hashed.hexdigest()}


def verify(before, after, receipt, freeze_tick, action_id, api_id):
    require(
        all(after.get(name) == value for name, value in before.items()),
        "existing_backup_changed",
    )
    added = set(after) - set(before)
    dumps = [
        name
        for name in added
        if re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.dump", name)
    ]
    require(
        len(dumps) == 1 and added == {dumps[0], dumps[0] + ".json"},
        "unique_backup_pair",
    )
    name = dumps[0]
    archive, sidecar = after[name], after[name + ".json"]
    for item in (archive, sidecar):
        exact(item, ("bytes", "sha256", "created_tick", "kind"))
        sha(item["sha256"])
        require(
            type(item["bytes"]) is int
            and 0 < item["bytes"] <= MAX_DUMP
            and type(item["created_tick"]) is int
            and item["created_tick"] >= 0,
            "backup_metadata",
        )
    exact(
        receipt,
        (
            "action_id",
            "started_tick",
            "finished_tick",
            "exit_code",
            "archive_list",
            "archive_full_read",
            "local_sha256",
            "local_bytes",
            "r2_get_sha256",
            "r2_get_bytes",
            "r2_get_complete",
            "existing_objects_changed",
            "sidecar_sha256",
            "api_id",
        ),
    )
    for key in ("local_sha256", "r2_get_sha256", "sidecar_sha256"):
        sha(receipt[key])
    for key in ("started_tick", "finished_tick", "local_bytes", "r2_get_bytes"):
        require(type(receipt[key]) is int and receipt[key] >= 0, "backup_receipt_types")
    require(type(freeze_tick) is int and freeze_tick >= 0, "freeze_tick")
    require(
        receipt["action_id"] == action_id
        and receipt["api_id"] == api_id
        and freeze_tick
        <= receipt["started_tick"]
        <= archive["created_tick"]
        <= sidecar["created_tick"]
        <= receipt["finished_tick"],
        "backup_action_freshness",
    )
    require(
        type(receipt["exit_code"]) is int
        and receipt["exit_code"] == 0
        and receipt["archive_list"] is True
        and receipt["archive_full_read"] is True
        and receipt["r2_get_complete"] is True
        and receipt["existing_objects_changed"] is False,
        "backup_incomplete",
    )
    require(
        archive["kind"] == "dump"
        and sidecar["kind"] == "sidecar"
        and 0 < archive["bytes"] <= MAX_DUMP
        and receipt["local_bytes"] == receipt["r2_get_bytes"] == archive["bytes"]
        and archive["sha256"] == receipt["local_sha256"] == receipt["r2_get_sha256"]
        and sidecar["sha256"] == receipt["sidecar_sha256"],
        "backup_readback",
    )
    return {
        "dump_sha256": archive["sha256"],
        "dump_name": name,
        "receipt_sha256": digest(receipt),
        "freeze_tick": freeze_tick,
        "mode": "synthetic",
        "existing_backup_api_required": True,
    }
