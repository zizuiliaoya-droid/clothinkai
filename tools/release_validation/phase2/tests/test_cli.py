import unittest
import release_cli
from contracts import Rejected, validate_input
from tests.test_state_machine import input_data


class CliTests(unittest.TestCase):
    def test_plan_never_grants_completion_or_live_authority(self):
        result = release_cli.plan(validate_input(input_data()))
        self.assertFalse(result["live_enabled"])
        self.assertFalse(result["deployable"])
        self.assertFalse(any(result["completed"].values()))

    def test_unrecognized_actions_cannot_become_commands(self):
        with self.assertRaises(Rejected):
            release_cli.dispatch("live", {})

    def test_success_scenario_is_synthetic_and_does_not_run_sql_runtime(self):
        data = {
            "schema_version": 1,
            "mode": "synthetic",
            "release": input_data(),
            "fail_action": None,
            "fail_at": None,
        }
        result = release_cli.dispatch("simulate", data)
        self.assertEqual(result["status"], "SYNTHETIC_COMPLETE")
        self.assertFalse(result["sql_runtime_executed"])
        self.assertFalse(result["linux_execution_implemented"])

    def test_crash_scenario_retains_worker_write_boundary(self):
        data = {
            "schema_version": 1,
            "mode": "synthetic",
            "release": input_data(),
            "fail_action": "START_WORKER",
            "fail_at": "after_effect",
        }
        result = release_cli.dispatch("simulate", data)
        self.assertEqual(result["status"], "HOLD_UNKNOWN")
        self.assertEqual(result["gate"], "closed")
        self.assertTrue(result["possible_writes"])
        self.assertFalse(result["live_enabled"])
