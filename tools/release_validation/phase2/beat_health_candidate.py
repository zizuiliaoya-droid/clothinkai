"""Fixed, read-only original-wrapper/DBM health check; no scheduling or permit issuance."""

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from types import ModuleType

from open_release import verify_beat
from sql_policy import canonical, need


def validate_projection(observed, expected):
    return verify_beat(observed, expected)


def verify_processes(records, pid, commit, db_format):
    need(type(pid) is int and pid > 1 and len(records) == 1, "beat_unique_process")
    record = records[0]
    need(
        record["pid"] == pid
        and record["state"] not in ("Z", "X")
        and type(record["start_ticks"]) is int
        and record["start_ticks"] > 0,
        "beat_pid_identity",
    )
    argv = record["argv"]
    need(
        len(argv) == 7
        and argv[0]
        in ("python", "python3", "/usr/local/bin/python", "/usr/local/bin/python3")
        and argv[1:]
        == [
            "/opt/release/start_067.py",
            "beat",
            "--expected-commit",
            commit,
            "--beat-db-format",
            db_format,
        ],
        "beat_exact_command",
    )
    return {k: record[k] for k in ("pid", "start_ticks")}


def _frozen_start():
    from phase1_package_binding import frozen_contract, schedule_guard_pin

    builder = frozen_contract()
    pins = builder.json_bytes((builder.HERE / "implementation-pins.json").read_bytes())[
        "files"
    ]
    saved = {
        name: sys.modules.get(name)
        for name in ("runtime_guard", "schedule_pickle_guard")
    }
    try:
        for name in ("runtime_guard", "schedule_pickle_guard", "start_067"):
            raw = (builder.HERE / (name + ".py")).read_bytes()
            need(
                schedule_guard_pin(name + ".py", pins[name + ".py"])
                == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()},
                "beat_frozen_dependency",
            )
            module = ModuleType(name)
            module.__file__ = str(builder.HERE / (name + ".py"))
            # dataclass in the frozen parser expects an importable module.
            if name != "start_067":
                sys.modules[name] = module
            exec(compile(raw, module.__file__, "exec"), module.__dict__)
        return module
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _fixed_read(path, limit, private=False):
    for parent in path.parents:
        info = parent.lstat()
        need(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and (not info.st_mode & 0o022 or str(parent) == "/tmp"),
            "beat_parent",
        )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        before = os.fstat(handle.fileno())
        need(
            stat.S_ISREG(before.st_mode)
            and before.st_nlink == 1
            and before.st_uid == 0
            and not before.st_mode & (0o077 if private else 0o022),
            "beat_file",
        )
        raw = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
    need(
        len(raw) <= limit
        and (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        == (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
        "beat_file_changed",
    )
    return raw


def _processes():
    records = []
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            argv = (folder / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            # Count competing ordinary celery beat and original guarded wrappers.
            relevant = b"beat" in argv and any(
                b"celery" in x or x == b"/opt/release/start_067.py" for x in argv
            )
            if relevant:
                text = (folder / "stat").read_text()
                fields = text[text.rfind(")") + 2 :].split()
                records.append(
                    {
                        "pid": int(folder.name),
                        "state": fields[0],
                        "start_ticks": int(fields[19]),
                        "argv": [x.decode() for x in argv],
                    }
                )
        except FileNotFoundError:
            continue
    return records


def collect():
    need(sys.platform == "linux" and os.geteuid() == 0, "beat_linux_root")
    start = _frozen_start()
    release = start.load.__globals__["parse"](
        _fixed_read(Path("/app/release.json"), 65536)
    )
    guard = start.load.__globals__
    guard["validate_release"](release, release["source_commit"])
    permit = guard["parse"](
        _fixed_read(Path("/run/release-guard/start-permit.json"), 65536, True)
    )
    db_format = permit["beat_db_format"]
    guard["validate_permit"](
        permit,
        "start",
        release["source_commit"],
        hashlib.sha256(_fixed_read(Path("/app/release.json"), 65536)).hexdigest(),
        permit["config_sha256"],
        "beat",
        db_format,
    )
    expected = guard["parse"](
        _fixed_read(Path("/opt/release/beat-expected.json"), 65536)
    )
    pid = int(_fixed_read(Path("/tmp/celerybeat.pid"), 32).strip())
    identity = verify_processes(_processes(), pid, release["source_commit"], db_format)
    base = Path("/var/lib/celery/celerybeat-schedule")
    suffixes = start.DBM_LAYOUTS[db_format]
    names = {base.name + suffix for suffix in suffixes}
    found = {p.name for p in base.parent.glob(base.name + "*")}
    allowed = names | ({base.name + ".bak"} if db_format == "dbm.dumb" else set())
    need(names <= found <= allowed, "beat_exact_layout")
    before = {name: start.schedule_stat(base.parent / name) for name in found}
    raw = {name: _fixed_read(base.parent / name, 64 * 1024**2) for name in found}
    # Snapshot avoids GNU DBM's live writer lock. Verify identical source metadata
    # before/after; validate inert pickle opcodes in the frozen phase1 validator.
    with tempfile.TemporaryDirectory(prefix="flow78-beat-health-") as temporary:
        for name, value in raw.items():
            path = Path(temporary) / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(value)
        start.validate_existing_schedule(
            Path(temporary) / base.name, db_format, expected
        )
    need(
        before == {name: start.schedule_stat(base.parent / name) for name in found}
        and {p.name for p in base.parent.glob(base.name + "*")} == found,
        "beat_store_changed",
    )
    need(
        identity
        == verify_processes(_processes(), pid, release["source_commit"], db_format),
        "beat_process_changed",
    )
    return {
        "mode": "linux_observation",
        "healthy": True,
        "pid": pid,
        "format": db_format,
        "task_count": len(expected),
        "structure_verified": True,
        "snapshot_sha256": hashlib.sha256(
            canonical({n: hashlib.sha256(v).hexdigest() for n, v in raw.items()})
        ).hexdigest(),
        "live_enabled": False,
        "deployable": False,
    }


def main():
    try:
        observed = collect()
    except Exception:
        observed = {
            "mode": "linux_observation",
            "healthy": False,
            "code": "beat_observation_unverified",
            "live_enabled": False,
            "deployable": False,
        }
    print(json.dumps(observed, sort_keys=True))
    return 0 if observed["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
