from pathlib import Path
import tempfile
import unittest

from contracts import Rejected
from journal import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_fsync_failure_before_intent_prevents_effect(self):
        journal = Journal(self.root, fail_at="file_fsync")
        with self.assertRaises(Rejected):
            journal.append("intent", {"action_id": "fixture-action"})
        self.assertFalse(journal.committed)

    def test_truncated_tail_is_hold_not_success(self):
        journal = Journal(self.root)
        journal.append("intent", {"action_id": "fixture-action"})
        journal.path.write_bytes(journal.path.read_bytes()[:-3])
        with self.assertRaises(Rejected):
            journal.read()

    def test_hash_tamper_or_sequence_gap_rejected(self):
        journal = Journal(self.root)
        journal.append("intent", {"action_id": "fixture-action"})
        raw = journal.path.read_bytes().replace(b"fixture-action", b"fixture-edited")
        journal.path.write_bytes(raw)
        with self.assertRaises(Rejected):
            journal.read()

    def test_second_owner_cannot_acquire_same_lock_inode(self):
        first, second = Journal(self.root), Journal(self.root)
        first.acquire()
        try:
            with self.assertRaises(Rejected):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()

    def test_readback_has_monotonic_hash_chain(self):
        journal = Journal(self.root)
        journal.append("intent", {"action_id": "fixture-action"})
        journal.append(
            "result", {"action_id": "fixture-action", "facts": {"hash": "a" * 64}}
        )
        rows = journal.read()
        self.assertEqual([r["sequence"] for r in rows], [1, 2])
        self.assertEqual(rows[1]["previous"], rows[0]["sha256"])


if __name__ == "__main__":
    unittest.main()
