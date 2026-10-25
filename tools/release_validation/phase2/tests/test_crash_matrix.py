from pathlib import Path
import tempfile
import unittest

from contracts import Rejected, STEPS, validate_input
from state_machine import State, next_action
from tests.test_state_machine import input_data
from synthetic_world import World
from synthetic_world import WorldAdapter
from adapters import Runner, SyntheticAdapter, reconcile_fixture
from journal import Journal
from contracts import AFTER, parse_json
from recovery_plan import propose
from tests.test_recovery import facts as recovery_facts


class WorldRedTests(unittest.TestCase):
    def make(self):
        temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.addCleanup(temp.cleanup)
        spec = validate_input(input_data())
        return World(Path(temp.name), spec), spec

    def test_migration_cannot_precede_fresh_backup_and_exited_writers(self):
        world, spec = self.make()
        action = next_action(spec, State(completed=STEPS[: STEPS.index("MIGRATE")]))
        with self.assertRaises(Rejected):
            world.apply(action)

    def test_actual_effect_is_persisted_not_only_an_enum_receipt(self):
        world, spec = self.make()
        world.apply(next_action(spec, State()))
        self.assertTrue((world.root / "world.json").exists())

    def test_malformed_claimed_ready_evidence_is_rejected(self):
        world, _ = self.make()
        with self.assertRaises(Rejected):
            world.validate("MIGRATE", {"ready": True}, State())


class StatefulMatrixTests(unittest.TestCase):
    def run_to(self, root, index):
        spec = validate_input(input_data())
        journal = Journal(root)
        adapter = WorldAdapter(root, spec)
        runner = Runner(spec, journal, adapter, adapter.world.validate)
        for _ in range(index):
            runner.step()
            self.assertIsNone(runner.state.hold)
        return runner

    def test_complete_sequence_preserves_infra_and_fresh_drained_backup(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            runner = self.run_to(Path(tmp), len(STEPS))
            self.addCleanup(runner.close_after_confirmation)
            world = runner.adapter.world
            backup = parse_json((world.root / "database.synthetic.dump").read_bytes())
            restored = parse_json((world.root / "restored.synthetic.json").read_bytes())
            self.assertEqual(backup["old_write_count"], 12)
            self.assertEqual(backup["new_write_count"], 0)
            self.assertEqual(backup, restored)
            self.assertEqual(world.data["database"]["new_write_count"], 1)
            self.assertEqual(world.data["schema"], AFTER)
            self.assertEqual(
                world.data["infrastructure"], world.original["infrastructure"]
            )
            self.assertEqual(world.data["scheduler"], world.original["scheduler"])
            self.assertEqual(world.data["business_pending"], ["K7-4", "R33"])
            self.assertFalse(world.data["future_timer_runs_verified"])
            self.assertEqual(world.data["gate"], "open-new")
            self.assertEqual(
                [r["kind"] for r in runner.journal.read()],
                [k for _ in STEPS for k in ("intent", "result")],
            )

    def test_every_edge_five_fault_points_never_replays_or_advances(self):
        points = (
            "before_intent",
            "after_intent",
            "after_effect",
            "before_result",
            "after_result",
        )
        for index, name in enumerate(STEPS):
            for point in points:
                with self.subTest(
                    action=name, point=point
                ), tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
                    runner = self.run_to(Path(tmp), index)
                    try:
                        runner.fail_at = point
                        runner.step()
                        self.assertEqual(runner.state.hold, "HOLD_UNKNOWN")
                        saved = list(runner.adapter.events)
                        runner.step()
                        self.assertEqual(runner.adapter.events, saved)
                        history = runner.adapter.world.data["history"]
                        self.assertEqual(
                            len(history),
                            index
                            + int(
                                point
                                in {"after_effect", "before_result", "after_result"}
                            ),
                        )
                        self.assertLessEqual(history.count("MIGRATE"), 1)
                        if "START_WORKER" in history:
                            self.assertTrue(
                                runner.adapter.world.data["possible_writes"]
                            )
                            self.assertEqual(
                                propose(runner.state, recovery_facts())["branch"],
                                "HOLD_PRESERVE",
                            )
                        other = Runner(
                            runner.spec,
                            Journal(Path(tmp)),
                            SyntheticAdapter(Path(tmp), runner.spec, {}),
                            lambda *args: None,
                        )
                        other.step()
                        self.assertEqual(other.adapter.events, [])
                        self.assertEqual(other.state.hold, "HOLD_UNKNOWN")
                        other.close_after_confirmation()
                        projection = reconcile_fixture(runner.journal, runner.spec)
                        self.assertIsNone(projection["next_write"])
                    finally:
                        runner.close_after_confirmation()

    def test_backup_tampering_prevents_restore_and_migration(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            runner = self.run_to(Path(tmp), STEPS.index("RESTORE"))
            try:
                (Path(tmp) / "database.synthetic.dump").write_bytes(
                    b"synthetic-corruption"
                )
                runner.step()
                self.assertEqual(runner.state.hold, "HOLD_UNKNOWN")
                self.assertNotIn("MIGRATE", runner.adapter.world.data["history"])
                self.assertTrue(runner.adapter.world.data["apps"]["api"]["running"])
            finally:
                runner.close_after_confirmation()

    def test_worker_effect_before_result_preserves_possible_writes(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            runner = self.run_to(Path(tmp), STEPS.index("START_WORKER"))
            try:
                runner.fail_at = "after_effect"
                runner.step()
                self.assertTrue(runner.state.possible_writes)
                self.assertEqual(
                    runner.adapter.world.data["database"]["new_write_count"], 1
                )
                self.assertEqual(runner.adapter.world.data["gate"], "closed")
                self.assertIsNotNone(runner.state.pending)
            finally:
                runner.close_after_confirmation()

    def test_cancel_does_not_release_until_owned_exit_confirmed(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            runner = self.run_to(Path(tmp), STEPS.index("MIGRATE"))
            try:
                runner.adapter.exit_confirmed = False
                runner.fail_at = "after_effect"
                runner.step()
                self.assertIsNotNone(runner.journal.lock)
                with self.assertRaises(Rejected):
                    runner.close_after_confirmation()
            finally:
                runner.adapter.exit_confirmed = True
                runner.close_after_confirmation()
