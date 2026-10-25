import hashlib
import io
import unittest

from contracts import Rejected
import backup_adapter as backup

NAME = "20261021T120000Z-" + "a" * 32 + ".dump"
PAYLOAD = b"PGDMP synthetic bytes only"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def backup_evidence():
    files = {
        NAME: {
            "bytes": len(PAYLOAD),
            "sha256": SHA,
            "created_tick": 150,
            "kind": "dump",
        },
        NAME + ".json": {
            "bytes": 100,
            "sha256": "b" * 64,
            "created_tick": 151,
            "kind": "sidecar",
        },
    }
    receipt = {
        "action_id": "fixture-backup-1",
        "started_tick": 140,
        "finished_tick": 152,
        "exit_code": 0,
        "archive_list": True,
        "archive_full_read": True,
        "local_sha256": SHA,
        "local_bytes": len(PAYLOAD),
        "r2_get_sha256": SHA,
        "r2_get_bytes": len(PAYLOAD),
        "r2_get_complete": True,
        "existing_objects_changed": False,
        "sidecar_sha256": "b" * 64,
        "api_id": "c" * 64,
    }
    return files, receipt


class BackupTests(unittest.TestCase):
    def test_legacy_api_and_no_held_backup_lock_are_mandatory(self):
        for key, value in (
            ("api_running", False),
            ("backup_lock_owned", True),
            ("timers_idle", False),
        ):
            facts = {
                "api_running": True,
                "backup_lock_owned": False,
                "timers_idle": True,
                "sessions": 0,
                "stored_bytes": 0,
                "free_bytes": 20 * 1024**3,
            }
            facts[key] = value
            with self.assertRaises(Rejected):
                backup.plan(facts)

    def test_full_stream_hash_not_just_metadata(self):
        with self.assertRaises(Rejected):
            backup.verify_stream(io.BytesIO(PAYLOAD[:-1]), len(PAYLOAD), SHA)

    def test_two_new_archives_old_sidecar_or_partial_rejected(self):
        files, receipt = backup_evidence()
        for before, after in (
            ({NAME + ".json": files[NAME + ".json"]}, files),
            ({}, {**files, "new.partial": {"bytes": 1}}),
            ({}, {**files, NAME.replace("a", "d"): files[NAME]}),
        ):
            with self.assertRaises(Rejected):
                backup.verify(before, after, receipt, 130, "fixture-backup-1", "c" * 64)

    def test_truncated_remote_get_and_lost_receipt_rejected(self):
        files, receipt = backup_evidence()
        for change in (
            {"r2_get_bytes": 1},
            {"r2_get_complete": False},
            {"exit_code": None},
            {"action_id": "old-backup"},
        ):
            with self.assertRaises(Rejected):
                backup.verify(
                    {}, files, {**receipt, **change}, 130, "fixture-backup-1", "c" * 64
                )

    def test_fresh_unique_pair_is_bound_to_action_and_freeze(self):
        files, receipt = backup_evidence()
        result = backup.verify({}, files, receipt, 130, "fixture-backup-1", "c" * 64)
        self.assertEqual(result["dump_sha256"], SHA)
        self.assertEqual(result["dump_name"], NAME)
        with self.assertRaises(Rejected):
            backup.verify({}, files, receipt, 200, "fixture-backup-1", "c" * 64)
