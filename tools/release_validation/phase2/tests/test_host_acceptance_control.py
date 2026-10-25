"""Synthetic row/host-boundary contracts; no production or Docker execution."""

import hashlib
import time
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import host_acceptance_control as control
from contracts import AFTER, BEFORE, Rejected, canonical, digest

from tests.test_host_backup_control import Directory
from tests.test_migration_process import NoSocketAsyncCase

IDS = ["00000000-0000-0000-0000-" + str(i).zfill(12) for i in range(1, 10)]
BASELINE = {role: str(i + 1) * 64 for i, role in enumerate(control.ROLES)}


def base_rows(after=False):
    rows = {str(i): [] for i in range(7)}
    common = {
        "tenant_id": IDS[8],
        "publish_status": "已发布",
        "settlement_status": "已结款",
        "recall_status": None,
        "retro_status": "已完成",
        "metrics_recorded_at": "2020-01-01T00:00:00+00:00",
    }
    rows["0"] = [
        dict(common, id=IDS[0]),
        dict(common, id=IDS[1], publish_status="已取消", recall_status="召回成功"),
    ]
    if not after:
        rows["0"].append(dict(common, id=IDS[2], retro_status="待确认"))
    rows["1"] = [{"id": IDS[3], "tenant_id": IDS[8], "confirmed_at": None}]
    rows["2"] = [{"version_num": AFTER if after else BEFORE}]
    rows.update({name: [] for name in control.EXTRA})
    rows["promotions"] = [{"id": identity, "stable": "a" * 64} for identity in IDS[:3]]
    rows["role_access"] = [
        {"code": role, "scope": "*" if role == "admin" else "promotion.*:*"}
        for role in ("admin", "pr", "pr_manager", "finance", "operations")
    ]
    if after:
        rows["role_access"].append(
            {"code": "pr_manager", "scope": "promotion_retro:write"}
        )
    rows["finance_promotions"] = [
        {
            "id": IDS[1],
            "tenant_id": IDS[8],
            "publish_status": "未发布",
            "settlement_status": "未开始",
            "recall_status": "召回成功",
            "return_shipping_payer": "博主垫付",
            "return_shipping_fee": "30.00",
            "quote_amount": "0.00",
            "cost_snapshot": None,
            "total_promo_cost": "30.00",
        }
    ]
    rows["freight_index"] = [
        {
            "indexdef": "CREATE UNIQUE INDEX uq_return_freight_promotion ON public.return_freight_payment USING btree (tenant_id, promotion_id)"
        }
    ]
    rows["tenants"] = [{"id": IDS[8]}]
    rows["tenant_data"] = [{"tenant_id": IDS[8], "count": 3}]
    return rows


def migration_rows():
    return {
        "0": [{"version_num": AFTER}],
        "1": [{"id": i, "completed_at": "2026-10-24T01:00:01+00:00"} for i in IDS[:2]],
        "2": [{"id": IDS[2], "retro_status": "待复盘"}],
        "3": [{"id": IDS[3], "confirmed_at": None}],
        "4": [
            {
                "conname": "ck_promotion_ck_promotion_retro_status",
                "pg_get_constraintdef": next(iter(control.CONSTRAINTS)),
            }
        ],
        "5": [{"indexname": "idx_promotion_completed", "indexdef": control.INDEX}],
        "6": [{"data_type": "timestamp with time zone"}],
        "7": [{"scope": "promotion_retro:write", "code": "pr_manager"}],
        "8": [],
    }


def window():
    now = time.monotonic_ns()
    return {
        "db_before_utc": "2026-10-24T01:00:00.000000Z",
        "db_after_utc": "2026-10-24T01:00:02.000000Z",
        "started_monotonic_ns": max(0, now - 10**9),
        "completed_monotonic_ns": now,
    }


def counts():
    return {
        "0": [{"rolname": "clothing_app", "rolsuper": False, "rolbypassrls": False}],
        "1": [{"version_num": AFTER}],
        "2": [{"old_status_count": 0}],
        "3": [{"published_unstarted_count": 0}],
    }


def containers():
    return {
        role: {
            "id": identity,
            "image": "sha256:" + "b" * 64,
            "networks": {"erp": "a" * 64},
            "restarts": 0,
            "pid": 0 if role in ("worker", "beat") else 1,
            "running": role not in ("worker", "beat"),
            "paused": False,
            "restarting": False,
            "oom": False,
            "restart_policy": "no",
        }
        for role, identity in BASELINE.items()
    }


def spec():
    root = Path(control.__file__).parent
    return SimpleNamespace(
        data={
            "release_id": "release-test",
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "config_sha256": "d" * 64,
        },
        _binding=canonical(
            {
                "baseline_sha256": digest(BASELINE),
                "boot_id": "boot-test",
                "approval_sha256": "c" * 64,
            }
        ),
        _tools=tuple((name, (root / name).read_bytes()) for name in control.SQL_PINS),
        package=SimpleNamespace(_members=(("frontend-dist/index.html", b"asset"),)),
    )


class Projections(unittest.TestCase):
    def setUp(self):
        self.before = {
            "rows": base_rows(),
            "projection": control._before_projection(base_rows()),
        }

    def after(self, rows=None, base=None, interval=None):
        return control._after_projection(
            self.before,
            base or base_rows(True),
            rows or migration_rows(),
            counts(),
            interval or window(),
            {"gate_closed": True},
        )

    def test_actual_row_projection_full_sets_and_host_time(self):
        projected = self.after()
        control.verify_acceptance(self.before["projection"], projected)
        self.assertEqual(projected["mapped_ids"], [IDS[2]])
        self.assertEqual(projected["all_completed_ids"], IDS[:2])

    def test_window_fixture_accepts_short_runner_uptime(self):
        for now in (0, 1, 10 * 10**9, 1000 * 10**9):
            with (
                self.subTest(now=now),
                patch.object(control.time, "monotonic_ns", return_value=now),
            ):
                projected = self.after()
                control.verify_acceptance(self.before["projection"], projected)

    def test_negative_reversed_and_future_monotonic_ticks_rejected(self):
        now = 10 * 10**9
        for start, finish in ((-1, now), (now, now - 1), (0, now + 1)):
            with (
                self.subTest(start=start, finish=finish),
                patch.object(control.time, "monotonic_ns", return_value=now),
            ):
                interval = window()
                interval.update(
                    started_monotonic_ns=start, completed_monotonic_ns=finish
                )
                with self.assertRaisesRegex(Rejected, "^acceptance_migration_window$"):
                    self.after(interval=interval)

    def test_same_count_changed_id_is_rejected(self):
        rows = migration_rows()
        rows["1"][0]["id"] = IDS[7]
        with self.assertRaises(Rejected):
            control.verify_acceptance(self.before["projection"], self.after(rows))

    def test_missing_mapping_and_changed_confirmation_rejected(self):
        for key, replacement in (
            ("2", []),
            ("3", [{"id": IDS[3], "confirmed_at": "2026-10-24T00:00:00Z"}]),
        ):
            rows = migration_rows()
            rows[key] = replacement
            with self.subTest(key=key), self.assertRaises(Rejected):
                self.after(rows)

    def test_completion_outside_real_db_window_is_rejected(self):
        rows = migration_rows()
        rows["1"][0]["completed_at"] = "2026-10-24T01:00:03+00:00"
        with self.assertRaises(Rejected):
            self.after(rows)

    def test_reversed_or_naive_window_rejected(self):
        for key, value in (
            ("db_after_utc", "2026-10-24T00:00:00.000000Z"),
            ("db_before_utc", "2026-10-24T01:00:00"),
            ("completed_monotonic_ns", -1),
        ):
            interval = window()
            interval[key] = value
            with self.subTest(key=key), self.assertRaises(Rejected):
                self.after(interval=interval)

    def test_acl_and_schema_drift_fail_original_validator(self):
        rows = migration_rows()
        rows["4"][0]["pg_get_constraintdef"] = "CHECK (true)"
        with self.assertRaises(Rejected):
            control.verify_acceptance(self.before["projection"], self.after(rows))
        base = base_rows(True)
        base["4"] = [{"owner": "unexpected"}]
        with self.assertRaises(Rejected):
            control.verify_acceptance(self.before["projection"], self.after(base=base))

    def test_unpublished_recall_cost_included_and_exact_decimal(self):
        data = base_rows()
        result = control._financial(data, data)
        self.assertEqual(result["total_promo_cost"], "30.00")
        self.assertEqual(result["unpublished_count"], 1)
        changed = deepcopy(data)
        changed["finance_promotions"][0]["return_shipping_fee"] = "31.00"
        with self.assertRaises(Rejected):
            control._financial(data, changed)

    def test_r33_tenant_amount_payer_unique_failures(self):
        for field in ("freight_violations", "freight_duplicates", "freight_index"):
            data = base_rows()
            data[field] = [{"bad": True}]
            with self.subTest(field=field), self.assertRaises(Rejected):
                control._financial(data, data)

    def test_financial_event_id_or_status_change_rejected(self):
        for field in ("events", "settlements", "extra_items", "freight"):
            data, changed = base_rows(), base_rows(True)
            changed[field] = [{"id": IDS[5]}]
            with self.subTest(field=field), self.assertRaises(Rejected):
                control._financial(data, changed)

    def test_real_roles_preserve_star_old_wildcard_not_new_scope(self):
        actual, expected = control._roles(base_rows(), base_rows(True))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            {
                "admin": True,
                "pr": False,
                "pr_manager": True,
                "finance": False,
                "operations": False,
            },
        )
        self.assertNotIn("fixture_reader", actual)

    def test_unexpected_scope_or_override_rejected(self):
        for field, value in (
            ("role_access", {"code": "pr", "scope": "promotion_retro:write"}),
            ("new_overrides", {"id": IDS[6]}),
            ("overrides", {"value": "changed"}),
        ):
            changed = base_rows(True)
            changed[field].append(value)
            with self.subTest(field=field), self.assertRaises(Rejected):
                control._roles(base_rows(), changed)

    def test_uuid_injection_and_unknown_baseline_row_rejected(self):
        with self.assertRaises(Rejected):
            control._array(["'); SELECT 1;--"])
        data = base_rows()
        data["0"][0]["unknown"] = 1
        with self.assertRaises(Rejected):
            control._before_projection(data)

    def test_fixed_sql_pins_and_do_not_execute_post_open_query(self):
        data = spec()
        self.assertEqual(len(control._statements(data, "migration_acceptance.sql")), 10)
        data._tools = (("baseline_queries.sql", b"SELECT true;"),)
        with self.assertRaises(Rejected):
            control._statements(data, "baseline_queries.sql")


class CollectorBoundaries(NoSocketAsyncCase):
    async def test_three_stage_receipts_real_shapes_and_pending_business_samples(self):
        data = spec()
        rows = [
            {
                "kind": "intent",
                "payload": {"name": "BACKUP", "action_id": "backup-action"},
            }
        ]
        journal = SimpleNamespace(directory=Directory(), read=lambda: deepcopy(rows))
        facts = {
            "FREEZE_VERIFY": {"mode": "host_observed"},
            "CLOSE_GATE": {"mode": "host_observed", "gate_closed": True},
            "STOP_API": {"mode": "host_observed"},
        }
        quiet = {"containers": containers(), "sessions": {"private": True}}
        clock = [10**12]
        sql_counts = counts()
        queries = control._statements(data, "acceptance_queries.sql")[:4]

        async def read(_db, query, _budget):
            return deepcopy(sql_counts[str(queries.index(query))])

        with (
            patch.object(control, "require_step"),
            patch.object(
                control, "recorded_facts", side_effect=lambda s, j, key: facts[key]
            ),
            patch.object(control, "_quiet", AsyncMock(return_value=quiet)),
            patch.object(
                control,
                "_base",
                AsyncMock(
                    side_effect=[
                        base_rows(),
                        base_rows(True),
                        base_rows(True),
                        base_rows(True),
                    ]
                ),
            ),
            patch.object(control, "_rows", side_effect=read),
            patch.object(
                control, "_migration_rows", AsyncMock(return_value=migration_rows())
            ),
            patch.object(control.time, "monotonic_ns", side_effect=lambda: clock[0]),
        ):
            captured = await control.capture_pre_migration(data, journal, BASELINE)
            facts["BACKUP"] = {"schema_before_sha256": captured["receipt_sha256"]}
            facts["MIGRATE"] = {
                "mode": "host_observed",
                "backend_absent": True,
                "post_head": AFTER,
                "time_window": {
                    **window(),
                    "started_monotonic_ns": 10**12 + 1,
                    "completed_monotonic_ns": 10**12 + 2,
                },
            }
            clock[0] = 2 * 10**12
            rows.append(
                {
                    "kind": "intent",
                    "payload": {"name": "VERIFY_SCHEMA", "action_id": "schema-action"},
                }
            )
            schema = await control.verify_schema(data, journal, BASELINE)
            facts["VERIFY_SCHEMA"] = schema
            static = {"index.html": hashlib.sha256(b"asset").hexdigest()}
            current = containers()
            for role, step, build, identity in (
                ("api", "REPLACE_API", "BUILD_BACKEND", "a" * 64),
                ("web", "REPLACE_WEB", "BUILD_FRONTEND", "b" * 64),
            ):
                current[role]["id"] = identity
                facts[step] = {
                    "mode": "host_observed",
                    "role": role,
                    "old_container_id": BASELINE[role],
                    "new_container": current[role],
                    "image_id": current[role]["image"],
                    "source_commit": data.data["source_commit"],
                }
                facts[build] = {
                    "mode": "host_observed",
                    "image": {"id": current[role]["image"]},
                    "source_commit": data.data["source_commit"],
                    "probe_sha256": digest(static),
                }

            def runtime(tick):
                return {
                    "monotonic_ns": tick,
                    "containers": current,
                    "ports": {
                        r: [80, 443] if r == "web" else [] for r in control.ROLES
                    },
                    "gate": {
                        "tls_verified": True,
                        "certificate_not_after": time.time() + 200000,
                    },
                }

            clock[0] = 3 * 10**12
            rows.append(
                {
                    "kind": "intent",
                    "payload": {
                        "name": "ACCEPT_INTERNAL",
                        "action_id": "accept-action",
                    },
                }
            )
            with (
                patch.object(
                    control,
                    "_capture",
                    AsyncMock(
                        return_value=canonical({"permission_cache_ttl_seconds": 300})
                    ),
                ) as ttl_reader,
                patch.object(
                    control,
                    "_runtime",
                    AsyncMock(
                        side_effect=[runtime(2800 * 10**9), runtime(2900 * 10**9)]
                    ),
                ),
                patch.object(
                    control,
                    "_app_rls",
                    AsyncMock(return_value={"two_real_tenants": False}),
                ),
                patch.object(control, "_web_files", AsyncMock(return_value=static)),
            ):
                accepted = await control.accept_internal(data, journal, BASELINE)
                ttl_reader.assert_awaited_once()
                self.assertEqual(ttl_reader.await_args.args[0][-4:], (
                    control.TTL_OBSERVER, data.data["source_commit"],
                    data.data["source_tree"], data.data["config_sha256"],
                ))
            self.assertEqual(accepted["head"], AFTER)
            self.assertEqual(
                accepted["pending_business_samples"], control.BUSINESS_PENDING
            )
            self.assertNotIn("rows", accepted)
            stored = control.parse(
                journal.directory.read("internal-acceptance.json", control.MAX_BYTES)
            )
            self.assertEqual(stored["projection"]["mode"], "host_observed")
            self.assertEqual(stored["projection"]["role_access"]["pr"], False)
            self.assertEqual(
                stored["validation"]["pending_business_samples"], ["K7-4", "R33"]
            )
            self.assertEqual(stored["financial"]["total_promo_cost"], "30.00")
            with self.assertRaises(FileExistsError):
                await control.accept_internal(data, journal, BASELINE)

    async def test_migration_fixed_bindings_exclude_post_open_and_reject_time_injection(
        self,
    ):
        before = {"projection": control._before_projection(base_rows())}
        with patch.object(control, "_rows", AsyncMock(return_value=[])) as reader:
            await control._migration_rows(
                spec(), BASELINE["db"], before, window(), control._budget()
            )
        self.assertEqual(reader.await_count, 9)
        queries = [c.args[1] for c in reader.await_args_list]
        self.assertTrue(
            all(
                ":pre_" not in q and ":migration_" not in q and ":opened_at" not in q
                for q in queries
            )
        )
        bad = window()
        bad["db_before_utc"] = "'; UPDATE promotion SET id=NULL;--"
        with self.assertRaises(Rejected):
            await control._migration_rows(
                spec(), BASELINE["db"], before, bad, control._budget()
            )

    async def test_require_step_missing_predecessor_and_wrong_receipt_fail_before_sql(
        self,
    ):
        journal = SimpleNamespace(directory=Directory(), read=list)
        with (
            patch.object(
                control, "require_step", side_effect=Rejected("missing_intent")
            ),
            patch.object(control, "_sql", AsyncMock()) as sql,
        ):
            with self.assertRaises(Rejected):
                await control.verify_schema(spec(), journal, BASELINE)
            sql.assert_not_awaited()

    async def test_runtime_rejects_wrong_image_running_producer_and_bad_readiness(self):
        data, original = spec(), containers()
        replacements = {
            role: {"new_container": deepcopy(original[role])} for role in ("api", "web")
        }
        before = {"quiet_after": {"containers": original}}
        gate = {"tls_verified": True, "public_status": {"/": 503}}
        good_ready = {
            "/health": {"status": "ok"},
            "/ready": {"status": "ok", "checks": {"db": "ok", "redis": "ok"}},
        }
        for bad in ("image", "worker", "ready"):
            current = deepcopy(original)
            if bad == "image":
                current["api"]["image"] = "sha256:" + "f" * 64
            if bad == "worker":
                current["worker"]["running"] = True
            by_id = {r["id"]: r for r in current.values()}
            with (
                self.subTest(bad=bad),
                patch.object(
                    control,
                    "_container",
                    AsyncMock(
                        side_effect=lambda identity, indexed=by_id: indexed[identity]
                    ),
                ),
                patch.object(
                    control,
                    "_ports",
                    AsyncMock(
                        side_effect=lambda identity: (
                            [80, 443] if identity == BASELINE["web"] else []
                        )
                    ),
                ),
                patch(
                    "host_app_control.internal_api",
                    AsyncMock(return_value={} if bad == "ready" else good_ready),
                ),
                patch.object(
                    control.asyncio, "to_thread", AsyncMock(return_value=gate)
                ),
                self.assertRaises(Rejected),
            ):
                await control._runtime(data, BASELINE, replacements, before)

    async def test_schema_quiet_requires_absent_sessions_not_idle_pool(self):
        data, rows = spec(), containers()
        rows["api"].update(running=False, pid=0)
        by_id = {r["id"]: r for r in rows.values()}
        with (
            patch.object(
                control,
                "_container",
                AsyncMock(side_effect=lambda identity: by_id[identity]),
            ),
            patch(
                "host_session_contract.observe_sessions", AsyncMock(return_value={})
            ) as observe,
            patch.object(
                control.asyncio,
                "to_thread",
                AsyncMock(
                    return_value={"tls_verified": True, "public_status": {"/": 503}}
                ),
            ),
        ):
            await control._quiet(data, SimpleNamespace(), BASELINE, api_stopped=True)
        observe.assert_awaited_once_with(BASELINE["db"], require_absent=True)

    async def test_page_reader_is_readonly_bounded_and_complete(self):
        page = [{"id": i} for i in range(control.PAGE_ROWS)]
        with patch.object(
            control,
            "_sql",
            AsyncMock(side_effect=[canonical(page), canonical([{"id": 99}])]),
        ) as sql:
            result = await control._rows(
                BASELINE["db"],
                "SELECT id FROM promotion ORDER BY id",
                control._budget(),
            )
        self.assertEqual(len(result), control.PAGE_ROWS + 1)
        calls = [call.args[1] for call in sql.await_args_list]
        self.assertTrue(
            all(
                call.startswith(b"BEGIN READ ONLY;") and b"statement_timeout" in call
                for call in calls
            )
        )
        self.assertIn(b"OFFSET 16", calls[-1])

    async def test_truncated_invalid_shape_and_oversized_pages_reject(self):
        for raw in (
            b"[{",
            b"{}",
            b" " * 65537,
            canonical([{}] * (control.PAGE_ROWS + 1)),
        ):
            with (
                self.subTest(raw_length=len(raw)),
                patch.object(control, "_sql", AsyncMock(return_value=raw)),
                self.assertRaises((ValueError, Rejected)),
            ):
                await control._rows(
                    BASELINE["db"],
                    "SELECT id FROM promotion ORDER BY id",
                    control._budget(),
                )

    async def test_total_rows_bytes_and_deadline_reject(self):
        with (
            patch.object(control, "PAGE_ROWS", 1),
            patch.object(control, "MAX_ROWS", 1),
            patch.object(control, "_sql", AsyncMock(return_value=b"[{}]")),
            self.assertRaises(Rejected),
        ):
            await control._rows(
                BASELINE["db"],
                "SELECT id FROM promotion ORDER BY id",
                control._budget(),
            )
        with (
            patch.object(control, "MAX_BYTES", 1),
            patch.object(control, "_sql", AsyncMock(return_value=b"[]")),
            self.assertRaises(Rejected),
        ):
            await control._rows(
                BASELINE["db"],
                "SELECT id FROM promotion ORDER BY id",
                control._budget(),
            )
        with self.assertRaises(Rejected):
            await control._rows(
                BASELINE["db"],
                "SELECT id FROM promotion ORDER BY id",
                {"deadline": 0, "bytes": 0},
            )

    async def test_actual_socket_role_collector_single_real_tenant_explicit(self):
        with patch("host_backup_control._rls", AsyncMock(return_value="a" * 64)) as rls:
            result = await control._app_rls(BASELINE["db"], base_rows(True))
        self.assertFalse(result["two_real_tenants"])
        self.assertEqual(result["populated_tenant_count"], 1)
        rls.assert_awaited_once_with(BASELINE["db"], [IDS[8]])

    async def test_missing_real_tenant_data_or_existing_zero_tenant_rejected(self):
        for rows in (
            {"tenants": [{"id": IDS[8]}], "tenant_data": []},
            {
                "tenants": [{"id": "00000000-0000-0000-0000-000000000000"}],
                "tenant_data": [],
            },
        ):
            with self.assertRaises(Rejected):
                await control._app_rls(BASELINE["db"], rows)

    async def test_private_capture_and_exact_journal_binding(self):
        data = spec()
        intent = {
            "kind": "intent",
            "payload": {"name": "BACKUP", "action_id": "backup-action"},
        }
        journal = SimpleNamespace(directory=Directory(), read=lambda: [intent])
        facts = {
            "FREEZE_VERIFY": {"mode": "host_observed"},
            "CLOSE_GATE": {"mode": "host_observed", "gate_closed": True},
        }
        quiet = {"containers": containers(), "sessions": {"private": True}}
        with (
            patch.object(control, "require_step"),
            patch.object(
                control, "recorded_facts", side_effect=lambda s, j, key: facts[key]
            ),
            patch.object(control, "_quiet", AsyncMock(return_value=quiet)),
            patch.object(control, "_base", AsyncMock(return_value=base_rows())),
        ):
            result = await control.capture_pre_migration(data, journal, BASELINE)
            facts["BACKUP"] = {"schema_before_sha256": result["receipt_sha256"]}
            loaded = control._load_before(data, journal, BASELINE)
            self.assertEqual(loaded["projection"]["normal_ids"], [IDS[0]])
            self.assertNotIn("rows", result)
            self.assertIn("schema-before-collected.json", journal.directory.files)
            with self.assertRaises(FileExistsError):
                await control.capture_pre_migration(data, journal, BASELINE)
            facts["BACKUP"]["schema_before_sha256"] = "f" * 64
            with self.assertRaises(Rejected):
                control._load_before(data, journal, BASELINE)

    async def test_wrong_baseline_identity_rejected_before_io(self):
        journal = SimpleNamespace(directory=Directory(), read=list)
        changed = {**BASELINE, "db": "f" * 64}
        with patch.object(control, "require_step"), self.assertRaises(Rejected):
            control._begin(spec(), journal, changed, "BACKUP", "schema-before")
        self.assertEqual(journal.directory.files, {})

    async def test_public_port_and_asset_projections(self):
        with patch.object(
            control,
            "_capture",
            AsyncMock(
                return_value=canonical(
                    {
                        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
                        "443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "443"}],
                    }
                )
            ),
        ):
            self.assertEqual(await control._ports(BASELINE["web"]), [80, 443])
        with patch.object(
            control, "_capture", AsyncMock(return_value=b"a" * 64 + b"  ./index.html\n")
        ):
            self.assertEqual(
                await control._web_files(BASELINE["web"]), {"index.html": "a" * 64}
            )
        with (
            patch.object(
                control,
                "_capture",
                AsyncMock(
                    return_value=canonical(
                        {"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5432"}]}
                    )
                ),
            ),
            self.assertRaises(Rejected),
        ):
            await control._ports(BASELINE["db"])


if __name__ == "__main__":
    unittest.main()
