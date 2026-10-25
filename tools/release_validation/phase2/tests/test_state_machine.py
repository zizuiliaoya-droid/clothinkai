import copy
import unittest

import contracts
import state_machine as sm


def input_data():
    return {
        "schema_version": 1,
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "phase1_pins_sha256": "bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba",
        "release_id": "flow78-aaaaaaaaaaaa",
        "boot_id": "fixture-boot-001",
        "target": {
            "instance": "i-bp13rq87f4kzz3aalaf7",
            "disk_uuid": "7338e100-eb5b-489a-b457-1579c5200e9b",
            "origin": "https://47.110.76.9",
        },
        "from_revision": "066_platform_metric_setting",
        "target_revision": "067_retro_completion",
        "baseline_sha256": "c" * 64,
        "collector_sha256": "d" * 64,
        "policy": {
            "observation_seconds": 30,
            "drain_seconds": 600,
            "stop_seconds": 120,
            "backup_seconds": 2100,
            "restore_seconds": 1800,
            "build_seconds": 600,
            "migration_seconds": 210,
            "stable_seconds": 30,
            "ttl_seconds": 300,
            "sql_lock_ms": 10000,
            "sql_statement_ms": 150000,
        },
    }


class ContractTests(unittest.TestCase):
    def test_untrusted_live_or_command_is_rejected(self):
        for change in (
            {"mode": "live"},
            {"live_enabled": True},
            {"command": "docker ps"},
            {"target_revision": "head"},
            {"source_commit": "HEAD"},
        ):
            with self.subTest(change=change), self.assertRaises(contracts.Rejected):
                contracts.validate_input({**input_data(), **change})

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(contracts.Rejected):
            contracts.parse_json('{"mode":"synthetic","mode":"live"}')

    def test_valid_input_is_detached_from_original_mutation(self):
        data = input_data()
        result = contracts.validate_input(data)
        data["target"]["origin"] = "https://attacker.example"
        self.assertEqual(result.data["target"]["origin"], "https://47.110.76.9")

    def test_policy_and_baseline_are_required(self):
        for field in ("baseline_sha256", "policy", "phase1_pins_sha256"):
            data = copy.deepcopy(input_data())
            del data[field]
            with self.assertRaises(contracts.Rejected):
                contracts.validate_input(data)


class StateTests(unittest.TestCase):
    def test_stage_label_does_not_enable_migration(self):
        with self.assertRaises(contracts.Rejected):
            sm.State.from_dict({"phase": "BACKUP_VERIFIED"})

    def test_unknown_intent_prevents_replay(self):
        spec = contracts.validate_input(input_data())
        state = sm.State()
        action = sm.next_action(spec, state)
        self.assertEqual(action.name, "PREPARE")
        state = sm.intent(spec, state, action)
        self.assertEqual(sm.next_action(spec, state).classification, "HOLD_UNKNOWN")

    def test_new_worker_intent_closes_premigration_recovery(self):
        self.assertFalse(sm.recovery_eligible(sm.State(possible_writes=True)))
        self.assertFalse(sm.recovery_eligible(sm.State(migration_intent=True)))
        self.assertFalse(sm.recovery_eligible(sm.State(schema="067_retro_completion")))

    def test_cross_release_boot_and_fake_mode_receipt_rejected(self):
        spec = contracts.validate_input(input_data())
        action = sm.next_action(spec, sm.State())
        state = sm.intent(spec, sm.State(), action)
        for change in ({"release_id": "other"}, {"boot_id": "other"}, {"mode": "live"}):
            receipt = sm.fixture_receipt(
                spec, action, {"prepared_members": 4, "hashes_verified": True}
            )
            receipt.update(change)
            with self.assertRaises(contracts.Rejected):
                sm.result(spec, state, action, receipt, lambda *args: None)


if __name__ == "__main__":
    unittest.main()
