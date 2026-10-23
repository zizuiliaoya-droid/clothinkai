"""Explicit business start; requires updater-issued permit and existing schema067."""

import argparse
import asyncio
import dbm
import importlib
import json
import os
from pathlib import Path
import stat
import sys

from runtime_guard import AFTER, GuardError, connect, load, need, verify_connection
from schedule_pickle_guard import validate_value

EXCLUSIONS = {
    "backup-database-daily": "app.tasks.backup_tasks.backup_database",
    "cleanup-expired-backups-daily": "app.tasks.backup_tasks.cleanup_expired_backups",
    "archive-audit-logs-monthly": "app.tasks.cleanup_tasks.archive_audit_logs",
}
DBM_LAYOUTS = {"dbm.gnu": {""}, "dbm.dumb": {".dat", ".dir"}}


async def preflight(config):
    for key, role, bypass in (
        ("DATABASE_URL_APP", "clothing_app", False),
        ("DATABASE_URL_BYPASS", "clothing_bypass", True),
    ):
        conn = await connect(config, key)
        try:
            await verify_connection(conn, role, bypass, AFTER)
            if bypass:
                need(
                    await conn.fetchval(
                        "SELECT EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.user_role ur ON ur.user_id=u.id JOIN public.role r ON r.id=ur.role_id JOIN public.tenant t ON t.id=u.tenant_id WHERE t.code='default' AND r.code='admin' AND u.deleted_at IS NULL)"
                    ),
                    "existing_administrator",
                )
        finally:
            await conn.close()


def filtered_schedule(schedule, expected):
    need(
        all(schedule.get(name, {}).get("task") == task for name, task in EXCLUSIONS.items()),
        "maintenance_exclusions_changed",
    )
    result = {name: entry for name, entry in schedule.items() if name not in EXCLUSIONS}
    need(
        {name: entry["task"] for name, entry in result.items()} == expected and len(result) == 7,
        "business_schedule_changed",
    )
    return result


def schedule_stat(path):
    need(not path.is_symlink() and not path.is_junction(), "schedule_link")
    item = path.lstat()
    need(stat.S_ISREG(item.st_mode) and item.st_nlink == 1, "schedule_regular_file")
    need(0 < item.st_size <= 64 * 1024**2, "schedule_file_size")
    return (
        item.st_dev,
        item.st_ino,
        item.st_nlink,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )


def validate_existing_schedule(base, expected_format, expected):
    """Read existing DBM bytes only; never create a database or unpickle objects."""
    need(expected_format in DBM_LAYOUTS, "schedule_format_unreviewed")
    for path in (base.parent, *base.parent.parents):
        need(not path.is_symlink() and not path.is_junction(), "schedule_parent_link")
    found = {p.name: p for p in base.parent.glob(base.name + "*")}
    required = {base.name + suffix for suffix in DBM_LAYOUTS[expected_format]}
    allowed = required | ({base.name + ".bak"} if expected_format == "dbm.dumb" else set())
    need(required <= set(found) <= allowed, "schedule_file_layout")
    before = {name: schedule_stat(path) for name, path in found.items()}
    try:
        need(dbm.whichdb(str(base)) == expected_format, "schedule_format_mismatch")
        driver = importlib.import_module(expected_format)
        with driver.open(str(base), "r") as database:
            keys = set(database.keys())
            need(keys == {b"entries", b"__version__", b"tz", b"utc_enabled"}, "schedule_metadata")
            total = 0
            for key in keys:
                raw = database[key]
                total += len(raw)
                need(len(raw) <= 16 * 1024**2 and total <= 64 * 1024**2, "schedule_value_limit")
                validate_value(key, raw, expected)
    except GuardError:
        raise
    except Exception:
        raise GuardError("schedule_database_unreadable") from None
    need(
        {name: schedule_stat(path) for name, path in found.items()} == before
        and {p.name for p in base.parent.glob(base.name + "*")} == set(found),
        "schedule_changed_during_read",
    )


def dispatch(role, beat_db_format=None):
    os.chdir("/app")
    sys.path.insert(0, "/app")
    if role == "api":
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--proxy-headers",
                "--forwarded-allow-ips",
                "*",
                "--no-access-log",
            ],
        )
        return
    from app.core.celery_app import celery_app

    if role == "worker":
        celery_app.worker_main(
            [
                "worker",
                "--loglevel=WARNING",
                "--concurrency=2",
                "--queues=default,backup,crawler,report",
                "--without-gossip",
                "--without-mingle",
            ]
        )
        return
    expected = json.loads(Path("/opt/release/beat-expected.json").read_bytes())
    celery_app.conf.beat_schedule = filtered_schedule(dict(celery_app.conf.beat_schedule), expected)
    proof = json.dumps(expected, sort_keys=True).encode()
    proof_path = Path("/var/lib/celery/production-schedule.json")
    # Existing scheduler data is required. This is not an initial deployment or a reset.
    need(
        proof_path.is_file() and not proof_path.is_symlink() and proof_path.read_bytes() == proof,
        "existing_schedule_proof",
    )
    validate_existing_schedule(
        Path("/var/lib/celery/celerybeat-schedule"), beat_db_format, expected
    )
    celery_app.start(
        [
            "beat",
            "--loglevel=WARNING",
            "--pidfile=/tmp/celerybeat.pid",
            "--schedule=/var/lib/celery/celerybeat-schedule",
        ]
    )


def run(role, commit, beat_db_format=None):
    need(role in ("api", "worker", "beat"), "start_role")
    if role == "beat":
        need(beat_db_format in DBM_LAYOUTS, "schedule_format_unreviewed")
        config = load("start", commit, role, beat_db_format)
    else:
        need(beat_db_format is None, "unexpected_schedule_format")
        config = load("start", commit, role)
    asyncio.run(preflight(config))
    if role == "beat":
        dispatch(role, beat_db_format)
    else:
        dispatch(role)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("api", "worker", "beat"))
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--beat-db-format", choices=tuple(DBM_LAYOUTS))
    args = parser.parse_args()
    try:
        run(args.role, args.expected_commit, args.beat_db_format)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "RELEASE_START_STOP",
                    "code": str(exc) if isinstance(exc, GuardError) else type(exc).__name__,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
