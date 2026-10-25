import json
import tempfile
from pathlib import Path
from journal import Journal

from contracts import Rejected, validate_input
from tests.test_migration_process import NoSocketAsyncCase, fixture_transport
from tests.test_sql_migration_driver import fixture_spec
from tests.test_migration_adapter import prerequisites
from tests.test_state_machine import input_data
import migration_process
import migration_adapter


class IntegrationTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        migration_process._HELD.clear()
        self.policy = fixture_spec()
        self.release = validate_input(
            {
                **input_data(),
                **{
                    k: self.policy.data[k]
                    for k in ("source_commit", "source_tree", "release_id")
                },
            }
        )
        self.pre = prerequisites()
        sha = json.loads(self.policy.artifact("backup_receipt"))["backup_sha256"]
        self.pre.update(backup_sha256=sha, restore_dump_sha256=sha)
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.journal = Journal(Path(self.temp.name))

    async def asyncTearDown(self):
        migration_process._HELD.clear()
        self.journal.release()
        self.temp.cleanup()

    async def test_driver_verified_is_only_migrated_and_requires_schema_acceptance(
        self,
    ):
        transport = await fixture_transport(self.policy)
        result = await migration_adapter.run_candidate(
            self.release, self.pre, self.policy, transport, self.journal
        )
        self.assertEqual(
            result.get("classification"), "MIGRATED_REQUIRES_SCHEMA_ACCEPTANCE"
        )
        self.assertEqual(result["policy_sha256"], self.policy.sha256)
        self.assertEqual(transport.calls.count("spawn"), 1)
        self.assertFalse(result["live_enabled"])
        again = await migration_adapter.run_candidate(
            self.release, self.pre, self.policy, transport, self.journal
        )
        self.assertEqual(again["classification"], "HOLD_PRESERVE")
        self.assertEqual(transport.calls.count("spawn"), 1)

    async def test_mismatched_backup_and_source_reject_before_dispatch(self):
        transport = await fixture_transport(self.policy)
        bad = {**self.pre, "backup_sha256": "a" * 64, "restore_dump_sha256": "a" * 64}
        with self.assertRaises(Rejected):
            await migration_adapter.run_candidate(
                self.release, bad, self.policy, transport, self.journal
            )
        with self.assertRaises(Rejected):
            await migration_adapter.run_candidate(
                validate_input(input_data()),
                self.pre,
                self.policy,
                transport,
                self.journal,
            )
        self.assertEqual(transport.calls, [])

    async def test_unknown_backend_never_returns_migrated_and_cannot_retry(self):
        transport = await fixture_transport(self.policy)
        transport.backend_state = "present"
        result = await migration_adapter.run_candidate(
            self.release, self.pre, self.policy, transport, self.journal
        )
        self.assertEqual(result.get("classification"), "HOLD_PRESERVE")
        again = await migration_adapter.run_candidate(
            self.release, self.pre, self.policy, transport, self.journal
        )
        self.assertEqual(again["classification"], "HOLD_PRESERVE")
        self.assertEqual(transport.calls.count("spawn"), 1)
