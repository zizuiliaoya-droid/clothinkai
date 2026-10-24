"""Public shape regression from systemd Job=empty, no systemctl invocation."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import Rejected
from host_collectors import timer_projection


class SystemdProjectionTests(unittest.TestCase):
    def test_empty_and_zero_job_are_idle_other_job_is_busy(self):
        raw = b"Id=clothinkai-production-backup.timer\nLoadState=loaded\nActiveState=active\nSubState=waiting\nUnitFileState=enabled\nJob="
        for job, busy in ((b"", 0), (b"0", 0), (b"12", 1)):
            self.assertEqual(
                timer_projection(raw + job + b"\n", "backup")["queued_jobs"], busy
            )
        for job in (b"unknown", b"12 /job/12", b"-1"):
            with self.assertRaises(Rejected):
                timer_projection(raw + job + b"\n", "backup")


if __name__ == "__main__":
    unittest.main()
