"""Frozen producers, fixed worker inspection and two complete quiet windows."""

import asyncio
import time

from contracts import Rejected, canonical, exact, require
from fixed_host_transport import DOCKER, _capture, collect
from host_migration_control import recorded_facts
from host_producer_control import verify_frozen
from host_release_input import require_step
from host_session_contract import observe_sessions, two_idle_windows
from sql_policy import parse


def validate_queue(row):
    exact(
        row,
        (
            "worker",
            "active",
            "reserved",
            "scheduled",
            "ready",
            "unacked_hash",
            "unacked_index",
            "priority_steps",
            "broker_transport",
        ),
    )
    require(
        row["priority_steps"] == [0, 3, 6, 9] and row["broker_transport"] == "redis",
        "drain_broker_contract",
    )
    exact(row["ready"], ("default", "backup", "crawler", "report"))
    values = [
        row[k]
        for k in ("active", "reserved", "scheduled", "unacked_hash", "unacked_index")
    ]
    for counts in row["ready"].values():
        require(type(counts) is list and len(counts) == 4, "drain_priority_counts")
        values.extend(counts)
    require(all(type(v) is int and v == 0 for v in values), "drain_not_empty")
    return row


async def queues(spec, baseline):
    # Original API remains running with its business process SIGSTOP'ed; this
    # short independent exec is allowed and must finish before frozen readback.
    hostname = (
        (
            await _capture(
                DOCKER
                + (
                    "inspect",
                    "--type",
                    "container",
                    "--format",
                    "{{.Config.Hostname}}",
                    baseline["worker"],
                )
            )
        )
        .decode("ascii")
        .strip()
    )
    require(
        hostname
        and len(hostname) <= 64
        and all(c in "0123456789abcdefghijklmnopqrstuvwxyz-." for c in hostname),
        "drain_worker_hostname",
    )
    expected = "celery@" + hostname
    tools = dict(spec._tools)
    prefix = tools["original_api_observer.py"].split(b'if __name__ == "__main__":')[0]
    raw = await _capture(
        DOCKER + ("exec", "-i", baseline["api"], "python", "-I", "-B", "-", expected),
        stdin=prefix + b"\n" + tools["host_queue_observer.py"],
        timeout=50,
    )
    row = parse(raw)
    require(row["worker"] == expected, "drain_worker_reply")
    return validate_queue(row)


async def drain(spec, journal, baseline):
    require_step(spec, journal, "DRAIN")
    recorded_facts(spec, journal, "FREEZE_PRODUCERS")
    windows = []
    async with asyncio.timeout(600):
        while len(windows) < 2:
            await verify_frozen(journal, baseline)
            worker = (await collect("inspect", "worker"))["facts"]
            require(
                worker["id"] == baseline["worker"]
                and worker["running"] is True
                and worker["restart_policy"] == "no",
                "drain_worker_identity",
            )
            try:
                queue = await queues(spec, baseline)
                sessions = await observe_sessions(baseline["db"])
            except Rejected as exc:
                if str(exc) not in ("drain_not_empty", "session_not_quiescent"):
                    raise
                windows.clear()
                await asyncio.sleep(5)
                continue
            await verify_frozen(journal, baseline)
            windows.append(
                {
                    "monotonic_ns": time.monotonic_ns(),
                    "queues": queue,
                    "sessions": sessions,
                }
            )
            if len(windows) == 1:
                await asyncio.sleep(30)
    require(
        windows[1]["monotonic_ns"] - windows[0]["monotonic_ns"] >= 30_000_000_000,
        "drain_window",
    )
    result = {
        "mode": "host_observed",
        "windows": windows,
        "queue_payloads_emitted": False,
        "old_http_connections": 0,
    }
    journal.directory.create("drain-observed.json", canonical(result))
    return result


async def freeze_verify(spec, journal, baseline):
    require_step(spec, journal, "FREEZE_VERIFY")
    recorded_facts(spec, journal, "DRAIN")
    recorded_facts(spec, journal, "STOP_WORKER")
    proof = await verify_frozen(journal, baseline)
    stopped = {}
    for role in ("worker", "beat"):
        row = (await collect("inspect", role))["facts"]
        require(
            row["id"] == baseline[role]
            and row["running"] is False
            and row["pid"] == 0
            and row["restart_policy"] == "no",
            "freeze_background_running",
        )
        stopped[role] = row
    sessions = await two_idle_windows(baseline["db"])
    await verify_frozen(journal, baseline)
    result = {
        "mode": "host_observed",
        "containers": stopped,
        "api_frozen": proof,
        "sessions": sessions,
        "freeze_tick": time.monotonic_ns(),
        "old_http_connections": 0,
    }
    journal.directory.create("freeze-verified-observed.json", canonical(result))
    return result
