"""Freeze the one publicly specified uvicorn process; never resume it automatically.

This uses process SIGSTOP, not Docker pause: the original backup uploader must
still be able to run as a separate docker exec process. Linux/Docker validation
of that distinction is required before this candidate can be approved.
"""

import asyncio
import os
import signal
import time

from contracts import canonical, require
from fixed_host_transport import DOCKER, _capture, collect
from host_gate_control import _proc
from host_release_input import require_step
from sql_policy import parse

UVICORN = (
    b"-m",
    b"uvicorn",
    b"app.main:app",
    b"--host",
    b"0.0.0.0",
    b"--port",
    b"8000",
    b"--proxy-headers",
    b"--forwarded-allow-ips",
    b"*",
    b"--no-access-log",
)


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.read(fd, 65537)
        require(len(value) <= 65536, "producer_proc_bound")
        return value
    finally:
        os.close(fd)


def process_group(pid):
    row = _proc(pid)
    require(row is not None, "producer_process_absent")
    argv = row["command"].split(b"\0")
    require(
        tuple(argv[1:]) == UVICORN
        and argv[0].rsplit(b"/", 1)[-1]
        in (b"python", b"python3", b"python3.12", b"python3.13", b"python3.14"),
        "producer_launch_contract",
    )
    cgroup = _read("/proc/" + str(pid) + "/cgroup").decode("ascii").splitlines()
    require(
        len(cgroup) == 1 and cgroup[0].startswith("0::/"), "producer_cgroup_v2_required"
    )
    components = cgroup[0][3:].split("/")[1:]
    require(
        components and all(c and c not in (".", "..") for c in components),
        "producer_cgroup_path",
    )
    fd = os.open("/sys/fs/cgroup", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in components:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        child = os.open("cgroup.procs", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            raw = os.read(child, 65537)
        finally:
            os.close(child)
        require(
            len(raw) <= 65536
            and all(x.isdigit() for x in raw.split())
            and [int(x) for x in raw.split()] == [pid],
            "producer_uncovered_process",
        )
    finally:
        os.close(fd)
    namespace = os.readlink("/proc/" + str(pid) + "/ns/pid")
    threads = []
    task = "/proc/" + str(pid) + "/task"
    for token in os.listdir(task):
        require(token.isdigit() and len(threads) < 256, "producer_thread_bound")
        fields = (
            _read(task + "/" + token + "/stat")
            .decode("ascii")
            .rsplit(")", 1)[1]
            .split()
        )
        require(
            os.readlink(task + "/" + token + "/ns/pid") == namespace,
            "producer_thread_namespace",
        )
        require(
            _read(task + "/" + token + "/children").strip() == b"",
            "producer_uncovered_child",
        )
        threads.append(
            {"tid": int(token), "state": fields[0], "start_ticks": int(fields[19])}
        )
    require(threads and any(t["tid"] == pid for t in threads), "producer_main_thread")
    return {
        "pid": pid,
        "start_ticks": row["start_ticks"],
        "pid_namespace": namespace,
        "cgroup": cgroup[0][3:],
        "threads": sorted(threads, key=lambda x: x["tid"]),
    }


def same_group(before, after, *, frozen):
    require(
        all(
            before[k] == after[k]
            for k in ("pid", "start_ticks", "pid_namespace", "cgroup")
        ),
        "producer_process_replaced",
    )
    require(
        [(r["tid"], r["start_ticks"]) for r in before["threads"]]
        == [(r["tid"], r["start_ticks"]) for r in after["threads"]],
        "producer_threads_changed",
    )
    if frozen:
        require(
            all(r["state"] == "T" for r in after["threads"]), "producer_not_stopped"
        )


async def verify_frozen(journal, baseline):
    proof = parse(journal.directory.read("api-frozen-observed.json"))
    current = (await collect("inspect", "api"))["facts"]
    require(
        current["id"] == baseline["api"] == proof["container_id"]
        and current["running"] is True
        and current["paused"] is False
        and current["restart_policy"] == "no"
        and current["pid"] == proof["process"]["pid"],
        "producer_container_changed",
    )
    same_group(proof["process"], process_group(current["pid"]), frozen=True)
    return proof


async def freeze(spec, journal, baseline):
    require_step(spec, journal, "FREEZE_PRODUCERS")
    from host_migration_control import recorded_facts

    recorded_facts(spec, journal, "CLOSE_GATE")
    recorded_facts(spec, journal, "STOP_BEAT")
    row = (await collect("inspect", "api"))["facts"]
    require(
        row["id"] == baseline["api"]
        and row["running"] is True
        and row["paused"] is False
        and row["restart_policy"] == "no",
        "producer_original_api",
    )
    before = process_group(row["pid"])
    fd = os.pidfd_open(row["pid"], 0)
    try:
        same_group(before, process_group(row["pid"]), frozen=False)
        journal.directory.create(
            "api-freeze-intent.json",
            canonical({"container_id": row["id"], "process": before}),
        )
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while True:
            after = process_group(row["pid"])
            same_group(before, after, frozen=False)
            if all(t["state"] == "T" for t in after["threads"]):
                break
            require(time.monotonic() < deadline, "producer_stop_timeout")
            await asyncio.sleep(0.05)
    finally:
        os.close(fd)
    result = {
        "mode": "host_observed",
        "container_id": row["id"],
        "process": after,
        "freeze_monotonic_ns": time.monotonic_ns(),
        "automatic_resume_allowed": False,
    }
    journal.directory.create("api-frozen-observed.json", canonical(result))
    return result


async def terminate(spec, journal, baseline):
    """End the frozen process without SIGCONT or allowing buffered work to resume."""
    require_step(spec, journal, "STOP_API")
    proof = await verify_frozen(journal, baseline)
    pid = proof["process"]["pid"]
    fd = os.pidfd_open(pid, 0)
    try:
        same_group(proof["process"], process_group(pid), frozen=True)
        journal.directory.create("api-terminate-intent.json", canonical(proof))
        signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        os.close(fd)
    code = await _capture(DOCKER + ("wait", baseline["api"]), timeout=15)
    require(code.strip() == b"137", "producer_termination_exit")
    after = (await collect("inspect", "api"))["facts"]
    require(
        after["id"] == baseline["api"]
        and after["running"] is False
        and after["pid"] == 0
        and after["exit_code"] == 137
        and after["oom"] is False,
        "producer_termination_readback",
    )
    from host_session_contract import observe_sessions
    from host_stop_control import wait_tree_exit

    await wait_tree_exit([{"pid": pid, "start_ticks": proof["process"]["start_ticks"]}])
    sessions = await observe_sessions(baseline["db"], require_absent=True)
    result = {
        "mode": "host_observed",
        "role": "api",
        "container": after,
        "sessions": sessions,
        "termination": "frozen_process_sigkill_no_resume",
        "old_processes_absent": True,
    }
    journal.directory.create("stop-api-observed.json", canonical(result))
    return result
