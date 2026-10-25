from pathlib import Path
import tempfile
import unittest

from adapters import Runner, SyntheticAdapter
from contracts import Rejected, validate_input
from journal import Journal
from linux_adapter import live_factory
from tests.test_state_machine import input_data


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = validate_input(input_data())

    def runner(self, fail_at=None):
        journal = Journal(self.root)
        adapter = SyntheticAdapter(
            self.root,
            self.spec,
            {"PREPARE": {"prepared_members": 4, "hashes_verified": True}},
        )
        runner = Runner(
            self.spec, journal, adapter, validator=lambda *args: None, fail_at=fail_at
        )
        self.addCleanup(runner.close_after_confirmation)
        return runner

    def test_live_factory_has_no_activation_flag(self):
        with self.assertRaises(Rejected):
            live_factory({"enabled": True, "approval_ref": "pretend-approval"})

    def test_adapter_rejects_production_paths(self):
        with self.assertRaises(Rejected):
            SyntheticAdapter(Path("/srv/clothinkai"), self.spec, {})

    def test_write_after_durable_intent_and_before_result(self):
        runner = self.runner()
        runner.step()
        self.assertEqual(runner.adapter.events, ["PREPARE:effect", "PREPARE:observe"])
        self.assertEqual(
            [r["kind"] for r in runner.journal.read()], ["intent", "result"]
        )
        self.assertEqual(runner.state.completed, ("PREPARE",))

    def test_before_intent_failure_has_no_effect(self):
        runner = self.runner("before_intent")
        runner.step()
        self.assertEqual(runner.adapter.events, [])

    def test_after_effect_failure_cannot_repeat_on_current_or_reloaded_runner(self):
        runner = self.runner("after_effect")
        runner.step()
        runner.step()
        self.assertEqual(runner.adapter.events, ["PREPARE:effect"])
        other = self.runner()
        other.step()
        self.assertEqual(other.adapter.events, [])
        self.assertEqual(other.state.hold, "HOLD_UNKNOWN")

    def test_intent_fsync_failure_never_dispatches(self):
        runner = self.runner()
        runner.journal.fail_at = "file_fsync"
        runner.step()
        self.assertEqual(runner.adapter.events, [])

    def test_cancel_unconfirmed_owned_child_retains_lock(self):
        runner = self.runner("after_effect")
        runner.adapter.exit_confirmed = False
        runner.step()
        self.assertIsNotNone(runner.journal.lock)
        self.assertEqual(runner.state.hold, "HOLD_UNKNOWN")
        runner.adapter.exit_confirmed = True
        runner.close_after_confirmation()
        self.assertIsNone(runner.journal.lock)

    def test_atomic_artifact_interruption_keeps_original_absent_and_pending(self):
        journal = Journal(self.root, fail_at="before_replace")
        with self.assertRaises(Rejected):
            journal.atomic_artifact("nginx-active.conf", b"synthetic closed gate")
        self.assertFalse((self.root / "nginx-active.conf").exists())
        self.assertTrue((self.root / "nginx-active.conf.pending").exists())


if __name__ == "__main__":
    unittest.main()
