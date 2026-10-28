"""Targeted contracts for the current 067→069 release; no host/cloud calls."""

# ruff: noqa: E402 -- standalone runner binds sibling tools before importing them
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "phase1"))

from beat_health_candidate import verify_processes
from contracts import AFTER, BEFORE, Rejected, digest
from compose_candidate import generate, recovery_compose
from dashboard_contract import CURRENT_RELEASE, original_active
from host_acceptance_control import SQL_PINS, _roles, _statements
from host_preflight_control import mount_contract
import migration_069_acceptance as schema069
from migration_069_acceptance import ADDED, DAILY, REMOVED, projection, verify
from phase1_package_binding import frozen_contract, tool_bundle
from runtime_guard import validate_permit


class Release069Contracts(unittest.TestCase):
    def base(self):
        return json.loads(
            (ROOT / "templates/current-compose-067-1028.json").read_bytes()
        )

    def test_current_compose_keeps_infrastructure_and_replaces_each_guard_once(self):
        base = self.base()
        old = copy.deepcopy(base)
        commit = "a" * 40
        result = generate(
            base, commit, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "dbm.gnu"
        )
        self.assertEqual(base, old)
        for name in ("prod-db", "prod-redis"):
            self.assertEqual(result["services"][name], old["services"][name])
        for role in ("api", "worker", "beat", "web"):
            mounts = result["services"]["prod-" + role]["volumes"]
            destinations = [v.split(":")[1] for v in mounts]
            self.assertEqual(len(destinations), len(set(destinations)))
            self.assertFalse(any(v.startswith(CURRENT_RELEASE + "/") for v in mounts))
            if role != "web":
                self.assertEqual(destinations.count("/run/release-guard"), 1)
                self.assertEqual(destinations.count("/opt/update"), 1)
        self.assertIn(
            CURRENT_RELEASE + "/web-control:/opt/production:ro",
            recovery_compose(base)["services"]["prod-web"]["volumes"],
        )

    def test_preflight_accepts_current_067_mounts(self):
        for role in ("api", "worker", "beat", "web"):
            service = self.base()["services"]["prod-" + role]
            mounts = []
            for value in service["volumes"]:
                source, destination, *options = value.split(":")
                mode = options[0] if options else "rw"
                mounts.append(
                    {
                        "source": source,
                        "destination": destination,
                        "rw": mode == "rw",
                        "type": "bind",
                    }
                )
            row = {"mounts": mounts, "ports": {}}
            if role == "web":
                row["ports"] = {
                    str(p) + "/tcp": [{"HostPort": str(p), "HostIp": "0.0.0.0"}]
                    for p in (80, 443)
                }
            mount_contract(role, row)

    def test_beat_pid1_fix_remains_in_new_bundle(self):
        args = [
            "python",
            "/opt/release/start_067.py",
            "beat",
            "--expected-commit",
            "a" * 40,
            "--beat-db-format",
            "dbm.gnu",
        ]
        records = [{"pid": 1, "state": "S", "start_ticks": 123, "argv": args}]
        self.assertEqual(
            verify_processes(records, 1, "a" * 40, "dbm.gnu"),
            {"pid": 1, "start_ticks": 123},
        )
        with self.assertRaisesRegex(ValueError, "beat_unique_process"):
            verify_processes(records * 2, 1, "a" * 40, "dbm.gnu")

    def test_permission_equality_replaces_old_new_scope_cache_wait(self):
        before = {
            "role_access": [
                {
                    "code": code,
                    "scope": "*" if code == "admin" else "promotion_retro:write",
                }
                for code in ("admin", "pr", "pr_manager", "finance", "operations")
            ],
            "scopes": ["old-permission"],
            "role_scopes": ["old-grant"],
            "overrides": [{"effect": "revoke"}],
        }
        _roles(before, copy.deepcopy(before))
        after = copy.deepcopy(before)
        after["overrides"] = []
        with self.assertRaisesRegex(Rejected, "acceptance_role_delta"):
            _roles(before, after)

    def test_previous_root_compose_is_not_a_valid_current_template(self):
        old = json.loads((ROOT / "templates/original-compose.json").read_bytes())
        with self.assertRaisesRegex(Rejected, "current_release_mounts"):
            generate(
                old, "a" * 40, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "dbm.gnu"
            )

    def test_current_open_nginx_and_all_query_pins(self):
        original_active((ROOT / "templates/nginx-release-open.conf").read_bytes())
        spec = type(
            "Spec",
            (),
            {"_tools": tuple((name, (ROOT / name).read_bytes()) for name in SQL_PINS)},
        )()
        self.assertEqual([len(_statements(spec, n)) for n in SQL_PINS], [7, 9, 4])

    def test_pg18_not_null_uses_exact_column_contract(self):
        name = "migration_acceptance.sql"
        spec = type("Spec", (), {"_tools": ((name, (ROOT / name).read_bytes()),)})()
        self.assertIn("AND contype<>'n'", _statements(spec, name)[4])
        policy = [
            {
                "policyname": "tenant_isolation",
                "permissive": "PERMISSIVE",
                "roles": ["clothing_app"],
                "cmd": "ALL",
                "qual": "tenant_predicate",
                "with_check": "tenant_predicate",
            }
        ]
        rows = {
            "0": [{"version_num": AFTER}],
            "2": copy.deepcopy(schema069.CONTENT_COLUMN),
            "3": copy.deepcopy(schema069.PLATFORM_COLUMNS),
            "4": [{"definition": d} for d in sorted(schema069.PLATFORM_CONSTRAINTS)],
            "5": [{"indexdef": d} for d in sorted(schema069.PLATFORM_INDEXES)],
            "6": [schema069.PLATFORM_ACCESS],
            "7": policy,
            "8": [{"platform_post_count": 0}],
        }
        schema069.verify_schema_rows(rows, policy)
        # Excluding pg_constraint n must not accept a nullable tenant key.
        tenant_column = next(c for c in rows["3"] if c["column_name"] == "tenant_id")
        self.assertEqual(tenant_column["is_nullable"], "NO")
        tenant_column["is_nullable"] = "YES"
        with self.assertRaisesRegex(Rejected, "acceptance_platform_columns"):
            schema069.verify_schema_rows(rows, policy)
        tenant_column["is_nullable"] = "NO"
        rows["4"] = [
            r
            for r in rows["4"]
            if not r["definition"].startswith("FOREIGN KEY (tenant_id)")
        ]
        with self.assertRaisesRegex(Rejected, "acceptance_platform_constraints"):
            schema069.verify_schema_rows(rows, policy)

    def test_fresh_phase1_pins_and_actual_source_chain(self):
        builder = frozen_contract()
        self.assertEqual((builder.BEFORE, builder.AFTER), (BEFORE, AFTER))
        bundle = tool_bundle()
        self.assertIn("frozen_phase1/implementation-pins-069-1028.json", bundle)
        self.assertNotIn("frozen_phase1/implementation-pins.json", bundle)
        files = {}
        revisions = [
            "066_platform_metric_setting",
            BEFORE,
            "068_promotion_content_platform",
            AFTER,
        ]
        for index, revision in enumerate(revisions):
            parent = revisions[index - 1] if index else None
            files["backend/alembic/versions/" + revision + ".py"] = (
                f"revision={revision!r}\ndown_revision={parent!r}\n".encode()
            )
        self.assertTrue(builder.migrations(files).endswith(AFTER + ".py"))
        files["backend/alembic/versions/" + AFTER + ".py"] = (
            f"revision={AFTER!r}\ndown_revision={BEFORE!r}\n".encode()
        )
        with self.assertRaisesRegex(
            builder.Rejected, "migration_target_parent|multiple_migration_heads"
        ):
            builder.migrations(files)

    def test_start_permit_is_069_only(self):
        permit = {
            "schema_version": 1,
            "operation": "start",
            "source_commit": "a" * 40,
            "release_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "approval_ref": "reviewed-1028",
            "schema": AFTER,
            "allowed_roles": ["api"],
            "beat_db_format": None,
        }
        validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "api")
        permit["schema"] = BEFORE
        with self.assertRaisesRegex(ValueError, "start_permit_role"):
            validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "api")

    def rows(self, after=False):
        names = (
            set(DAILY) | {"promotion", "store_daily"} | ({ADDED} if after else REMOVED)
        )
        return {
            "0": [{"id": "p1", "tenant_id": "t1", "expected_category": "历史分类"}],
            "1": [{"id": "r1", "stable": "old-retro"}],
            "2": [{"version_num": AFTER if after else BEFORE}],
            "4": [
                {"relname": name, "owner": "clothing_bypass"} for name in sorted(names)
            ],
            "content_column": [],
            "overrides": [{"effect": "revoke"}],
            "shop_daily_summary": [{"row_count": 1, "stable": "old-daily"}],
        }

    def after(self, rows):
        return {
            **projection(rows),
            "schema_verified": True,
            "started_tick": 1,
            "observed_tick": 2,
            "gate_closed": True,
            "business_sessions": 0,
            "old_processes": 0,
        }

    def test_only_new_category_and_two_cache_relations_may_change(self):
        before, after = self.rows(), self.rows(True)
        self.assertEqual(verify(projection(before), self.after(after))["head"], AFTER)
        for field in ("1", "overrides", "shop_daily_summary"):
            bad = copy.deepcopy(after)
            bad[field] = []
            with self.subTest(field=field), self.assertRaisesRegex(
                Rejected, "stable_sha256"
            ):
                verify(projection(before), self.after(bad))
        candidate = self.after(after)
        candidate["categories_sha256"] = digest([])
        with self.assertRaisesRegex(Rejected, "categories_sha256"):
            verify(projection(before), candidate)

    def test_missing_daily_or_unexpected_relation_is_rejected(self):
        after = self.rows(True)
        after["4"] = [r for r in after["4"] if r["relname"] != "shop_daily_summary"]
        with self.assertRaisesRegex(Rejected, "daily_tables_missing"):
            projection(after)
        after = self.rows(True)
        after["4"].append({"relname": "unexpected", "owner": "clothing_bypass"})
        with self.assertRaisesRegex(Rejected, "stable_sha256|relation_delta"):
            verify(projection(self.rows()), self.after(after))


if __name__ == "__main__":
    unittest.main()
