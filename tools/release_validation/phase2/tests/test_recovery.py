from copy import deepcopy
from dataclasses import replace
import unittest
from contracts import BEFORE, AFTER
from state_machine import State
import recovery_plan


def facts():
    return {
        "head": BEFORE,
        "full_schema_matches": True,
        "acl_matches": True,
        "baseline_matches": True,
        "old_images_present": True,
        "old_controls_match": True,
        "queue_known": True,
        "owned_exit_confirmed": True,
        "recovery_started": False,
    }


class RecoveryTests(unittest.TestCase):
    def test_new_worker_intent_cannot_be_overridden_by_zero_write_count(self):
        result = recovery_plan.propose(replace(State(), possible_writes=True), facts())
        self.assertEqual(result["branch"], "HOLD_PRESERVE")

    def test_067_or_unclosed_migration_never_offers_restore_or_downgrade(self):
        for state in (
            replace(State(), schema=AFTER),
            replace(State(), migration_intent=True),
            replace(State(), pending="MIGRATE"),
        ):
            result = recovery_plan.propose(state, facts())
            self.assertNotEqual(result["branch"], "PRE066_RECOVERY_ELIGIBLE")
            self.assertEqual(result["actions"], [])

    def test_full_066_evidence_required_not_only_head(self):
        for key in (
            "full_schema_matches",
            "acl_matches",
            "old_images_present",
            "queue_known",
        ):
            bad = deepcopy(facts())
            bad[key] = False
            self.assertEqual(
                recovery_plan.propose(State(), bad)["branch"], "HOLD_UNKNOWN"
            )

    def test_partial_recovery_does_not_repeat_start(self):
        bad = facts()
        bad["recovery_started"] = True
        self.assertEqual(recovery_plan.propose(State(), bad)["branch"], "HOLD_UNKNOWN")

    def test_pre066_is_only_advice_with_no_executable_actions(self):
        result = recovery_plan.propose(State(), facts())
        self.assertEqual(result["actions"], [])
        self.assertEqual(
            result.get("proposed_order", [])[-1:], ["OPEN_OLD_AFTER_ACCEPTANCE"]
        )
