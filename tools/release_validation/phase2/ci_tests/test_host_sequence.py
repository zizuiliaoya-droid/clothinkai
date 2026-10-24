"""Synthetic memory and nonexecuting host boundary tests; no production access."""

import sys
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import STEPS, Rejected, digest
from host_sequence import SUPPORTED, HostCoordinator, HostDirectory, replay
from sql_policy import (
    HostMigrationSpec,
    LinuxCISpec,
    MigrationSpec,
    SqlRejected,
    canonical,
    validate_spec,
)
from state_machine import State, next_action


class HostSequenceTests(unittest.TestCase):
    def setUp(self):
        self.spec = SimpleNamespace(
            data={"release_id": "flow78-" + "a" * 12}, sha256="b" * 64
        )

    def test_exact_29_order_and_unknown_is_never_replayed(self):
        rows = []
        for step in STEPS:
            state = replay(self.spec, rows)
            action = next_action(self.spec, state)
            self.assertEqual(action.name, step)
            rows.append({"kind": "intent", "payload": asdict(action)})
            self.assertEqual(
                next_action(self.spec, replay(self.spec, rows)).classification,
                "HOLD_UNKNOWN",
            )
            rows.append(
                {
                    "kind": "result",
                    "payload": {
                        "action_id": action.action_id,
                        "input_sha256": self.spec.sha256,
                        "facts_sha256": digest({"test_only": True}),
                        "completed": True,
                    },
                }
            )
        state = replay(self.spec, rows)
        self.assertTrue(state.migration_intent)
        self.assertTrue(state.possible_writes)
        self.assertEqual(next_action(self.spec, state).classification, "COMPLETE")

    def test_forged_out_of_order_duplicate_or_unmatched_result_rejected(self):
        action = next_action(self.spec, State())
        intent = {"kind": "intent", "payload": asdict(action)}
        result = {
            "kind": "result",
            "payload": {
                "action_id": action.action_id,
                "input_sha256": self.spec.sha256,
                "facts_sha256": "c" * 64,
                "completed": True,
            },
        }
        for rows in (
            [result],
            [intent, intent],
            [intent, result, result],
            [{"kind": "intent", "payload": {**asdict(action), "name": "MIGRATE"}}],
        ):
            with self.assertRaises(Rejected):
                replay(self.spec, rows)
        held = replay(self.spec, [intent, {"kind": "hold", "payload": {}}])
        self.assertIsNotNone(held.pending)

    def test_host_is_not_linux_ci_or_synthetic_spec(self):
        self.assertFalse(issubclass(HostMigrationSpec, LinuxCISpec))
        for spec in (
            LinuxCISpec(b"{}"),
            MigrationSpec(b"{}"),
            HostMigrationSpec(b"{}"),
        ):
            with self.assertRaises((SqlRejected, Rejected)):
                HostCoordinator(spec, object(), {})
        with self.assertRaises(SqlRejected):
            validate_spec(HostMigrationSpec(b"{}"))

    def test_full_binding_changes_policy_identity(self):
        first = HostMigrationSpec(b"{}", (), canonical({"container_id": "a" * 64}))
        second = HostMigrationSpec(b"{}", (), canonical({"container_id": "b" * 64}))
        self.assertNotEqual(first.sha256, second.sha256)

    def test_host_directory_cannot_accept_a_path_or_branch(self):
        for commit in ("../production", "HEAD", "a" * 39):
            with self.assertRaises(Rejected):
                HostDirectory(commit)

    def test_pending_actions_are_not_implicitly_success(self):
        self.assertIn("MIGRATE", SUPPORTED)
        self.assertIn("PREPARE", SUPPORTED)
        self.assertNotIn("OPEN_GATE", SUPPORTED)


if __name__ == "__main__":
    unittest.main()
