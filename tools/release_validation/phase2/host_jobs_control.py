"""New worker registration and original-store beat verification, no task dispatch."""

import asyncio
import time

from contracts import canonical, require
from fixed_host_transport import DOCKER, ROOT, _capture, collect
from host_migration_control import recorded_facts
from host_release_input import require_step
from sql_policy import parse

WORKER_PROBE = b"""import hashlib,json,os,sys
config=_config();validate_config(config);os.environ.update(config);sys.path.insert(0,'/app')
from app.core.celery_app import celery_app
celery_app.loader.import_default_modules()
expected=sorted(n for n in celery_app.tasks if not n.startswith('celery.'))
inspect=celery_app.control.inspect(timeout=5)
registered=inspect.registered();queues=inspect.active_queues();ping=inspect.ping()
name=sys.argv[1]
if not expected or any(type(v)is not dict or set(v)!={name} for v in (registered,queues,ping)):raise ValueError('worker_identity')
actual=sorted(registered[name]);names=sorted(v['name'] for v in queues[name])
if actual!=expected or names!=['backup','crawler','default','report']:raise ValueError('worker_registration')
def hashed(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
print(json.dumps({'count':1,'worker':name,'registered_tasks_sha256':hashed(actual),'expected_tasks_sha256':hashed(expected),'queues':names}))
"""


async def worker_window(spec, journal):
    expected = recorded_facts(spec, journal, "START_WORKER")
    current = (await collect("inspect", "worker"))["facts"]
    require(
        current["id"] == expected["new_container"]["id"]
        and current["image"] == expected["image_id"]
        and current["running"] is True
        and current["oom"] is False
        and current["restarts"] == 0,
        "worker_container_state",
    )
    absent = await _capture(
        DOCKER
        + ("ps", "-q", "--no-trunc", "--filter", "id=" + expected["old_container_id"])
    )
    require(absent.strip() == b"", "worker_old_running")
    hostname = (
        (
            await _capture(
                DOCKER + ("inspect", "--format", "{{.Config.Hostname}}", current["id"])
            )
        )
        .decode("ascii")
        .strip()
    )
    require(
        hostname
        and len(hostname) <= 64
        and all(c in "0123456789abcdefghijklmnopqrstuvwxyz-." for c in hostname),
        "worker_hostname",
    )
    tools = dict(spec._tools)
    prefix = tools["original_api_observer.py"].split(b'if __name__ == "__main__":')[0]
    observed = parse(
        await _capture(
            DOCKER
            + (
                "exec",
                "-i",
                current["id"],
                "python",
                "-I",
                "-B",
                "-",
                "celery@" + hostname,
            ),
            stdin=prefix + b"\n" + WORKER_PROBE,
            timeout=25,
        )
    )
    require(
        observed["count"] == 1
        and observed["worker"] == "celery@" + hostname
        and observed["registered_tasks_sha256"] == observed["expected_tasks_sha256"],
        "worker_registration",
    )
    return {
        "container": current,
        "registration": observed,
        "monotonic_ns": time.monotonic_ns(),
    }


async def verify_worker(spec, journal, baseline):
    require_step(spec, journal, "VERIFY_WORKER")
    first = await worker_window(spec, journal)
    await asyncio.sleep(30)
    second = await worker_window(spec, journal)
    require(
        second["monotonic_ns"] - first["monotonic_ns"] >= 30_000_000_000,
        "worker_stable_window",
    )
    result = {
        "mode": "host_observed",
        "windows": [first, second],
        "possible_new_writes": True,
    }
    journal.directory.create("worker-verified-observed.json", canonical(result))
    return result


def unchanged_beat_store(spec, journal):
    from pathlib import Path

    from beat_health_candidate import _fixed_read, _frozen_start

    expected = recorded_facts(spec, journal, "STOP_BEAT")["schedule"]
    parent = Path(ROOT) / "beat"
    from sql_policy import sha

    for name, pin in expected["files"].items():
        raw = _fixed_read(parent / name, 64 * 1024**2)
        require(
            {"bytes": len(raw), "sha256": sha(raw)} == pin,
            "beat_original_store_changed",
        )
    _frozen_start().validate_existing_schedule(
        parent / "celerybeat-schedule",
        expected["format"],
        parse(spec.package.member("control/beat-expected.json")),
    )
    return expected


async def beat_window(spec, journal):
    expected = recorded_facts(spec, journal, "START_BEAT")
    row = (await collect("inspect", "beat"))["facts"]
    require(
        row["id"] == expected["new_container"]["id"]
        and row["image"] == expected["image_id"]
        and row["running"] is True
        and row["oom"] is False
        and row["restarts"] == 0,
        "beat_container_state",
    )
    require(
        (
            await _capture(
                DOCKER
                + (
                    "ps",
                    "-q",
                    "--no-trunc",
                    "--filter",
                    "id=" + expected["old_container_id"],
                )
            )
        ).strip()
        == b"",
        "beat_old_running",
    )
    raw = await _capture(
        DOCKER + ("exec", row["id"], "python", "/opt/update/beat_health_candidate.py"),
        timeout=15,
    )
    observed = parse(raw)
    schedule = recorded_facts(spec, journal, "STOP_BEAT")["schedule"]
    require(
        observed["healthy"] is True
        and observed["structure_verified"] is True
        and observed["format"] == schedule["format"]
        and observed["task_count"] == 7,
        "beat_original_structure",
    )
    # The fixed health probe validates exactly one beat wrapper/process and the
    # original persistent mount. Directory metadata changes are normal for DBM.
    return {"container": row, "health": observed, "monotonic_ns": time.monotonic_ns()}


async def verify_jobs(spec, journal, baseline):
    require_step(spec, journal, "VERIFY_JOBS")
    windows = []
    for index in range(2):
        if index:
            await asyncio.sleep(30)
        windows.append(
            {
                "worker": await worker_window(spec, journal),
                "beat": await beat_window(spec, journal),
            }
        )
    require(
        windows[1]["beat"]["monotonic_ns"] - windows[0]["beat"]["monotonic_ns"]
        >= 30_000_000_000,
        "jobs_stable_window",
    )
    result = {"mode": "host_observed", "windows": windows, "possible_new_writes": True}
    journal.directory.create("jobs-verified-observed.json", canonical(result))
    return result
