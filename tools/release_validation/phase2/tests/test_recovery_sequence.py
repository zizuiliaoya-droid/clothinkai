from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from state_machine import State
from tests.test_recovery import facts
from pre_migration_recover import simulate


class RecoverySequenceTests(unittest.TestCase):
    def test_recovery_worker_failure_does_not_open_or_repeat(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            result = simulate(Path(tmp), State(), facts(), "START_UNIQUE_OLD_WORKER")
            self.assertEqual(result["status"], "HOLD_UNKNOWN")
            self.assertEqual(result["gate"], "closed")
            self.assertTrue(result["possible_writes"])
            again = simulate(Path(tmp), State(), facts())
            self.assertEqual(again["status"], "HOLD_UNKNOWN")
            self.assertEqual(again["actions"], [])

    def test_recovery_preserves_data_and_opens_only_after_acceptance(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            result = simulate(Path(tmp), State(), facts())
            self.assertEqual(result["status"], "SYNTHETIC_COMPLETE")
            self.assertTrue((Path(tmp) / "recovery-world.json").exists())
            self.assertEqual(result.get("database_identity"), "fixture-original-db")
            self.assertEqual(result["actions"][-1], "OPEN_OLD_AFTER_ACCEPTANCE")
            self.assertEqual(result["actions"].count("START_UNIQUE_OLD_WORKER"), 1)

    def test_migration_intent_prevents_any_recovery_side_effect(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            result = simulate(
                Path(tmp), replace(State(), migration_intent=True), facts()
            )
            self.assertEqual(result["status"], "HOLD_PRESERVE")
            self.assertEqual(list(Path(tmp).iterdir()), [])
