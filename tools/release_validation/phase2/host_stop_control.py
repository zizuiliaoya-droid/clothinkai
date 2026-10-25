"""Full-ID graceful app stops and original beat DBM snapshot without unpickling."""

import asyncio
import dbm
import os
import time
from pathlib import Path

from contracts import canonical, require
from fixed_host_transport import ROOT, _capture, collect, command
from host_gate_control import _proc
from host_release_input import require_step
from sql_policy import parse, sha


def process_tree(pid):
    pending, records = [pid], []
    while pending:
        current = pending.pop()
        require(
            len(records) < 256 and current not in {r["pid"] for r in records},
            "stop_process_tree_bound",
        )
        row = _proc(current)
        require(row is not None, "stop_process_disappeared_before_snapshot")
        records.append({k: row[k] for k in ("pid", "start_ticks")})
        fd = os.open(
            "/proc/" + str(current) + "/task/" + str(current) + "/children",
            os.O_RDONLY | os.O_NOFOLLOW,
        )
        try:
            raw = os.read(fd, 65537)
        finally:
            os.close(fd)
        require(
            len(raw) <= 65536 and all(v.isdigit() for v in raw.split()),
            "stop_children_projection",
        )
        pending.extend(int(v) for v in raw.split())
    return records


async def wait_tree_exit(records):
    deadline = time.monotonic() + 5
    while True:
        alive = []
        for row in records:
            current = _proc(row["pid"])
            if current is not None and current["start_ticks"] == row["start_ticks"]:
                alive.append(row)
        if not alive:
            return
        require(time.monotonic() < deadline, "stop_original_process_alive")
        await asyncio.sleep(0.25)


def snapshot_beat(spec, journal):
    from beat_health_candidate import _fixed_read, _frozen_start
    from host_build_control import _write_tree

    base = Path(ROOT) / "beat/celerybeat-schedule"
    driver = dbm.whichdb(str(base))
    require(driver in ("dbm.gnu", "dbm.dumb"), "beat_original_format_unreviewed")
    required = {
        "celerybeat-schedule" + suffix
        for suffix in ({""} if driver == "dbm.gnu" else {".dir", ".dat"})
    }
    found = {p.name for p in base.parent.glob("celerybeat-schedule*")}
    require(
        required
        <= found
        <= required | ({"celerybeat-schedule.bak"} if driver == "dbm.dumb" else set()),
        "beat_original_file_set",
    )
    expected = parse(spec.package.member("control/beat-expected.json"))
    proof = _fixed_read(base.parent / "production-schedule.json", 65536)
    require(parse(proof) == expected and len(expected) == 7, "beat_original_tasks")
    frozen = _frozen_start()
    before = {n: frozen.schedule_stat(base.parent / n) for n in found}
    frozen.validate_existing_schedule(base, driver, expected)
    files = {n: _fixed_read(base.parent / n, 64 * 1024**2) for n in found}
    require(
        before == {n: frozen.schedule_stat(base.parent / n) for n in found},
        "beat_snapshot_changed",
    )
    files["production-schedule.json"] = proof
    _write_tree(journal.directory.fd, "beat-before", files)
    result = {
        "format": driver,
        "task_count": 7,
        "structure_verified": True,
        "files": {n: {"bytes": len(b), "sha256": sha(b)} for n, b in files.items()},
    }
    journal.directory.create("beat-before-manifest.json", canonical(result))
    return result


async def stop(spec, journal, baseline, role):
    require(role in ("beat", "worker", "api"), "stop_role")
    require_step(spec, journal, "STOP_" + role.upper())
    before = (await collect("inspect", role))["facts"]
    require(
        before["id"] == baseline[role]
        and before["running"] is True
        and before["restart_policy"] == "no",
        "stop_original_identity",
    )
    tree = process_tree(before["pid"])
    journal.directory.create(
        "stop-" + role + "-processes.json",
        canonical({"container": before, "processes": tree}),
    )
    argv = command("stop", role)
    await _capture(argv[:-1] + (before["id"],), timeout=135)
    after = (await collect("inspect", role))["facts"]
    require(
        after["id"] == before["id"]
        and after["image"] == before["image"]
        and after["running"] is False
        and after["pid"] == 0
        and after["restarting"] is False
        and after["oom"] is False
        and after["exit_code"] == 0,
        "stop_not_graceful",
    )
    await wait_tree_exit(tree)
    facts = {
        "mode": "host_observed",
        "role": role,
        "container": after,
        "old_processes_absent": True,
        "stopped_monotonic_ns": time.monotonic_ns(),
    }
    if role == "beat":
        facts["schedule"] = snapshot_beat(spec, journal)
    elif role == "worker":
        from host_session_contract import two_idle_windows

        facts["sessions"] = await two_idle_windows(baseline["db"])
        api = (await collect("inspect", "api"))["facts"]
        require(
            api["id"] == baseline["api"] and api["running"] is True,
            "stop_worker_backup_api_window",
        )
    else:
        from host_session_contract import observe_sessions

        facts["sessions"] = await observe_sessions(baseline["db"], require_absent=True)
    journal.directory.create("stop-" + role + "-observed.json", canonical(facts))
    return facts
