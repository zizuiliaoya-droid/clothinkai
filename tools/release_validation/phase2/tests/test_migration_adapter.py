from copy import deepcopy
import unittest

from contracts import AFTER, BEFORE, Rejected
import migration_adapter as migration


def snapshots():
    before = {
        "head": BEFORE,
        "normal_ids": ["fixture-normal"],
        "recall_ids": ["fixture-recall"],
        "pending_ids": ["fixture-pending"],
        "published_unstarted_ids": ["fixture-published"],
        "legacy_unconfirmed_ids": ["fixture-retro"],
        "old_scope_sha256": "1" * 64,
        "old_override_sha256": "2" * 64,
        "roles_acl_sha256": "3" * 64,
    }
    after = {
        "head": AFTER,
        "normal_ids": ["fixture-normal"],
        "recall_ids": ["fixture-recall"],
        "mapped_ids": ["fixture-pending", "fixture-published"],
        "all_completed_ids": ["fixture-normal", "fixture-recall"],
        "legacy_unconfirmed_ids": ["fixture-retro"],
        "new_scope_roles": ["pr_manager"],
        "old_scope_sha256": "1" * 64,
        "old_override_sha256": "2" * 64,
        "roles_acl_sha256": "3" * 64,
        "column_timestamptz": True,
        "constraint_exact": True,
        "partial_index_exact": True,
        "old_status_count": 0,
        "published_unstarted_count": 0,
        "migration_event_ids": [],
        "started_tick": 80,
        "observed_tick": 90,
        "gate_closed": True,
        "business_sessions": 0,
        "old_processes": 0,
        "unknown_fields": [],
    }
    return before, after


def prerequisites():
    return {
        "backup_sha256": "a" * 64,
        "restore_dump_sha256": "a" * 64,
        "fresh_backup": True,
        "restore_verified": True,
        "gate_closed": True,
        "api_exited": True,
        "worker_exited": True,
        "beat_exited": True,
        "child_processes": 0,
        "business_sessions": 0,
        "external_producers": 0,
        "queue_empty_tick": 30,
        "now_tick": 40,
        "last_write_tick": 10,
        "timers_idle": True,
        "head": BEFORE,
        "live_enabled": False,
    }


class MigrationAdapterTests(unittest.TestCase):
    def test_exact_stable_sets_and_scope_are_required(self):
        before, after = snapshots()
        for key, value in (
            ("all_completed_ids", ["fixture-normal"]),
            ("mapped_ids", []),
            ("old_override_sha256", "f" * 64),
            ("legacy_unconfirmed_ids", []),
            ("new_scope_roles", ["admin"]),
            ("normal_ids", ["fixture-normal", "fixture-recall"]),
        ):
            bad = deepcopy(after)
            bad[key] = value
            with self.assertRaises(Rejected):
                migration.verify_acceptance(before, bad)

    def test_revision_alone_does_not_prove_schema(self):
        before, after = snapshots()
        for key, value in (
            ("constraint_exact", False),
            ("partial_index_exact", False),
            ("column_timestamptz", False),
            ("old_status_count", 1),
            ("migration_event_ids", ["invented-business-event"]),
            ("unknown_fields", ["constraint"]),
            ("old_processes", 1),
        ):
            bad = deepcopy(after)
            bad[key] = value
            with self.assertRaises(Rejected):
                migration.verify_acceptance(before, bad)

    def test_valid_synthetic_sets_preserve_time_interval_and_no_ids_in_summary(self):
        before, after = snapshots()
        result = migration.verify_acceptance(before, after)
        self.assertIn("migration_observation_interval", result)
        self.assertEqual(result["migration_observation_interval"], [80, 90])
        self.assertEqual(result["normal_count"], 1)
        self.assertNotIn("fixture-normal", str(result))
        self.assertFalse(result["live_verified"])

    def test_no_migration_until_fresh_backup_restore_and_all_old_processes_exit(self):
        good = prerequisites()
        for key, value in (
            ("api_exited", False),
            ("business_sessions", 1),
            ("restore_dump_sha256", "b" * 64),
            ("now_tick", 400),
            ("last_write_tick", 35),
            ("live_enabled", True),
        ):
            bad = deepcopy(good)
            bad[key] = value
            with self.assertRaises(Rejected):
                migration.verify_prerequisites(bad)

    def test_failed_unknown_or_missing_exits_never_verifies(self):
        for outcome in (
            {"outcome": "unknown"},
            {"outcome": "failed_known"},
            {"outcome": "verified"},
            {
                "outcome": "verified",
                "child_exited": True,
                "container_exited": False,
                "backend_exited": True,
                "head": AFTER,
            },
        ):
            self.assertNotEqual(migration.classify_outcome(outcome), "SCHEMA_VERIFIED")
