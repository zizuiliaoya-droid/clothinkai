"""Real GNU/dumb stores in a disposable container; never production schedules."""

import ast
import dbm
import hashlib
import importlib
import json
import os
import pickle
import sys
import tempfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_jobs_control as jobs
import host_stop_control as stopped
from beat_health_candidate import _fixed_read, _frozen_start
from contracts import Rejected, canonical
from phase1_package_binding import frozen_contract

SOURCE_SHA = "99823396818da52989d3339fe213907110cff9ccd8fa6eb57f5fee50489ae6a5"


def source_schedule(path):
    from celery.schedules import crontab

    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA
    expected = frozen_contract().beat_tasks(raw)
    nodes = [
        n.value
        for n in ast.walk(ast.parse(raw))
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "beat_schedule"
            for t in n.targets
        )
    ]
    assert len(nodes) == 1 and isinstance(nodes[0], ast.Dict)
    result = {}
    for key, value in zip(nodes[0].keys, nodes[0].values):
        name = ast.literal_eval(key)
        if name not in expected:
            continue
        fields = {ast.literal_eval(k): v for k, v in zip(value.keys, value.values)}
        call = fields["schedule"]
        assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        assert call.func.id == "crontab" and not call.args
        kwargs = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        assert None not in kwargs
        result[name] = {
            "task": ast.literal_eval(fields["task"]),
            "schedule": crontab(**kwargs),
            "options": ast.literal_eval(fields["options"]),
        }
    assert {k: v["task"] for k, v in result.items()} == expected
    return expected, result


def make_store(parent, format_name, schedule):
    import celery
    from celery.beat import ScheduleEntry

    parent.mkdir(mode=0o700)
    base = parent / "celerybeat-schedule"
    driver = importlib.import_module(format_name)  # Absence is a failure, never a skip.
    entries = {
        name: ScheduleEntry(
            name=name,
            last_run_at=datetime.now(timezone.utc),
            total_run_count=0,
            args=(),
            kwargs={},
            **value,
        )
        for name, value in schedule.items()
    }
    values = {
        b"entries": entries,
        b"__version__": celery.__version__,
        b"tz": "UTC",
        b"utc_enabled": True,
    }
    with driver.open(str(base), "n") as store:
        for key, value in values.items():
            store[key] = pickle.dumps(value, protocol=4)
    expected = {n: v["task"] for n, v in schedule.items()}
    (parent / "production-schedule.json").write_bytes(canonical(expected))
    for file in parent.iterdir():
        file.chmod(0o600)
    assert dbm.whichdb(str(base)) == format_name
    return base


def rejected(fn):
    try:
        fn()
    except (ValueError, Rejected, OSError) as error:
        return {"rejected": True, "code": str(error)}
    raise AssertionError("invalid DBM fixture was accepted")


class FixtureDirectory:
    def __init__(self, path, fd):
        self.path, self.fd = path, fd

    def create(self, name, raw):
        with (self.path / name).open("xb") as out:
            out.write(raw)


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    expected, schedule = source_schedule(
        Path("/product-source/backend/app/core/celery_app.py")
    )
    frozen = _frozen_start()
    original_root, original_jobs_root, original_facts = (
        stopped.ROOT,
        jobs.ROOT,
        jobs.recorded_facts,
    )
    results = {}
    try:
        with tempfile.TemporaryDirectory(prefix="flow78-dbm-ci-") as temporary:
            root = Path(temporary)
            for index, format_name in enumerate(("dbm.gnu", "dbm.dumb")):
                home = root / str(index)
                home.mkdir(mode=0o700)
                base = make_store(home / "beat", format_name, schedule)
                frozen.validate_existing_schedule(base, format_name, expected)
                stopped.ROOT = jobs.ROOT = str(home)
                state = home / "state"
                state.mkdir(mode=0o700)
                fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)

                directory = FixtureDirectory(state, fd)
                journal = SimpleNamespace(directory=directory)
                spec = SimpleNamespace(
                    package=SimpleNamespace(member=lambda name: canonical(expected))
                )
                try:
                    saved = stopped.snapshot_beat(spec, journal)
                    jobs.recorded_facts = lambda *args, saved=saved: {"schedule": saved}
                    assert jobs.unchanged_beat_store(spec, journal) == saved
                    assert saved["format"] == format_name and saved["task_count"] == 7
                    copy_base = state / "beat-before/celerybeat-schedule"
                    frozen.validate_existing_schedule(copy_base, format_name, expected)
                    checks = {
                        "snapshot_and_unchanged_readback": True,
                        "wrong_format": rejected(
                            lambda base=base, format_name=format_name: (
                                frozen.validate_existing_schedule(
                                    base,
                                    "dbm.dumb"
                                    if format_name == "dbm.gnu"
                                    else "dbm.gnu",
                                    expected,
                                )
                            )
                        ),
                    }
                    driver = importlib.import_module(format_name)
                    if format_name == "dbm.gnu":
                        with driver.open(str(base), "w"):
                            checks["writer_locked_direct_read"] = rejected(
                                lambda base=base, format_name=format_name: (
                                    frozen.validate_existing_schedule(
                                        base, format_name, expected
                                    )
                                )
                            )
                            snapshot = home / "writer-snapshot"
                            snapshot.mkdir(mode=0o700)
                            before = {
                                p.name: frozen.schedule_stat(p)
                                for p in base.parent.glob(base.name + "*")
                            }
                            for name in before:
                                (snapshot / name).write_bytes(
                                    _fixed_read(base.parent / name, 64 * 1024**2)
                                )
                            assert before == {
                                p.name: frozen.schedule_stat(base.parent / p.name)
                                for p in snapshot.iterdir()
                            }
                            frozen.validate_existing_schedule(
                                snapshot / base.name, format_name, expected
                            )
                            checks["locked_writer_snapshot_validated"] = True
                    for label, key, raw in (
                        (
                            "dangerous_pickle",
                            b"entries",
                            pickle.dumps(eval, protocol=4),
                        ),
                        (
                            "wrong_timezone",
                            b"tz",
                            pickle.dumps("Asia/Shanghai", protocol=4),
                        ),
                    ):
                        with driver.open(str(base), "w") as store:
                            previous = store[key]
                            store[key] = raw
                        checks[label] = rejected(
                            lambda base=base, format_name=format_name: (
                                frozen.validate_existing_schedule(
                                    base, format_name, expected
                                )
                            )
                        )
                        checks[label + "_original_hash"] = rejected(
                            partial(jobs.unchanged_beat_store, spec, journal)
                        )
                        with driver.open(str(base), "w") as store:
                            store[key] = previous
                    extra = base.with_name(base.name + ".unexpected")
                    extra.write_bytes(b"synthetic")
                    checks["extra_file"] = rejected(
                        lambda base=base, format_name=format_name: (
                            frozen.validate_existing_schedule(
                                base, format_name, expected
                            )
                        )
                    )
                    extra.unlink()
                    required = (
                        base if format_name == "dbm.gnu" else base.with_suffix(".dat")
                    )
                    moved = home / "saved-required"
                    required.rename(moved)
                    checks["missing_file"] = rejected(
                        lambda base=base, format_name=format_name: (
                            frozen.validate_existing_schedule(
                                base, format_name, expected
                            )
                        )
                    )
                    required.symlink_to(moved)
                    checks["symlink"] = rejected(
                        lambda base=base, format_name=format_name: (
                            frozen.validate_existing_schedule(
                                base, format_name, expected
                            )
                        )
                    )
                    required.unlink()
                    os.link(moved, required)
                    checks["hardlink"] = rejected(
                        lambda base=base, format_name=format_name: (
                            frozen.validate_existing_schedule(
                                base, format_name, expected
                            )
                        )
                    )
                    required.unlink()
                    moved.rename(required)
                    frozen.validate_existing_schedule(base, format_name, expected)
                    results[format_name] = {
                        "format_observed": dbm.whichdb(str(base)),
                        "initial_snapshot": saved,
                        "checks": checks,
                    }
                finally:
                    os.close(fd)
    finally:
        stopped.ROOT, jobs.ROOT, jobs.recorded_facts = (
            original_root,
            original_jobs_root,
            original_facts,
        )
    print(
        json.dumps(
            {
                "mode": "real_linux_synthetic_dbm",
                "source_schedule_sha256": SOURCE_SHA,
                "expected_tasks": expected,
                "formats": results,
                "environment_shims": [
                    "temporary_ROOT",
                    "synthetic_recorded_STOP_BEAT_snapshot",
                    "synthetic_journal_directory",
                    "source_bound_expected_tasks_package_member",
                ],
                "production_store_read": False,
                "production_format_verified": False,
                "host_approval_journal_executed": False,
                "live_enabled": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
