from copy import deepcopy
import unittest

from contracts import Rejected
import drain


def observations():
    window = {
        "tick": 100,
        "inspect_complete": True,
        "workers": 1,
        "beat_processes": 0,
        "api_requests": 0,
        "business_sessions": 0,
        "callbacks": 0,
        "queues": {
            q: {
                "ready": [0, 0, 0, 0],
                "active": 0,
                "reserved": 0,
                "scheduled": 0,
                "retry": 0,
                "unacked": 0,
            }
            for q in ("default", "backup", "crawler", "report")
        },
    }
    second = deepcopy(window)
    second["tick"] = 130
    return {
        "transport": {
            "priority_steps": [0, 3, 6, 9],
            "unacked_observed": True,
            "settings_sha256": "a" * 64,
            "versions_sha256": "b" * 64,
        },
        "producers_frozen": ["api", "beat", "callbacks", "manual"],
        "unknown_producers": 0,
        "delay_bound_seconds": 30,
        "intervening_publish_count": 0,
        "windows": [window, second],
    }


class DrainTests(unittest.TestCase):
    def test_ready_zero_does_not_hide_unacked_eta_retry(self):
        for key in ("unacked", "scheduled", "retry"):
            data = observations()
            data["windows"][1]["queues"]["crawler"][key] = 1
            with self.assertRaises(Rejected):
                drain.verify(data)

    def test_inspect_timeout_duplicate_worker_and_callback_rejected(self):
        for key, value in (
            ("inspect_complete", False),
            ("workers", 2),
            ("callbacks", 1),
            ("beat_processes", 1),
        ):
            data = observations()
            data["windows"][0][key] = value
            with self.assertRaises(Rejected):
                drain.verify(data)

    def test_retry_between_samples_or_unknown_shards_rejected(self):
        data = observations()
        data["intervening_publish_count"] = 1
        with self.assertRaises(Rejected):
            drain.verify(data)
        data = observations()
        data["transport"]["priority_steps"] = [0]
        with self.assertRaises(Rejected):
            drain.verify(data)

    def test_interval_must_cover_known_delays(self):
        data = observations()
        data["delay_bound_seconds"] = 31
        with self.assertRaises(Rejected):
            drain.verify(data)

    def test_two_complete_windows_preserve_freeze_boundary(self):
        self.assertEqual(drain.verify(observations())["last_empty_tick"], 130)

    def test_worker_stop_must_not_kill_or_leave_children_and_api_stays(self):
        for field in ("graceful", "api_running"):
            data = {
                "graceful": True,
                "exit_confirmed": True,
                "children": 0,
                "business_sessions": 0,
                "api_running": True,
                "api_id": "a" * 64,
            }
            data[field] = False
            with self.assertRaises(Rejected):
                drain.verify_stopped(data, "a" * 64)
