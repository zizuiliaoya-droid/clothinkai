"""Conservative recovery advice; does not restore or start anything."""

from contracts import BEFORE, exact, require
from state_machine import recovery_eligible


def propose(state, facts):
    exact(
        facts,
        (
            "head",
            "full_schema_matches",
            "acl_matches",
            "baseline_matches",
            "old_images_present",
            "old_controls_match",
            "queue_known",
            "owned_exit_confirmed",
            "recovery_started",
        ),
    )
    require(
        all(type(v) is bool for k, v in facts.items() if k != "head"),
        "recovery_fact_types",
    )
    out = {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "actions": [],
        "proposed_order": [],
    }
    if (
        state.migration_intent
        or state.possible_writes
        or state.schema != BEFORE
        or facts["head"] != BEFORE
    ):
        return {
            **out,
            "branch": "HOLD_PRESERVE",
            "reason": "migration_or_write_boundary",
            "missing": [],
        }
    missing = [
        k
        for k, v in facts.items()
        if k not in {"head", "recovery_started"} and v is not True
    ]
    if not recovery_eligible(state) or missing or facts["recovery_started"]:
        return {
            **out,
            "branch": "HOLD_UNKNOWN",
            "reason": "incomplete_or_repeated_recovery",
            "missing": missing,
        }
    return {
        **out,
        "branch": "PRE066_RECOVERY_ELIGIBLE",
        "reason": "requires_separate_reviewed_action",
        "missing": [],
        "proposed_order": [
            "CLOSE_GATE",
            "RESTORE_OLD_CONFIG",
            "START_OLD_API",
            "START_UNIQUE_OLD_WORKER",
            "START_UNIQUE_OLD_BEAT",
            "ACCEPT_OLD_INTERNAL",
            "RESTORE_OLD_OPERATIONS",
            "OPEN_OLD_AFTER_ACCEPTANCE",
        ],
    }
