"""Explicit one-step migration only. Permit issuance belongs to the later updater."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from runtime_guard import AFTER, BEFORE, GuardError, connect, load, need, verify_connection

LOCK_KEY = 1007067066
CHILD_TIMEOUT = 180
TERMINATE_GRACE = 5


def verify_migration_tree():
    os.chdir("/app")
    sys.path.insert(0, "/app")
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    need(
        Path("/app/alembic/env.py").is_file() and Path("/app/alembic.ini").is_file(),
        "migration_files_missing",
    )
    scripts = ScriptDirectory.from_config(Config("/app/alembic.ini"))
    need(
        scripts.get_heads() == [AFTER] and scripts.get_revision(AFTER).down_revision == BEFORE,
        "migration_tree",
    )


async def cleanup_owned_child(conn, locked, spawn_task):
    """Do not release the connection until the owned child is confirmed exited."""
    process = None
    if spawn_task is not None:
        try:
            process = await spawn_task
        except Exception:
            # The spawn call failed before returning a child; the original failure propagates.
            pass
    if process is not None:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                # Confirmation takes precedence over releasing the migration lock.
                await process.wait()
        else:
            await process.wait()
        need(process.returncode is not None, "migration_child_exit_unconfirmed")
    try:
        if locked:
            await conn.fetchval("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    finally:
        await conn.close()


async def cleanup_without_cancellation(conn, locked, spawn_task):
    cleanup = asyncio.create_task(cleanup_owned_child(conn, locked, spawn_task))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


async def apply(config):
    conn = await connect(config, "DATABASE_URL_BYPASS")
    locked = False
    spawn_task = None
    try:
        await verify_connection(conn, "clothing_bypass", True, BEFORE)
        locked = bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY))
        need(locked, "migration_busy")
        # Check again after taking the deployment advisory lock.
        await verify_connection(conn, "clothing_bypass", True, BEFORE)
        spawn_task = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                "-c",
                "/app/alembic.ini",
                "upgrade",
                AFTER,
                cwd="/app",
                env=dict(os.environ),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        # Even cancellation during spawn must leave cleanup with a retrievable child handle.
        process = await asyncio.shield(spawn_task)
        try:
            await asyncio.wait_for(process.wait(), timeout=CHILD_TIMEOUT)
        except asyncio.TimeoutError:
            raise GuardError("migration_timeout") from None
        need(process.returncode == 0, "migration_process_failed")
        await verify_connection(conn, "clothing_bypass", True, AFTER)
    finally:
        await cleanup_without_cancellation(conn, locked, spawn_task)


def run(commit):
    config = load("migrate", commit)
    verify_migration_tree()
    asyncio.run(apply(config))
    return {"status": "MIGRATION_067_VERIFIED", "source_commit": commit, "schema": AFTER}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.expected_commit)))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "MIGRATION_STOP",
                    "code": str(exc) if isinstance(exc, GuardError) else type(exc).__name__,
                }
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
