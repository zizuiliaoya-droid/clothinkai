"""Stage-specific session counts: paused API pools versus complete process exit.

1024 root decision: idle pooled connections may remain before STOP_API. Active,
idle-in-transaction (including aborted) and unknown states never count as drained.
After STOP_API all three roles must have zero connections, including idle.
"""

import asyncio
import time

from contracts import exact, require
from sql_policy import parse

SESSION_SQL = b"""BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_object_agg(name,counts) FROM (
 SELECT names.name,json_build_object(
  'idle',count(a.pid) FILTER (WHERE a.state='idle' AND a.backend_type='client backend'),
  'active',count(a.pid) FILTER (WHERE a.state='active' AND a.backend_type='client backend'),
  'idle_in_transaction',count(a.pid) FILTER (WHERE a.state IN ('idle in transaction','idle in transaction (aborted)') AND a.backend_type='client backend'),
  'unknown',count(a.pid) FILTER (WHERE a.state IS NULL OR a.state NOT IN ('idle','active','idle in transaction','idle in transaction (aborted)') OR a.backend_type<>'client backend'),
  'total',count(a.pid)) counts
 FROM (VALUES ('clothing_app'),('clothing_bypass'),('clothing_archiver')) names(name)
 LEFT JOIN pg_stat_activity a ON a.usename=names.name AND a.datname=current_database() AND a.pid<>pg_backend_pid()
 GROUP BY names.name) grouped;
ROLLBACK;
"""
ROLES = ("clothing_app", "clothing_bypass", "clothing_archiver")


def validate_sessions(row, *, require_absent=False):
    require(type(require_absent) is bool, "session_stage")
    exact(row, ROLES)
    for counts in row.values():
        exact(counts, ("idle", "active", "idle_in_transaction", "unknown", "total"))
        require(
            all(type(n) is int and 0 <= n <= 100000 for n in counts.values()),
            "session_count",
        )
        require(
            counts["total"]
            == sum(
                counts[k] for k in ("idle", "active", "idle_in_transaction", "unknown")
            ),
            "session_total",
        )
        require(
            counts["active"] == counts["idle_in_transaction"] == counts["unknown"] == 0,
            "session_not_quiescent",
        )
        if require_absent:
            require(counts["total"] == 0, "session_not_absent")
    return row


async def observe_sessions(database_id, *, require_absent=False):
    from host_backup_control import _sql

    return validate_sessions(
        parse(await _sql(database_id, SESSION_SQL, timeout=15)),
        require_absent=require_absent,
    )


async def two_idle_windows(database_id):
    first = {
        "monotonic_ns": time.monotonic_ns(),
        "roles": await observe_sessions(database_id),
    }
    await asyncio.sleep(30)
    second = {
        "monotonic_ns": time.monotonic_ns(),
        "roles": await observe_sessions(database_id),
    }
    require(
        second["monotonic_ns"] - first["monotonic_ns"] >= 30 * 10**9,
        "session_window_interval",
    )
    return {
        "stage": "business_quiescent_api_pool_may_be_idle",
        "windows": [first, second],
        "all_connections_absent": all(
            v["total"] == 0 for w in (first, second) for v in w["roles"].values()
        ),
    }
