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


PROBE = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3).close()"
SHELL_PROBE = 'python -c "' + PROBE + '"'
HEALTH = {
    "Test": ["CMD-SHELL", SHELL_PROBE],
    "Interval": 10_000_000_000,
    "Timeout": 4_000_000_000,
    "StartPeriod": 30_000_000_000,
    "Retries": 3,
}


async def original_healthcheck(identity, pid):
    # Deliberately exclude Env and all unrelated container configuration.
    projection = "{{json .Id}}|{{json .State.Pid}}|{{json .Config.Healthcheck}}"
    raw = await _capture(
        DOCKER + ("inspect", "--format", projection, identity), timeout=10
    )
    fields = raw.strip().split(b"|", 2)
    require(len(fields) == 3, "producer_healthcheck_projection")
    require(
        parse(fields[0]) == identity and parse(fields[1]) == pid,
        "producer_healthcheck_container_changed",
    )
    health = parse(fields[2])
    require(health == HEALTH, "producer_healthcheck_config")
    return health


def validate_probe_pair(main, rows, shell_exe, ticks, hz):
    """A complete, stable original CMD-SHELL pair only; partial samples HOLD."""
    require(len(rows) == 2, "producer_healthcheck_incomplete_pair")
    shell = next((r for r in rows if r["argv"] == ["/bin/sh", "-c", SHELL_PROBE]), None)
    python = next((r for r in rows if r["argv"] == ["python", "-c", PROBE]), None)
    require(shell is not None and python is not None, "producer_healthcheck_argv")
    members = {main["pid"], shell["pid"], python["pid"]}
    require(
        len(members) == 3
        and shell["ppid"] > 1
        and shell["ppid"] == main["ppid"]
        and shell["ppid"] not in members
        and python["ppid"] == shell["pid"],
        "producer_healthcheck_ancestry",
    )
    require(
        shell["exe"] == shell_exe and python["exe"] == main["exe"],
        "producer_healthcheck_executable",
    )
    for row in rows:
        require(
            all(row[k] == main[k] for k in ("namespaces", "cgroup", "uids")),
            "producer_healthcheck_identity",
        )
        require(row["tasks"] == [row["pid"]], "producer_healthcheck_threads")
        require(
            main["start_ticks"] <= row["start_ticks"] <= ticks
            and ticks - row["start_ticks"] <= 5 * hz,
            "producer_healthcheck_lifetime",
        )
    require(
        shell["children"] == [python["pid"]] and python["children"] == [],
        "producer_healthcheck_children",
    )
    return rows


def _health_identity(pid):
    row = _proc(pid)
    require(row is not None, "producer_healthcheck_sample_changed")
    prefix = "/proc/" + str(pid)
    executable = os.stat(prefix + "/exe")
    uid = [
        line.split()[1:]
        for line in _read(prefix + "/status").splitlines()
        if line.startswith(b"Uid:")
    ]
    require(len(uid) == 1 and len(uid[0]) == 4, "producer_healthcheck_uid")
    return {
        "pid": pid,
        "ppid": row["ppid"],
        "start_ticks": row["start_ticks"],
        "argv": [a.decode("ascii") for a in row["command"].split(b"\0")],
        "exe": [executable.st_dev, executable.st_ino],
        "namespaces": {
            n: os.readlink(prefix + "/ns/" + n) for n in ("pid", "mnt", "net", "user")
        },
        "uids": [int(v) for v in uid[0]],
        "cgroup": _read(prefix + "/cgroup").decode("ascii"),
    }


def _health_pair(pid, members):
    require(len(members) == 3, "producer_healthcheck_incomplete_pair")
    try:
        main = _health_identity(pid)
        rows = []
        for member in sorted(members - {pid}):
            row = _health_identity(member)
            task = "/proc/" + str(member) + "/task"
            tokens = os.listdir(task)
            require(all(t.isdigit() for t in tokens), "producer_healthcheck_threads")
            row["tasks"] = sorted(int(t) for t in tokens)
            row["children"] = [
                int(t) for t in _read(task + "/" + str(member) + "/children").split()
            ]
            rows.append(row)
        shell_executable = os.stat("/proc/" + str(pid) + "/root/bin/sh")
        hz = os.sysconf("SC_CLK_TCK")
        ticks = int(time.clock_gettime(time.CLOCK_BOOTTIME) * hz)
        validate_probe_pair(
            main, rows, [shell_executable.st_dev, shell_executable.st_ino], ticks, hz
        )
        # Re-read every identity and child set. An exiting/forking probe is HOLD,
        # never a successful observation manufactured by waiting out the probe.
        for row in rows:
            again = _health_identity(row["pid"])
            require(
                all(row[k] == v for k, v in again.items()),
                "producer_healthcheck_sample_changed",
            )
            task = "/proc/" + str(row["pid"]) + "/task"
            require(
                sorted(int(t) for t in os.listdir(task)) == row["tasks"]
                and [
                    int(t)
                    for t in _read(task + "/" + str(row["pid"]) + "/children").split()
                ]
                == row["children"],
                "producer_healthcheck_sample_changed",
            )
        require(_health_identity(pid) == main, "producer_healthcheck_sample_changed")
        return rows
    except (FileNotFoundError, ProcessLookupError):
        require(False, "producer_healthcheck_sample_changed")


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.read(fd, 65537)
        require(len(value) <= 65536, "producer_proc_bound")
        return value
    finally:
        os.close(fd)


def process_group(pid, *, healthcheck=None):
    if healthcheck is not None:
        require(healthcheck == HEALTH, "producer_healthcheck_config")
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
            and pid in {int(x) for x in raw.split()},
            "producer_uncovered_process",
        )
        members = {int(x) for x in raw.split()}
        probes = []
        if members != {pid}:
            require(healthcheck is not None, "producer_uncovered_process")
            probes = _health_pair(pid, members)
            child = os.open("cgroup.procs", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                again = os.read(child, 65537)
            finally:
                os.close(child)
            require(
                len(again) <= 65536
                and all(x.isdigit() for x in again.split())
                and {int(x) for x in again.split()} == members,
                "producer_healthcheck_sample_changed",
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
        "healthcheck_processes": probes,
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
    health = await original_healthcheck(current["id"], current["pid"])
    require(proof["healthcheck"] == health, "producer_healthcheck_config")
    same_group(
        proof["process"], process_group(current["pid"], healthcheck=health), frozen=True
    )
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
    health = await original_healthcheck(row["id"], row["pid"])
    before = process_group(row["pid"], healthcheck=health)
    fd = os.pidfd_open(row["pid"], 0)
    try:
        same_group(before, process_group(row["pid"], healthcheck=health), frozen=False)
        journal.directory.create(
            "api-freeze-intent.json",
            canonical(
                {"container_id": row["id"], "process": before, "healthcheck": health}
            ),
        )
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while True:
            after = process_group(row["pid"], healthcheck=health)
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
        "healthcheck": health,
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
        same_group(
            proof["process"],
            process_group(pid, healthcheck=proof["healthcheck"]),
            frozen=True,
        )
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
