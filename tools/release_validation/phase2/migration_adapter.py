"""Phase2 integration boundary; no live launcher or permit generation."""

from contracts import AFTER, BEFORE, digest, exact, require, sha


def _ids(value):
    require(
        type(value) is list
        and all(type(item) is str and 0 < len(item) <= 128 for item in value),
        "id_set",
    )
    require(len(value) == len(set(value)), "duplicate_id")
    return set(value)


def verify_acceptance(before, after):
    exact(
        before,
        (
            "head",
            "normal_ids",
            "recall_ids",
            "pending_ids",
            "published_unstarted_ids",
            "legacy_unconfirmed_ids",
            "old_scope_sha256",
            "old_override_sha256",
            "roles_acl_sha256",
        ),
    )
    exact(
        after,
        (
            "head",
            "normal_ids",
            "recall_ids",
            "mapped_ids",
            "all_completed_ids",
            "legacy_unconfirmed_ids",
            "new_scope_roles",
            "old_scope_sha256",
            "old_override_sha256",
            "roles_acl_sha256",
            "column_timestamptz",
            "constraint_exact",
            "partial_index_exact",
            "old_status_count",
            "published_unstarted_count",
            "migration_event_ids",
            "started_tick",
            "observed_tick",
            "gate_closed",
            "business_sessions",
            "old_processes",
            "unknown_fields",
        ),
    )
    require(before["head"] == BEFORE and after["head"] == AFTER, "revision")
    expected = {
        key: _ids(before[key])
        for key in (
            "normal_ids",
            "recall_ids",
            "pending_ids",
            "published_unstarted_ids",
            "legacy_unconfirmed_ids",
        )
    }
    require(not expected["normal_ids"] & expected["recall_ids"], "completion_overlap")
    for key in ("normal_ids", "recall_ids", "legacy_unconfirmed_ids"):
        require(_ids(after[key]) == expected[key], "stable_set_mismatch")
    require(
        _ids(after["all_completed_ids"])
        == expected["normal_ids"] | expected["recall_ids"],
        "completion_set",
    )
    require(
        _ids(after["mapped_ids"])
        == expected["pending_ids"] | expected["published_unstarted_ids"],
        "mapping_set",
    )
    require(after["new_scope_roles"] == ["pr_manager"], "new_scope_roles")
    for key in ("old_scope_sha256", "old_override_sha256", "roles_acl_sha256"):
        sha(before[key])
        require(after[key] == before[key], "legacy_privilege_changed")
    for key in (
        "column_timestamptz",
        "constraint_exact",
        "partial_index_exact",
        "gate_closed",
    ):
        require(after[key] is True, "schema_or_gate")
    for key in (
        "old_status_count",
        "published_unstarted_count",
        "business_sessions",
        "old_processes",
    ):
        require(
            type(after[key]) is int and after[key] == 0, "unexpected_rows_or_writers"
        )
    require(
        after["migration_event_ids"] == [] and after["unknown_fields"] == [],
        "unknown_migration_effects",
    )
    require(
        all(type(after[k]) is int for k in ("started_tick", "observed_tick"))
        and 0 <= after["started_tick"] <= after["observed_tick"],
        "migration_interval",
    )
    return {
        "mode": "synthetic",
        "live_verified": False,
        "head": AFTER,
        "normal_count": len(expected["normal_ids"]),
        "recall_count": len(expected["recall_ids"]),
        "mapping_count": len(_ids(after["mapped_ids"])),
        "baseline_sha256": digest(before),
        "acceptance_sha256": digest(after),
        "migration_observation_interval": [
            after["started_tick"],
            after["observed_tick"],
        ],
    }


def verify_prerequisites(evidence):
    exact(
        evidence,
        (
            "backup_sha256",
            "restore_dump_sha256",
            "fresh_backup",
            "restore_verified",
            "gate_closed",
            "api_exited",
            "worker_exited",
            "beat_exited",
            "child_processes",
            "business_sessions",
            "external_producers",
            "queue_empty_tick",
            "now_tick",
            "last_write_tick",
            "timers_idle",
            "head",
            "live_enabled",
        ),
    )
    sha(evidence["backup_sha256"])
    require(
        evidence["restore_dump_sha256"] == evidence["backup_sha256"], "restore_binding"
    )
    for key in (
        "fresh_backup",
        "restore_verified",
        "gate_closed",
        "api_exited",
        "worker_exited",
        "beat_exited",
        "timers_idle",
    ):
        require(evidence[key] is True, "migration_prerequisite")
    for key in ("child_processes", "business_sessions", "external_producers"):
        require(type(evidence[key]) is int and evidence[key] == 0, "writers_alive")
    for key in ("queue_empty_tick", "now_tick", "last_write_tick"):
        require(type(evidence[key]) is int and evidence[key] >= 0, "time")
    require(
        evidence["last_write_tick"]
        <= evidence["queue_empty_tick"]
        <= evidence["now_tick"]
        <= evidence["queue_empty_tick"] + 300,
        "freeze_stale",
    )
    require(
        evidence["head"] == BEFORE and evidence["live_enabled"] is False,
        "migration_mode",
    )
    return {
        "mode": "synthetic",
        "prerequisites_sha256": digest(evidence),
        "live_enabled": False,
    }


def classify_outcome(outcome):
    # Process success never substitutes for the separate stable-ID/ACL/schema acceptance.
    if type(outcome) is not dict or outcome.get("outcome") != "verified":
        return "HOLD_PRESERVE"
    exits = outcome.get("owned_exit", {})
    if (
        not all(exits.get(k) is True for k in ("process", "container", "backend"))
        or outcome.get("host_lock_released") is not True
    ):
        return "HOLD_UNKNOWN"
    return "MIGRATED_REQUIRES_SCHEMA_ACCEPTANCE"


async def run_candidate(release_spec, prerequisites, policy, transport, journal):
    """Bind the frozen synthetic SQL driver to release/freeze evidence; no live permit."""
    from contracts import parse_json
    from migration_process import run_guarded_migration
    from sql_policy import validate_spec

    verify_prerequisites(prerequisites)
    policy = validate_spec(policy)
    require(
        all(
            policy.data[k] == release_spec.data[k]
            for k in ("source_commit", "source_tree", "release_id")
        ),
        "policy_release_binding",
    )
    for name in ("backup_receipt", "restore_receipt"):
        require(
            parse_json(policy.artifact(name))["backup_sha256"]
            == prerequisites["backup_sha256"],
            "policy_backup_binding",
        )
    action_id = release_spec.data["release_id"] + ":sql-migration"
    hold = {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "classification": "HOLD_PRESERVE",
        "policy_sha256": policy.sha256,
    }
    # One durable attempt, including successful attempts. Reconciliation never retries.
    if journal.lock is not None:
        return hold
    journal.acquire()
    try:
        if any(row["payload"].get("action_id") == action_id for row in journal.read()):
            # Existing unknown operations can still be running; retain the acquired lock.
            return hold
        journal.append(
            "intent",
            {
                "action_id": action_id,
                "name": "MIGRATE",
                "boot_id": release_spec.data["boot_id"],
                "input_sha256": release_spec.sha256,
                "policy_sha256": policy.sha256,
                "prerequisites_sha256": digest(prerequisites),
            },
        )
        outcome = await run_guarded_migration(policy, transport)
        classification = classify_outcome(outcome)
        report = {**hold, "classification": classification, "process_result": outcome}
        journal.append("result", {"action_id": action_id, "receipt": report})
        if (
            all(outcome["owned_exit"].values())
            and outcome["host_lock_released"] is True
        ):
            journal.release()
        return report
    except BaseException:
        # Unknown fsync/cancellation/process status keeps the journal lock until explicit reconciliation.
        return hold
