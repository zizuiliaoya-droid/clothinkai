"""Targeted negative contracts; fake DB results are not PostgreSQL evidence."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sql_migration_driver import SqlMigrationDriver, validate_busy_diagnostic
from sql_policy import SqlRejected
from tests.test_migration_process import NoSocketAsyncCase
from tests.test_sql_migration_driver import DIALECT, FakeRaw, fixture_spec, params


class AdvisoryBusyTests(NoSocketAsyncCase):
    async def test_only_actual_false_emits_bound_busy_diagnostic(self):
        spec = fixture_spec()
        rows = []
        driver = SqlMigrationDriver(spec, diagnostic=rows.append)
        driver.prepare_connect(DIALECT, params())
        raw = FakeRaw(spec)
        raw.busy = True
        with self.assertRaisesRegex(SqlRejected, "migration_busy"):
            await driver.verify_connection(raw)
        self.assertEqual(len(rows), 1)
        checked = validate_busy_diagnostic(rows[0], spec)
        self.assertEqual(checked["identity"]["pid"], raw.pid)
        self.assertEqual(checked["advisory_acquire"], "busy")
        self.assertIsNone(driver.proof["advisory"])
        self.assertEqual(driver.proof["statements"], 0)
        with self.assertRaises(SqlRejected):
            validate_busy_diagnostic({**rows[0], "policy_sha256": "0" * 64}, spec)
        from ci_tests.run_linux import assert_advisory_busy
        from sql_policy import BEFORE

        held = {
            "policy_sha256": spec.sha256,
            "action": "HOLD",
            "outcome": "unknown",
            "host_lock_released": False,
            "proof": None,
            "post_head": None,
            "owned_exit": {"process": True},
        }
        assert_advisory_busy(held, BEFORE, rows[0], spec)
        for diagnostic in ({}, {**rows[0], "advisory_acquire": "guard_rejected"}):
            with self.assertRaises(SqlRejected):
                assert_advisory_busy(held, BEFORE, diagnostic, spec)
        for change in (
            {"host_lock_released": True},
            {"outcome": "failed_known"},
            {"policy_sha256": "0" * 64},
        ):
            with self.assertRaises(SqlRejected):
                assert_advisory_busy({**held, **change}, BEFORE, rows[0], spec)

    async def test_other_guard_failure_cannot_claim_contention(self):
        spec = fixture_spec()
        rows = []
        driver = SqlMigrationDriver(spec, diagnostic=rows.append)
        driver.prepare_connect(DIALECT, params())
        raw = FakeRaw(spec)
        raw.roles["rolsuper"] = True
        raw.busy = True
        with self.assertRaises(Exception):
            await driver.verify_connection(raw)
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
