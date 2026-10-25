"""Finite private host observations; incomplete business collectors fail closed.

Only the three stage entry points are public. SQL is fixed here or comes from
hash-verified public tools. No credentials, customer IDs, URLs or SQL arguments
are accepted by the entry points. Local tests do not establish host execution.
"""

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from contracts import AFTER, BEFORE, TARGET, canonical, digest, exact, require, sha
from fixed_host_transport import DOCKER, ROLES, _capture
from host_backup_control import _sql
from host_migration_control import _container, recorded_facts
from host_release_input import require_step
from migration_adapter import verify_acceptance
from sql_policy import parse

TTL_OBSERVER = """import hashlib,json,os,sys
try:
 sys.path.insert(0,'/opt/release')
 from runtime_guard import CONFIG,RELEASE,need,parse,read_protected,validate_config,validate_release
 from pathlib import Path
 commit,tree,config_sha=sys.argv[1:]
 need(os.geteuid()==0 and not Path('/app/.env').exists(),'runtime_identity')
 release=validate_release(parse(RELEASE.read_bytes()),commit)
 need(release['source_tree']==tree,'release_tree')
 raw=read_protected(CONFIG)
 need(hashlib.sha256(raw).hexdigest()==config_sha,'config_binding')
 config=validate_config(parse(raw))
 os.environ.clear()
 os.environ.update({'PATH':'/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8','HOME':'/root',**config})
 os.chdir('/app');sys.path.insert(0,'/app')
 from app.core.config import settings
 ttl=settings.PERM_CACHE_TTL_SECONDS
 need(type(ttl) is int and 300<=ttl<=540,'cache_ttl')
 print(json.dumps({'permission_cache_ttl_seconds':ttl}))
except BaseException:
 raise SystemExit(2) from None
"""


SQL_PINS = {
    "baseline_queries.sql": "db648368f18ac1966533145cd69920a4bd205c842083a63acc0af20416f61403",
    "migration_acceptance.sql": "bd079c6e9aaa197e433f99b2702af4515198eda4eb4d5e3565f5560f816ec4c9",
    "acceptance_queries.sql": "b2baa2257f61c691d68ae9d084f3e267b390d11af3d20849bc532adb406f34e8",
}
MAX_ROWS = 20000
PAGE_ROWS = 16
MAX_BYTES = 16 * 1024**2
DEADLINE = 600


def parse_private_receipt(raw):
    """Private complete-set receipts have a 16 MiB limit, unlike SQL pages."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "acceptance_receipt_size")

    def pairs(values):
        require(len(values) == len(dict(values)), "acceptance_receipt_duplicate")
        return dict(values)

    def constant(value):
        require(False, "acceptance_receipt_constant")

    try:
        result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        require(False, "acceptance_receipt_json")
    require(type(result) is dict, "acceptance_receipt_object")
    return result


EXTRA = {
    # Preserve every promotion, including unpublished historical recall costs.
    # Only the two fields deliberately changed by 067 are removed.
    "promotions": "SELECT id,encode(sha256(convert_to((to_jsonb(p)-'retro_status'-'completed_at')::text,'UTF8')),'hex') AS stable FROM promotion p ORDER BY id",
    "scopes": "SELECT to_jsonb(p) AS value FROM permission p WHERE scope<>'promotion_retro:write' ORDER BY id",
    "role_scopes": "SELECT to_jsonb(rp) AS value FROM role_permission rp JOIN permission p ON p.id=rp.permission_id WHERE p.scope<>'promotion_retro:write' ORDER BY rp.role_id,rp.permission_id",
    "overrides": "SELECT to_jsonb(o) AS value FROM user_permission_override o JOIN permission p ON p.id=o.permission_id WHERE p.scope<>'promotion_retro:write' ORDER BY o.id",
    "new_overrides": "SELECT o.id FROM user_permission_override o JOIN permission p ON p.id=o.permission_id WHERE p.scope='promotion_retro:write' ORDER BY o.id",
    "role_access": "SELECT r.code,p.scope FROM role r LEFT JOIN role_permission rp ON rp.role_id=r.id LEFT JOIN permission p ON p.id=rp.permission_id ORDER BY r.code,p.scope",
    "tenants": "SELECT id FROM tenant ORDER BY id",
    "tenant_data": "SELECT tenant_id,count(*) AS count FROM promotion GROUP BY tenant_id ORDER BY tenant_id",
    "finance_promotions": "SELECT id,tenant_id,publish_status,settlement_status,recall_status,return_shipping_payer,return_shipping_fee::text,quote_amount::text,cost_snapshot::text,total_promo_cost::text FROM promotion ORDER BY id",
    "settlements": "SELECT id,tenant_id,promotion_id,settlement_status,amount::text,total_amount::text,payment_amount::text,payment_date,request_event_id FROM settlement ORDER BY id",
    "extra_items": "SELECT id,tenant_id,settlement_id,item_type,amount::text,created_at FROM settlement_extra_item ORDER BY id",
    "freight": "SELECT id,tenant_id,promotion_id,status,amount::text,payment_date,paid_at,submitted_at FROM return_freight_payment ORDER BY id",
    "events": "SELECT id,tenant_id,promotion_id,event_type,created_at,encode(sha256(convert_to(payload::text,'UTF8')),'hex') AS payload_sha256 FROM line_event ORDER BY id",
    "freight_violations": "SELECT f.id FROM return_freight_payment f LEFT JOIN promotion p ON p.id=f.promotion_id AND p.tenant_id=f.tenant_id WHERE p.id IS NULL OR f.amount IS DISTINCT FROM p.return_shipping_fee OR p.return_shipping_payer IS DISTINCT FROM '博主垫付' ORDER BY f.id",
    "freight_duplicates": "SELECT tenant_id,promotion_id,count(*) AS count FROM return_freight_payment GROUP BY tenant_id,promotion_id HAVING count(*)<>1 ORDER BY tenant_id,promotion_id",
    "freight_index": "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='return_freight_payment' AND indexname='uq_return_freight_promotion'",
}
CONSTRAINTS = {
    "CHECK (((retro_status)::text = ANY ((ARRAY['未开始'::character varying, '待复盘'::character varying, '已完成'::character varying])::text[])))",
}
INDEX = "CREATE INDEX idx_promotion_completed ON public.promotion USING btree (tenant_id, completed_at DESC) WHERE (completed_at IS NOT NULL)"
BUSINESS_PENDING = [
    "five_role_http_login_and_authorized_business_operations",
    "K7-4",
    "R33",
]


def _statements(spec, name):
    raw = dict(spec._tools)[name]
    require(hashlib.sha256(raw).hexdigest() == SQL_PINS[name], "acceptance_sql_pin")
    text = re.sub(r"--[^\r\n]*", "", raw.decode("utf-8"))
    queries = [s.strip() for s in text.split(";") if s.strip()]
    require(all(q.startswith("SELECT ") for q in queries), "acceptance_select_only")
    require(
        len(queries)
        == {
            "baseline_queries.sql": 7,
            "migration_acceptance.sql": 10,
            "acceptance_queries.sql": 5,
        }[name],
        "acceptance_sql_count",
    )
    return queries


def _binding(spec, baseline):
    exact(baseline, ROLES)
    for value in baseline.values():
        sha(value)
    host = parse(spec._binding)
    require(digest(baseline) == host["baseline_sha256"], "acceptance_baseline_pin")
    return {
        **{
            key: spec.data[key]
            for key in ("release_id", "source_commit", "source_tree")
        },
        "baseline_sha256": digest(baseline),
        "db_id": baseline["db"],
        "boot_id": host["boot_id"],
        "approval_sha256": host["approval_sha256"],
    }


def _begin(spec, journal, baseline, stage, filename):
    require_step(spec, journal, stage)
    binding = _binding(spec, baseline)
    rows = journal.read()
    intent = rows[-1]
    # O_EXCL before collection also prevents retrying a failed/partial read stage.
    marker = {
        "binding": binding,
        "action_id": intent["payload"]["action_id"],
        "journal_sha256": digest(rows),
    }
    journal.directory.create(filename + ".started.json", canonical(marker))
    return marker


async def _rows(database_id, query, budget):
    """Private fixed-query reader: every page is a bounded read-only transaction.

    Cross-page consistency is supplied by the verified writer freeze, checked
    before and after collection; pagination is never treated as a sample.
    """
    sha(database_id)
    require(
        query.lstrip().startswith("SELECT ") and ";" not in query,
        "acceptance_fixed_select",
    )
    rows = []
    while True:
        require(time.monotonic() < budget["deadline"], "acceptance_deadline")
        sql = (
            "BEGIN READ ONLY; SET LOCAL statement_timeout='15000ms'; SET LOCAL lock_timeout='3000ms'; SET LOCAL TIME ZONE 'UTC'; "
            "SELECT COALESCE(json_agg(row_to_json(q)), '[]'::json) FROM ("
            + query
            + " LIMIT "
            + str(PAGE_ROWS)
            + " OFFSET "
            + str(len(rows))
            + ") q; COMMIT;"
        )
        raw = await _sql(database_id, sql.encode("utf-8"), timeout=20)
        require(type(raw) is bytes and len(raw) <= 65536, "acceptance_page_bytes")
        budget["bytes"] += len(raw)
        require(budget["bytes"] <= MAX_BYTES, "acceptance_total_bytes")
        page = parse(raw)
        require(
            type(page) is list
            and len(page) <= PAGE_ROWS
            and all(type(r) is dict for r in page),
            "acceptance_page_shape",
        )
        rows.extend(page)
        require(len(rows) <= MAX_ROWS, "acceptance_row_limit")
        if len(page) < PAGE_ROWS:
            return rows


def _budget():
    return {"deadline": time.monotonic() + DEADLINE, "bytes": 0}


def _uuid(value):
    require(type(value) is str, "acceptance_uuid")
    try:
        require(str(UUID(value)) == value, "acceptance_uuid")
    except ValueError:
        require(False, "acceptance_uuid")
    return value


def _ids(rows):
    values = [_uuid(row["id"]) for row in rows]
    require(len(set(values)) == len(values), "acceptance_duplicate_id")
    return sorted(values)


def _array(values):
    return "'{" + ",".join(_uuid(v) for v in values) + "}'"


def _utc(value):
    require(
        type(value) is str
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z", value),
        "acceptance_database_utc",
    )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        require(False, "acceptance_database_utc")


def _timestamp(value):
    require(type(value) is str, "acceptance_timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(
            stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0,
            "acceptance_timestamp_zone",
        )
        return stamp
    except ValueError:
        require(False, "acceptance_timestamp")


def _head(rows):
    require(len(rows) == 1, "acceptance_head_count")
    exact(rows[0], ("version_num",))
    return rows[0]["version_num"]


async def _base(spec, database_id, budget):
    queries = _statements(spec, "baseline_queries.sql")
    result = {
        str(i): await _rows(database_id, query, budget)
        for i, query in enumerate(queries)
    }
    result.update(
        {name: await _rows(database_id, query, budget) for name, query in EXTRA.items()}
    )
    return result


def _before_projection(rows):
    keys = (
        "id",
        "tenant_id",
        "publish_status",
        "settlement_status",
        "recall_status",
        "retro_status",
        "metrics_recorded_at",
    )
    for row in rows["0"]:
        exact(row, keys)
        _uuid(row["tenant_id"])
    _ids(rows["0"])
    normal = [
        r
        for r in rows["0"]
        if r["publish_status"] == "已发布"
        and r["settlement_status"] == "已结款"
        and r["metrics_recorded_at"] is not None
        and r["retro_status"] == "已完成"
    ]
    recall = [
        r
        for r in rows["0"]
        if r["publish_status"] == "已取消" and r["recall_status"] == "召回成功"
    ]
    for row in rows["1"]:
        exact(row, ("id", "tenant_id", "confirmed_at"))
        _uuid(row["tenant_id"])
        require(row["confirmed_at"] is None, "acceptance_legacy_confirmed")
    require(rows["new_overrides"] == [], "acceptance_new_scope_override")
    _ids(rows["promotions"])
    return {
        "head": _head(rows["2"]),
        "normal_ids": _ids(normal),
        "recall_ids": _ids(recall),
        "pending_ids": _ids([r for r in rows["0"] if r["retro_status"] == "待确认"]),
        "published_unstarted_ids": _ids(
            [
                r
                for r in rows["0"]
                if r["publish_status"] == "已发布" and r["retro_status"] == "未开始"
            ]
        ),
        "legacy_unconfirmed_ids": _ids(rows["1"]),
        "old_scope_sha256": digest([rows["scopes"], rows["role_scopes"]]),
        "old_override_sha256": digest(rows["overrides"]),
        "roles_acl_sha256": digest([rows[str(i)] for i in range(3, 7)]),
    }


async def _quiet(spec, journal, baseline, *, api_stopped):
    from host_producer_control import verify_frozen
    from host_session_contract import observe_sessions, two_idle_windows

    rows = {role: await _container(identity) for role, identity in baseline.items()}
    for role, row in rows.items():
        require(
            row["id"] == baseline[role]
            and not any(row[k] for k in ("paused", "restarting", "oom")),
            "acceptance_container_state",
        )
        stopped = role in ("worker", "beat") or (role == "api" and api_stopped)
        require(row["running"] is (not stopped), "acceptance_container_running")
        if stopped:
            require(
                row["pid"] == 0 and row["restart_policy"] == "no",
                "acceptance_stopped_process",
            )
    if api_stopped:
        sessions = await observe_sessions(baseline["db"], require_absent=True)
    else:
        await verify_frozen(journal, baseline)
        sessions = await two_idle_windows(baseline["db"])
    from public_gate_observer import observe_gate

    gate = await asyncio.to_thread(observe_gate)
    require(
        gate["tls_verified"] is True
        and gate["public_status"]
        and all(v == 503 for v in gate["public_status"].values()),
        "acceptance_public_gate",
    )
    return {"containers": rows, "sessions": sessions, "gate": gate}


def _save(journal, filename, value, head, pending):
    raw = canonical(value)
    require(len(raw) <= MAX_BYTES, "acceptance_receipt_size")
    journal.directory.create(filename, raw)
    return {
        "mode": "host_observed",
        "head": head,
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "pending_business_samples": pending,
    }


async def capture_pre_migration(spec, journal, baseline):
    marker = _begin(spec, journal, baseline, "BACKUP", "schema-before")
    freeze = recorded_facts(spec, journal, "FREEZE_VERIFY")
    gate = recorded_facts(spec, journal, "CLOSE_GATE")
    require(
        gate["mode"] == "host_observed" and gate["gate_closed"] is True,
        "acceptance_gate_predecessor",
    )
    async with asyncio.timeout(DEADLINE):
        quiet_before = await _quiet(spec, journal, baseline, api_stopped=False)
        rows = await _base(spec, baseline["db"], _budget())
        journal.directory.create(
            "schema-before-collected.json", canonical({"marker": marker, "rows": rows})
        )
        projected = _before_projection(rows)
        require(projected["head"] == BEFORE, "acceptance_before_revision")
        _financial(rows, rows)
        quiet_after = await _quiet(spec, journal, baseline, api_stopped=False)
    value = {
        "mode": "host_observed",
        **marker,
        "freeze_sha256": digest(freeze),
        "gate_sha256": digest(gate),
        "captured_monotonic_ns": time.monotonic_ns(),
        "rows": rows,
        "projection": projected,
        "quiet_before": quiet_before,
        "quiet_after": quiet_after,
    }
    require_step(spec, journal, "BACKUP")
    result = _save(journal, "schema-before.json", value, BEFORE, BUSINESS_PENDING)
    journal.directory.create("schema-before-pin.json", canonical(result))
    return result


def _load_before(spec, journal, baseline):
    raw = journal.directory.read("schema-before.json", MAX_BYTES)
    pin = parse(journal.directory.read("schema-before-pin.json", 4096))
    require(
        hashlib.sha256(raw).hexdigest() == pin["receipt_sha256"],
        "acceptance_before_hash",
    )
    require(
        pin["receipt_sha256"]
        == recorded_facts(spec, journal, "BACKUP")["schema_before_sha256"],
        "acceptance_before_journal_hash",
    )
    before = parse_private_receipt(raw)
    require(
        before["mode"] == "host_observed"
        and before["binding"] == _binding(spec, baseline),
        "acceptance_before_binding",
    )
    require(
        before["freeze_sha256"]
        == digest(recorded_facts(spec, journal, "FREEZE_VERIFY")),
        "acceptance_freeze_binding",
    )
    # Bind to the precise durable BACKUP intent prefix, not just a private hash.
    rows = journal.read()
    intents = [
        i
        for i, row in enumerate(rows)
        if row["kind"] == "intent"
        and row["payload"]["action_id"] == before["action_id"]
    ]
    require(
        len(intents) == 1 and rows[intents[0]]["payload"]["name"] == "BACKUP",
        "acceptance_capture_intent",
    )
    require(
        digest(rows[: intents[0] + 1]) == before["journal_sha256"],
        "acceptance_capture_journal",
    )
    require(
        _before_projection(before["rows"]) == before["projection"],
        "acceptance_before_projection",
    )
    return before


def _financial(before, after):
    for key in (
        "finance_promotions",
        "settlements",
        "extra_items",
        "freight",
        "events",
    ):
        _ids(before[key])
        _ids(after[key])
        require(before[key] == after[key], "acceptance_financial_set_changed")
    require(
        after["freight_violations"] == [] and after["freight_duplicates"] == [],
        "acceptance_freight_relationship",
    )
    require(
        after["freight_index"]
        == [
            {
                "indexdef": "CREATE UNIQUE INDEX uq_return_freight_promotion ON public.return_freight_payment USING btree (tenant_id, promotion_id)"
            }
        ],
        "acceptance_freight_unique_index",
    )
    # Exact decimal strings prevent float rounding. Publication is deliberately
    # not a filter: R5 historical unpublished costs remain in this complete set.
    total = Decimal(0)
    for row in after["finance_promotions"]:
        try:
            values = [
                Decimal(row[k] if row[k] is not None else "0")
                for k in (
                    "quote_amount",
                    "cost_snapshot",
                    "return_shipping_fee",
                    "total_promo_cost",
                )
            ]
        except (InvalidOperation, TypeError):
            require(False, "acceptance_financial_decimal")
        require(
            all(v.is_finite() for v in values) and sum(values[:3]) == values[3],
            "acceptance_cost_formula",
        )
        total += values[3]
    return {
        "amount_delta_minor": 0,
        "promotion_count": len(after["finance_promotions"]),
        "unpublished_count": sum(
            r["publish_status"] != "已发布" for r in after["finance_promotions"]
        ),
        "total_promo_cost": str(total),
        "baseline_sha256": digest(
            {
                k: before[k]
                for k in (
                    "finance_promotions",
                    "settlements",
                    "extra_items",
                    "freight",
                    "events",
                )
            }
        ),
    }


def _roles(before, after):
    def grouped(rows):
        result = {}
        for row in rows:
            exact(row, ("code", "scope"))
            require(
                type(row["code"]) is str
                and (row["scope"] is None or type(row["scope"]) is str),
                "acceptance_role_shape",
            )
            scopes = result.setdefault(row["code"], set())
            if row["scope"] is not None:
                require(row["scope"] not in scopes, "acceptance_role_duplicate")
                scopes.add(row["scope"])
        return result

    old, current = grouped(before["role_access"]), grouped(after["role_access"])
    require(
        {"admin", "pr", "pr_manager", "finance", "operations"} <= old.keys(),
        "acceptance_role_matrix_missing",
    )
    require(
        all("promotion_retro:write" not in v for v in old.values()),
        "acceptance_scope_preexisting",
    )
    expected = {code: set(scopes) for code, scopes in old.items()}
    expected["pr_manager"].add("promotion_retro:write")
    require(
        current == expected
        and after["new_overrides"] == []
        and before["overrides"] == after["overrides"],
        "acceptance_role_delta",
    )

    def allowed(scopes):
        return bool(
            scopes
            & {
                "*",
                "promotion_retro:write",
                "promotion_retro.*:*",
                "promotion_retro.*:write",
            }
        )

    # The source permission algorithm treats promotion.*:* as a different
    # prefix. Preserve genuine '*' grants; never claim these are HTTP probes.
    observed = {code: allowed(scopes) for code, scopes in current.items()}
    wanted = {code: allowed(scopes) for code, scopes in expected.items()}
    return observed, wanted


def _after_projection(before, base, rows, counts, window, gate):
    start, finish = _utc(window["db_before_utc"]), _utc(window["db_after_utc"])
    ticks = [window[k] for k in ("started_monotonic_ns", "completed_monotonic_ns")]
    require(
        all(type(v) is int for v in ticks)
        and 0 <= ticks[0] <= ticks[1] <= time.monotonic_ns()
        and start <= finish,
        "acceptance_migration_window",
    )
    current = _before_projection(base)
    require(
        base["promotions"] == before["rows"]["promotions"],
        "acceptance_stable_promotions",
    )
    require(
        current["legacy_unconfirmed_ids"]
        == before["projection"]["legacy_unconfirmed_ids"],
        "acceptance_all_legacy_ids",
    )
    _financial(before["rows"], base)
    expected_mapping = set(
        before["projection"]["pending_ids"]
        + before["projection"]["published_unstarted_ids"]
    )
    for row in rows["1"]:
        exact(row, ("id", "completed_at"))
        require(
            start <= _timestamp(row["completed_at"]) <= finish,
            "acceptance_completed_timestamp_window",
        )
    for row in rows["2"]:
        exact(row, ("id", "retro_status"))
        require(row["retro_status"] == "待复盘", "acceptance_mapping_status")
    require(set(_ids(rows["2"])) == expected_mapping, "acceptance_mapping_ids")
    for row in rows["3"]:
        exact(row, ("id", "confirmed_at"))
        require(row["confirmed_at"] is None, "acceptance_legacy_confirmation_changed")
    require(
        _ids(rows["3"]) == before["projection"]["legacy_unconfirmed_ids"],
        "acceptance_legacy_ids",
    )
    for i, key in ((2, "old_status_count"), (3, "published_unstarted_count")):
        require(len(counts[str(i)]) == 1, "acceptance_count_rows")
        exact(counts[str(i)][0], (key,))
        require(type(counts[str(i)][0][key]) is int, "acceptance_count_type")
    return {
        "head": _head(rows["0"]),
        **{
            k: current[k]
            for k in (
                "normal_ids",
                "recall_ids",
                "old_scope_sha256",
                "old_override_sha256",
                "roles_acl_sha256",
            )
        },
        "mapped_ids": _ids(rows["2"]),
        "all_completed_ids": _ids(rows["1"]),
        "legacy_unconfirmed_ids": _ids(rows["3"]),
        "new_scope_roles": [
            r["code"] for r in rows["7"] if r["scope"] == "promotion_retro:write"
        ],
        "column_timestamptz": rows["6"] == [{"data_type": "timestamp with time zone"}],
        "constraint_exact": len(rows["4"]) == 1
        and rows["4"][0].get("conname") == "ck_promotion_ck_promotion_retro_status"
        and rows["4"][0].get("pg_get_constraintdef") in CONSTRAINTS,
        "partial_index_exact": rows["5"]
        == [{"indexname": "idx_promotion_completed", "indexdef": INDEX}],
        "old_status_count": counts["2"][0]["old_status_count"],
        "published_unstarted_count": counts["3"][0]["published_unstarted_count"],
        "migration_event_ids": _ids(rows["8"]),
        "started_tick": ticks[0],
        "observed_tick": ticks[1],
        "gate_closed": gate["gate_closed"],
        "business_sessions": 0,
        "old_processes": 0,
        "unknown_fields": [],
    }


async def _migration_rows(spec, database_id, before, window, budget):
    _utc(window["db_before_utc"])
    _utc(window["db_after_utc"])
    substitutions = {
        ":pre_mapping_ids": _array(
            sorted(
                set(
                    before["projection"]["pending_ids"]
                    + before["projection"]["published_unstarted_ids"]
                )
            )
        ),
        ":pre_unconfirmed_ids": _array(before["projection"]["legacy_unconfirmed_ids"]),
        ":migration_started_at": "'" + window["db_before_utc"] + "'",
        ":migration_observed_at": "'" + window["db_after_utc"] + "'",
    }
    rows = {}
    # Statement 9 requires an actual OPEN_GATE time; never manufacture it here.
    for i, query in enumerate(_statements(spec, "migration_acceptance.sql")[:9]):
        for key, value in substitutions.items():
            query = query.replace(key, value)
        rows[str(i)] = await _rows(database_id, query, budget)
    return rows


async def verify_schema(spec, journal, baseline):
    marker = _begin(spec, journal, baseline, "VERIFY_SCHEMA", "schema-after")
    before = _load_before(spec, journal, baseline)
    migration = recorded_facts(spec, journal, "MIGRATE")
    recorded_facts(spec, journal, "STOP_API")
    gate = recorded_facts(spec, journal, "CLOSE_GATE")
    require(
        migration["mode"] == "host_observed"
        and migration["backend_absent"] is True
        and migration["post_head"] == AFTER,
        "acceptance_migration_predecessor",
    )
    window = migration["time_window"]
    exact(
        window,
        (
            "db_before_utc",
            "db_after_utc",
            "started_monotonic_ns",
            "completed_monotonic_ns",
        ),
    )
    _utc(window["db_before_utc"])
    _utc(window["db_after_utc"])
    require(
        before["captured_monotonic_ns"] <= window["started_monotonic_ns"],
        "acceptance_capture_order",
    )
    async with asyncio.timeout(DEADLINE):
        quiet_before = await _quiet(spec, journal, baseline, api_stopped=True)
        budget = _budget()
        base = await _base(spec, baseline["db"], budget)
        rows = await _migration_rows(spec, baseline["db"], before, window, budget)
        counts = {
            str(i): await _rows(baseline["db"], q, budget)
            for i, q in enumerate(_statements(spec, "acceptance_queries.sql")[:4])
        }
        quiet_after = await _quiet(spec, journal, baseline, api_stopped=True)
    journal.directory.create(
        "schema-after-collected.json",
        canonical(
            {
                "marker": marker,
                "base": base,
                "rows": rows,
                "counts": counts,
                "window": window,
            }
        ),
    )
    after = _after_projection(before, base, rows, counts, window, gate)
    # Pure validator has legacy synthetic output; use only its validation and
    # retain the independently collected host evidence as provenance.
    verify_acceptance(before["projection"], after)
    value = {
        "mode": "host_observed",
        **marker,
        "before_sha256": digest(before),
        "migration_sha256": digest(migration),
        "rows": rows,
        "base": base,
        "counts": counts,
        "projection": after,
        "quiet_before": quiet_before,
        "quiet_after": quiet_after,
    }
    require_step(spec, journal, "VERIFY_SCHEMA")
    return _save(journal, "schema-after.json", value, AFTER, BUSINESS_PENDING)


async def _web_files(identity):
    raw = await _capture(
        DOCKER
        + (
            "exec",
            "--user",
            "nginx",
            identity,
            "/bin/sh",
            "-c",
            "cd /usr/share/nginx/html && find . -type f -exec sha256sum '{}' ';'",
        ),
        timeout=30,
    )
    files = {}
    for line in raw.decode("ascii").splitlines():
        require(
            re.fullmatch(r"[0-9a-f]{64}  \./[A-Za-z0-9_./-]+", line),
            "acceptance_static_line",
        )
        hashed, name = line.split("  ./", 1)
        require(
            name not in files
            and all(p not in ("", ".", "..") for p in name.split("/")),
            "acceptance_static_duplicate",
        )
        files[name] = hashed
    require(files, "acceptance_static_empty")
    return files


async def _ports(identity):
    result = parse(
        await _capture(
            DOCKER
            + (
                "inspect",
                "--type",
                "container",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                identity,
            )
        )
    )
    require(type(result) is dict, "acceptance_ports_shape")
    ports = set()
    for port, values in result.items():
        if values is None:
            continue
        require(
            port in ("80/tcp", "443/tcp") and type(values) is list and values,
            "acceptance_public_port",
        )
        for value in values:
            exact(value, ("HostIp", "HostPort"))
            require(
                value["HostIp"] in ("0.0.0.0", "::") and value["HostPort"] == port[:-4],
                "acceptance_port_binding",
            )
            ports.add(int(value["HostPort"]))
    return sorted(ports)


async def _runtime(spec, baseline, replacements, before):
    from host_app_control import internal_api
    from public_gate_observer import observe_gate

    identities = {
        **baseline,
        **{role: row["new_container"]["id"] for role, row in replacements.items()},
    }
    rows, ports = {}, {}
    for role, identity in identities.items():
        row = await _container(identity)
        require(
            row["id"] == identity
            and not any(row[k] for k in ("paused", "restarting", "oom")),
            "acceptance_runtime_state",
        )
        require(
            row["running"] is (role not in ("worker", "beat")),
            "acceptance_runtime_running",
        )
        previous = (
            replacements[role]["new_container"]
            if role in replacements
            else before["quiet_after"]["containers"][role]
        )
        require(
            all(row[k] == previous[k] for k in ("image", "networks", "restarts")),
            "acceptance_runtime_identity",
        )
        if role in ("worker", "beat"):
            require(row["pid"] == 0, "acceptance_producer_running")
        if role in ("api", "web", "worker", "beat"):
            require(row["restart_policy"] == "no", "acceptance_runtime_restart")
        rows[role], ports[role] = row, await _ports(identity)
        require(
            ports[role] == ([80, 443] if role == "web" else []),
            "acceptance_public_ports",
        )
    ready = await internal_api(identities["api"])
    require(
        ready
        == {
            "/health": {"status": "ok"},
            "/ready": {"status": "ok", "checks": {"db": "ok", "redis": "ok"}},
        },
        "acceptance_readiness_shape",
    )
    gate = await asyncio.to_thread(observe_gate)
    require(
        gate["tls_verified"] is True
        and gate["public_status"]
        and all(v == 503 for v in gate["public_status"].values()),
        "acceptance_internal_gate",
    )
    return {
        "monotonic_ns": time.monotonic_ns(),
        "containers": rows,
        "ports": ports,
        "ready": ready,
        "gate": gate,
    }


async def _app_rls(database_id, base):
    from host_backup_control import _rls

    tenants = _ids(base["tenants"])
    zero = "00000000-0000-0000-0000-000000000000"
    require(1 <= len(tenants) <= 128 and zero not in tenants, "acceptance_rls_tenants")
    populated = []
    for row in base["tenant_data"]:
        exact(row, ("tenant_id", "count"))
        require(
            row["tenant_id"] in tenants
            and type(row["count"]) is int
            and row["count"] > 0,
            "acceptance_rls_populated_tenant",
        )
        populated.append(row["tenant_id"])
    require(populated, "acceptance_rls_missing_data")
    # _rls uses independent clothing_app socket connections, compares full row
    # digests with root's tenant-filtered digests, and checks its own role flags.
    hashed = await _rls(database_id, tenants)
    return {
        "scope": "existing_tenants_and_select_verified_absent_zero_tenant",
        "tenant_count": len(tenants),
        "populated_tenant_count": len(set(populated)),
        "two_real_tenants": len(set(populated)) >= 2,
        "rls_sha256": hashed,
        "tables": ["promotion", "promotion_platform_setting"],
    }


async def accept_internal(spec, journal, baseline):
    from acceptance import verify_host

    marker = _begin(spec, journal, baseline, "ACCEPT_INTERNAL", "internal-acceptance")
    before = _load_before(spec, journal, baseline)
    schema = recorded_facts(spec, journal, "VERIFY_SCHEMA")
    schema_raw = journal.directory.read("schema-after.json", MAX_BYTES)
    require(
        schema["mode"] == "host_observed"
        and schema["head"] == AFTER
        and hashlib.sha256(schema_raw).hexdigest() == schema["receipt_sha256"],
        "acceptance_schema_receipt",
    )
    schema_data = parse_private_receipt(schema_raw)
    migration = recorded_facts(spec, journal, "MIGRATE")
    require(
        schema_data["binding"] == marker["binding"]
        and schema_data["migration_sha256"] == digest(migration),
        "acceptance_schema_binding",
    )
    replacements = {}
    for role, step, build in (
        ("api", "REPLACE_API", "BUILD_BACKEND"),
        ("web", "REPLACE_WEB", "BUILD_FRONTEND"),
    ):
        row, built = (
            recorded_facts(spec, journal, step),
            recorded_facts(spec, journal, build),
        )
        require(
            row["mode"] == built["mode"] == "host_observed"
            and row["role"] == role
            and row["old_container_id"] == baseline[role],
            "acceptance_replacement_predecessor",
        )
        sha(row["new_container"]["id"])
        require(
            row["new_container"]["id"] != baseline[role]
            and row["image_id"] == built["image"]["id"] == row["new_container"]["image"]
            and row["source_commit"]
            == built["source_commit"]
            == spec.data["source_commit"],
            "acceptance_replacement_identity",
        )
        replacements[role] = row
    async with asyncio.timeout(DEADLINE):
        ttl = parse(
            await _capture(
                DOCKER
                + (
                    "exec",
                    "--user",
                    "0:0",
                    replacements["api"]["new_container"]["id"],
                    "python",
                    "-I",
                    "-B",
                    "-c",
                    TTL_OBSERVER,
                    spec.data["source_commit"],
                    spec.data["source_tree"],
                    spec.data["config_sha256"],
                )
            )
        )
        exact(ttl, ("permission_cache_ttl_seconds",))
        ttl = ttl["permission_cache_ttl_seconds"]
        require(type(ttl) is int and 300 <= ttl <= 540, "acceptance_cache_ttl")
        migration_tick = migration["time_window"]["completed_monotonic_ns"] // 10**9
        remaining = migration_tick + ttl - time.monotonic_ns() // 10**9
        require(remaining <= ttl, "acceptance_future_migration_tick")
        if remaining > 0:
            await asyncio.sleep(remaining)
        first = await _runtime(spec, baseline, replacements, before)
        budget = _budget()
        base = await _base(spec, baseline["db"], budget)
        require(base == schema_data["base"], "acceptance_post_schema_drift")
        schema_rows = await _migration_rows(
            spec, baseline["db"], before, migration["time_window"], budget
        )
        require(
            schema_rows == schema_data["rows"], "acceptance_post_schema_sets_changed"
        )
        counts = {
            str(i): await _rows(baseline["db"], q, budget)
            for i, q in enumerate(_statements(spec, "acceptance_queries.sql")[:4])
        }
        journal.directory.create(
            "internal-acceptance-collected.json",
            canonical(
                {"marker": marker, "base": base, "counts": counts, "runtime": first}
            ),
        )
        financial = _financial(before["rows"], base)
        actual_roles, expected_roles = _roles(before["rows"], base)
        require(
            _head(counts["1"]) == AFTER
            and counts["2"] == [{"old_status_count": 0}]
            and counts["3"] == [{"published_unstarted_count": 0}],
            "acceptance_internal_schema",
        )
        require(
            counts["0"]
            == [{"rolname": "clothing_app", "rolsuper": False, "rolbypassrls": False}],
            "acceptance_application_role",
        )
        rls = await _app_rls(baseline["db"], base)
        static = await _web_files(replacements["web"]["new_container"]["id"])
        built = recorded_facts(spec, journal, "BUILD_FRONTEND")
        require(
            digest(static) == built["probe_sha256"], "acceptance_frontend_build_bytes"
        )
        expected_static = {
            n.removeprefix("frontend-dist/"): hashlib.sha256(raw).hexdigest()
            for n, raw in spec.package._members
            if n.startswith("frontend-dist/")
        }
        require(
            expected_static
            and all(static.get(n) == hashed for n, hashed in expected_static.items()),
            "acceptance_frontend_package_bytes",
        )
        remaining = 31 - (time.monotonic_ns() - first["monotonic_ns"]) / 10**9
        if remaining > 0:
            await asyncio.sleep(remaining)
        second = await _runtime(spec, baseline, replacements, before)
        # A second complete read binds the entire interval, not just the first
        # SQL snapshot to a later container/timing observation.
        final = await _base(spec, baseline["db"], budget)
        require(final == base, "acceptance_internal_rows_changed")
        require(
            await _migration_rows(
                spec, baseline["db"], before, migration["time_window"], budget
            )
            == schema_rows,
            "acceptance_final_schema_sets_changed",
        )
    observed_tick = time.monotonic_ns() // 10**9
    expiry_remaining = int(second["gate"]["certificate_not_after"] - time.time())
    projected = {
        "mode": "host_observed",
        "head": AFTER,
        "commit": spec.data["source_commit"],
        "frontend_commit": replacements["web"]["source_commit"],
        "backend_image": replacements["api"]["image_id"],
        "frontend_sha256": digest(expected_static),
        "db_id": baseline["db"],
        "redis_id": baseline["redis"],
        "network_ids": {
            role: row["networks"] for role, row in second["containers"].items()
        },
        "public_ports": second["ports"]["web"],
        "ready": {"database": "ok", "redis": "ok", "status": "ready"},
        "origin": TARGET["origin"],
        "tls_valid": second["gate"]["tls_verified"],
        "tls_remaining_seconds": expiry_remaining,
        "app_superuser": counts["0"][0]["rolsuper"],
        "app_bypassrls": counts["0"][0]["rolbypassrls"],
        "app_rls_checked": True,
        "role_access": actual_roles,
        "cache_ttl_seconds": ttl,
        "migration_tick": migration_tick,
        "observed_tick": observed_tick,
        "stable_ticks": [r["monotonic_ns"] // 10**9 for r in (first, second)],
        "worker_count": int(second["containers"]["worker"]["running"]),
        "beat_count": int(second["containers"]["beat"]["running"]),
        "gate_closed": True,
        "k7_errors": counts["2"][0]["old_status_count"]
        + counts["3"][0]["published_unstarted_count"],
        "k8_amount_delta_minor": financial["amount_delta_minor"],
        # No OPEN_GATE yet: no legitimate T_open sample exists. This is a
        # pending sample count, never a claim of a successful business action.
        "new_retro_samples": 0,
        "r33_samples": 0,
        "unknown_fields": [],
    }
    expected = {
        **projected,
        "role_access": expected_roles,
        "network_ids": {
            role: (
                replacements[role]["new_container"]
                if role in replacements
                else before["quiet_after"]["containers"][role]
            )["networks"]
            for role in ROLES
        },
    }
    checked = verify_host(projected, expected)
    require_step(spec, journal, "ACCEPT_INTERNAL")
    value = {
        "mode": "host_observed",
        **marker,
        "projection": projected,
        "expected": expected,
        "validation": checked,
        "schema_sha256": schema["receipt_sha256"],
        "before_sha256": digest(before),
        "base": base,
        "counts": counts,
        "rls": rls,
        "financial": financial,
        "runtime_windows": [first, second],
        "static": static,
        "role_access_scope": "database_role_grants_and_unchanged_override_projection_only_not_http",
        "business_sample_boundary": "before_OPEN_GATE_no_user_login_or_business_write_performed",
        "pending_business_samples": BUSINESS_PENDING,
    }
    return _save(journal, "internal-acceptance.json", value, AFTER, BUSINESS_PENDING)
