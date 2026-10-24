"""Opt-in isolated Linux CI entry. Never run locally against an existing database.

Requires a fresh PostgreSQL 18.6 service on loopback with the fixed fixture-only
admin password. Each scenario needs a new empty service (no DROP/reset commands).
"""

import argparse
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from linux_transport import (  # noqa: E402 - self-contained CI entry fixes module path
    LinuxCITransport,
    LinuxJournal,
    PrivateDirectory,
    ci_root,
    require_linux,
    verify_ci_network,
)
from phase1_package_binding import (  # noqa: E402
    frozen_contract,
    tool_bundle,
    verify_ci_source,
)
from sql_policy import (  # noqa: E402
    AFTER,
    BEFORE,
    DATABASE,
    LOCK_KEY,
    canonical,
    need,
    prepare_linux_ci,
    sha,
)


def stage(source, nonce):
    root = ci_root(nonce)
    root.mkdir(mode=0o700)
    directory = PrivateDirectory(nonce)
    bundle = tool_bundle()
    for prefix, files in (("app/", dict(source._members)), ("tools/", bundle)):
        for name, raw in files.items():
            parent = root / (prefix + name)
            parent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.create(prefix + name, raw)
    phase1 = frozen_contract().HERE
    artifacts = {
        "phase1_pins": (phase1 / "implementation-pins.json").read_bytes(),
        "phase1_guard": (phase1 / "runtime_guard.py").read_bytes(),
    }
    spec = prepare_linux_ci(
        source,
        nonce,
        artifacts,
        {n: {"bytes": len(b), "sha256": sha(b)} for n, b in bundle.items()},
    )
    directory.create(
        "policy.json",
        canonical(
            {
                "data": spec.data,
                "artifacts": {k: v.decode() for k, v in spec._artifacts},
            }
        ),
    )
    directory.close()
    return spec


async def bootstrap(nonce):
    import asyncpg

    verify_ci_network()
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        database="postgres",
        user="postgres",
        password="flow78-disposable-admin-only",
        timeout=10,
    )
    try:
        need(
            await conn.fetchval("SELECT current_setting('server_version_num')")
            == "180006",
            "ci_pg18_required",
        )
        need(
            not await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=$1)", DATABASE
            ),
            "ci_database_must_be_absent",
        )
        need(
            await conn.fetchval(
                "SELECT count(*) FROM pg_roles WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver')"
            )
            == 0,
            "ci_roles_must_be_absent",
        )
        await conn.execute(
            "CREATE ROLE clothing_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT PASSWORD 'fixture-password'"
        )
        await conn.execute(
            "CREATE ROLE clothing_bypass LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS NOINHERIT PASSWORD 'fixture-password'"
        )
        await conn.execute(
            "CREATE ROLE clothing_archiver LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT PASSWORD 'fixture-password'"
        )
        await conn.execute(
            "CREATE DATABASE clothing_erp_prod_20261018 OWNER clothing_bypass"
        )
    finally:
        await conn.close()
    conn = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        database=DATABASE,
        user="postgres",
        password="flow78-disposable-admin-only",
        timeout=10,
    )
    try:
        await conn.execute("CREATE EXTENSION pg_trgm")
        await conn.execute(
            "CREATE TABLE public.flow78_disposable_ci (nonce text NOT NULL)"
        )
        await conn.execute("INSERT INTO public.flow78_disposable_ci VALUES ($1)", nonce)
        await conn.execute(
            "GRANT SELECT ON public.flow78_disposable_ci TO clothing_bypass"
        )
    finally:
        await conn.close()


def filesystem_checks(nonce):
    directory = PrivateDirectory(nonce)
    directory.create("fsync-evidence.json", b"fixture-only")
    need(directory.read("fsync-evidence.json") == b"fixture-only", "fsync_readback")
    os.link(directory.root / "fsync-evidence.json", directory.root / "hardlink.json")
    try:
        directory.read("hardlink.json")
    except ValueError:
        pass
    else:
        raise AssertionError("hardlink accepted")
    os.symlink("fsync-evidence.json", directory.root / "symlink.json")
    try:
        directory.read("symlink.json")
    except OSError:
        pass
    else:
        raise AssertionError("symlink accepted")
    one, two = LinuxJournal(nonce), LinuxJournal(nonce)
    one.acquire()
    try:
        try:
            two.acquire()
        except BlockingIOError:
            pass
        else:
            raise AssertionError("parallel flock accepted")
    finally:
        one.release()
        one.directory.close()
        two.directory.close()
        directory.close()


async def exercise(spec, scenario):
    import asyncpg

    from linux_collectors import schema_head
    from migration_process import run_guarded_migration

    root = ci_root(spec.data["nonce"])
    seed = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        str(root / "tools/linux_ci_seed.py"),
        spec.data["nonce"],
        cwd=root / "app/backend",
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        need(await asyncio.wait_for(seed.wait(), 180) == 0, "original_066_seed_failed")
    except BaseException:
        if seed.returncode is None:
            seed.terminate()
            try:
                await asyncio.wait_for(seed.wait(), 5)
            except TimeoutError:
                seed.kill()
                await seed.wait()
        raise
    need(await schema_head(spec) == BEFORE, "seed_head")
    blocker = None
    if scenario in ("lock_timeout", "cancel", "advisory_busy"):
        blocker = await asyncpg.connect(
            host="127.0.0.1",
            port=5432,
            database=DATABASE,
            user="clothing_bypass",
            password="fixture-password",
        )
        if scenario == "advisory_busy":
            await blocker.fetchval("SELECT pg_advisory_lock($1)", LOCK_KEY)
        else:
            await blocker.execute("BEGIN")
            await blocker.execute(
                "LOCK TABLE public.promotion IN ACCESS EXCLUSIVE MODE"
            )
    journal = LinuxJournal(spec.data["nonce"])
    transport = LinuxCITransport(spec, journal)
    task = asyncio.create_task(run_guarded_migration(spec, transport))
    try:
        if scenario == "cancel":
            deadline = time.monotonic() + 30
            while not (root / "connected.json").exists():
                need(
                    time.monotonic() < deadline and not task.done(),
                    "connection_checkpoint_missing",
                )
                await asyncio.sleep(0.05)
            task.cancel()
        result = await task
    finally:
        if blocker:
            await blocker.close()
    head = await schema_head(spec)
    busy_diagnostic = None
    if scenario == "success":
        need(
            result["outcome"] == "verified"
            and result["host_lock_released"] is True
            and head == AFTER,
            "real_migration_failed",
        )
        need(
            result["proof"]["gucs"] == {"lock_ms": 10000, "statement_ms": 150000},
            "real_gucs",
        )
        need(result["proof"]["advisory"]["held_at_postcheck"] is True, "real_ddl_lock")
    else:
        need(result["action"] == "HOLD" and head == BEFORE, "failure_must_hold_before")
        if scenario == "cancel":
            need(
                result["cancelled"] is True and result["owned_exit"]["process"] is True,
                "cancel_not_exercised",
            )
        if scenario == "lock_timeout":
            need(
                result["failure"] is not None
                and result["failure"]["sqlstate"] == "55P03",
                "real_lock_timeout_sqlstate_required",
            )
        if scenario == "advisory_busy":
            from sql_policy import parse

            directory = PrivateDirectory(spec.data["nonce"])
            try:
                busy_diagnostic = assert_advisory_busy(
                    result,
                    head,
                    parse(directory.read("advisory-busy.json", 4096)),
                    spec,
                )
            finally:
                directory.close()
    # A second fresh coordinator must not issue a second child after any journal.
    again = await run_guarded_migration(
        spec, LinuxCITransport(spec, LinuxJournal(spec.data["nonce"]))
    )
    need(
        again["action"] == "HOLD" and again["owned_exit"]["process"] is False,
        "replay_executed",
    )
    return {
        "scenario": scenario,
        "head": head,
        "result": result,
        "replay": "HOLD",
        "advisory_busy_diagnostic": busy_diagnostic,
        "linux_executed": True,
        "package_semantics_verified": False,
        "live_verified": False,
    }


def assert_advisory_busy(result, head, diagnostic, spec):
    from sql_migration_driver import validate_busy_diagnostic

    diagnostic = validate_busy_diagnostic(diagnostic, spec)
    need(
        result["policy_sha256"] == spec.sha256
        and result["action"] == "HOLD"
        and head == BEFORE
        and result["outcome"] == "unknown"
        and result["host_lock_released"] is False
        and result["proof"] is None
        and result["post_head"] is None
        and result["owned_exit"]["process"] is True,
        "busy_must_preserve_unknown_and_host_lock",
    )
    return diagnostic


def main():
    require_linux()
    need(
        os.environ.get("GITHUB_ACTIONS") == "true", "disposable_github_runner_required"
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument(
        "--scenario",
        choices=("success", "lock_timeout", "cancel", "advisory_busy"),
        required=True,
    )
    args = parser.parse_args()
    source = verify_ci_source(Path(args.repository), args.commit, args.tree)
    nonce = secrets.token_hex(16)
    spec = stage(source, nonce)
    filesystem_checks(nonce)
    asyncio.run(bootstrap(nonce))
    result = asyncio.run(exercise(spec, args.scenario))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
