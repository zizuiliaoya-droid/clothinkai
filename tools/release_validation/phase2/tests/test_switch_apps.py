from copy import deepcopy
import unittest
from contracts import AFTER, Rejected
import switch_apps


def binding():
    return {
        "mode": "synthetic",
        "role": "api",
        "operation": "start",
        "head": AFTER,
        "commit": "a" * 40,
        "schema_receipt_sha256": "b" * 64,
        "config_sha256": "c" * 64,
        "guard_directory": "guards/api",
        "read_only": True,
        "live_enabled": False,
        "gate_closed": True,
    }


class SwitchTests(unittest.TestCase):
    def test_worker_cannot_reuse_api_binding(self):
        with self.assertRaises(Rejected):
            switch_apps.validate_start(binding(), "worker", "a" * 40)

    def test_wrong_source_guard_or_unverified_schema_rejected(self):
        for key, val in (
            ("commit", "d" * 40),
            ("guard_directory", "guards/worker"),
            ("read_only", False),
            ("head", "066"),
            ("gate_closed", False),
        ):
            bad = binding()
            bad[key] = val
            with self.assertRaises(Rejected):
                switch_apps.validate_start(bad, "api", "a" * 40)

    def test_only_requested_application_may_change(self):
        old = {
            "db": {"id": "1" * 64},
            "redis": {"id": "2" * 64},
            "api": {"id": "3" * 64, "image": "sha256:" + "4" * 64},
            "web": {"id": "5" * 64},
        }
        new = deepcopy(old)
        new["api"] = {"id": "6" * 64, "image": "sha256:" + "7" * 64}
        self.assertTrue(
            switch_apps.verify_replaced(old, new, "api", "sha256:" + "7" * 64)
        )
        new["db"]["id"] = "9" * 64
        with self.assertRaises(Rejected):
            switch_apps.verify_replaced(old, new, "api", "sha256:" + "7" * 64)
