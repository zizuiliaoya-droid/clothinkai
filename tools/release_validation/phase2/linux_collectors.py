"""Bounded read-only Linux observations. Raw SQL, tasks and credentials stay private."""

import hashlib
import re
import time

from sql_policy import DATABASE, LOCK_KEY, canonical, need


def pending(kind):
    need(
        kind
        in {
            "external_producers",
            "queues",
            "backup_r2",
            "restore_acl_rls",
            "role_ttl",
            "business_k7_k8",
            "tls_open",
            "instance_disk",
            "timers",
            "requests",
        },
        "collector_kind",
    )
    return {
        "kind": kind,
        "status": "pending",
        "verified": False,
        "live_verified": False,
    }


def backend_projection(row):
    need(type(row["pid"]) is int and row["pid"] > 0, "backend_pid")
    need(
        type(row["backend_start"]) is str
        and re.fullmatch(r"[0-9]{9,12}(?:\.[0-9]{1,6})?", row["backend_start"]),
        "backend_start",
    )
    need(
        type(row["application_name"]) is str
        and re.fullmatch("flow78-[0-9a-f]{32}", row["application_name"]),
        "backend_application",
    )
    return {k: row[k] for k in ("pid", "backend_start", "application_name")}


async def ci_connection(spec):
    from linux_transport import verify_ci_network
    from sql_policy import LinuxCISpec, validate_spec

    need(type(spec) is LinuxCISpec, "ci_observer_spec")
    validate_spec(spec)
    verify_ci_network()
    import asyncpg

    # Literal loopback prevents a DNS change between validation and connection.
    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        database=DATABASE,
        user="clothing_bypass",
        password="fixture-password",
        timeout=10,
        command_timeout=15,
        server_settings={
            "default_transaction_read_only": "on",
            "application_name": "flow78-ci-observer",
        },
    )
    try:
        need(
            await connection.fetchval("SELECT nonce FROM public.flow78_disposable_ci")
            == spec.data["nonce"],
            "ci_observer_marker",
        )
        need(
            await connection.fetchval("SELECT current_setting('server_version_num')")
            == "180006",
            "ci_pg_version",
        )
    except BaseException:
        await connection.close()
        raise
    return connection


async def inspect_ci_backend(identity, spec):
    from sql_migration_driver import validate_identity

    validate_identity(identity, spec)
    conn = await ci_connection(spec)
    try:
        exists = await conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
            WHERE pid=$1 AND extract(epoch from backend_start)::text=$2
            AND application_name=$3)""",
            identity["pid"],
            identity["backend_start"],
            identity["application_name"],
        )
        # A recycled PID carrying ANY lock is conservative HOLD, not proof of absence.
        lock = await conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM pg_locks WHERE
            locktype='advisory' AND pid=$1 AND classid=0 AND objid=$2 AND objsubid=1)""",
            identity["pid"],
            LOCK_KEY,
        )
        return {
            "policy_sha256": spec.sha256,
            "identity": identity,
            "state": "present" if exists else "absent",
            "after_process_exit": True,
            "owned_lock_absent": not lock,
        }
    finally:
        await conn.close()


async def schema_head(spec):
    conn = await ci_connection(spec)
    try:
        values = [
            r["version_num"]
            for r in await conn.fetch("SELECT version_num FROM public.alembic_version")
        ]
        need(len(values) == 1, "schema_head_count")
        return values[0]
    finally:
        await conn.close()


def hash_stream(stream, expected_bytes, expected_sha256):
    """Full bounded readback, usable for a local dump or already-open R2 GET body.

    Opening/authenticating an R2 object is deliberately not inferred from a URL.
    """
    need(
        type(expected_bytes) is int and 0 < expected_bytes <= 64 * 1024**3,
        "readback_size",
    )
    need(
        type(expected_sha256) is str and re.fullmatch("[0-9a-f]{64}", expected_sha256),
        "readback_hash",
    )
    count, digest = 0, hashlib.sha256()
    while count <= expected_bytes:
        chunk = stream.read(min(1024**2, expected_bytes - count + 1))
        need(type(chunk) is bytes, "readback_chunk")
        if not chunk:
            break
        count += len(chunk)
        digest.update(chunk)
    need(
        count == expected_bytes and digest.hexdigest() == expected_sha256,
        "readback_mismatch",
    )
    return {"bytes": count, "sha256": digest.hexdigest(), "full_readback": True}


def private_set_digest(values):
    """Only private callers retain IDs. Public projection contains count and digest."""
    need(
        type(values) is list
        and all(type(x) is str for x in values)
        and len(values) == len(set(values)),
        "private_set",
    )
    return {
        "count": len(values),
        "sha256": hashlib.sha256(canonical(sorted(values))).hexdigest(),
    }


def observed_interval(started, ended, status_codes):
    need(
        type(started) in (int, float)
        and type(ended) in (int, float)
        and 0 <= started <= ended <= time.monotonic(),
        "observed_interval",
    )
    need(
        status_codes and all(type(x) is int and 100 <= x <= 599 for x in status_codes),
        "http_statuses",
    )
    return {
        "started_monotonic": started,
        "ended_monotonic": ended,
        "status_codes": list(status_codes),
        "observed": True,
    }
