from copy import deepcopy
import unittest
from contracts import AFTER, Rejected
import acceptance


def internal():
    return {
        "mode": "synthetic",
        "head": AFTER,
        "commit": "a" * 40,
        "frontend_commit": "a" * 40,
        "backend_image": "sha256:" + "b" * 64,
        "frontend_sha256": "c" * 64,
        "db_id": "1" * 64,
        "redis_id": "2" * 64,
        "network_ids": {"back": "3" * 64, "egress": "4" * 64},
        "public_ports": [80, 443],
        "ready": {"database": "ok", "redis": "ok", "status": "ready"},
        "origin": "https://47.110.76.9",
        "tls_valid": True,
        "tls_remaining_seconds": 172800,
        "app_superuser": False,
        "app_bypassrls": False,
        "app_rls_checked": True,
        "role_access": {"admin": True, "pr_manager": True, "fixture_reader": False},
        "cache_ttl_seconds": 300,
        "migration_tick": 100,
        "observed_tick": 400,
        "stable_ticks": [370, 400],
        "worker_count": 0,
        "beat_count": 0,
        "gate_closed": True,
        "k7_errors": 0,
        "k8_amount_delta_minor": 0,
        "new_retro_samples": 0,
        "r33_samples": 0,
        "unknown_fields": [],
    }


class AcceptanceTests(unittest.TestCase):
    def test_old_frontend_wrong_network_or_escalated_role_rejected(self):
        good = internal()
        for key, value in (
            ("frontend_commit", "d" * 40),
            ("network_ids", {}),
            ("app_superuser", True),
            ("app_rls_checked", False),
            ("ready", {"status": "ready"}),
            ("public_ports", [80, 443, 5432]),
        ):
            bad = deepcopy(good)
            bad[key] = value
            with self.assertRaises(Rejected):
                acceptance.verify(bad, good)

    def test_ttl_actual_roles_and_money_delta_gate(self):
        good = internal()
        for key, value in (
            ("observed_tick", 399),
            ("role_access", {"admin": True}),
            ("k8_amount_delta_minor", 1),
            ("worker_count", 1),
            ("tls_remaining_seconds", 10),
        ):
            bad = deepcopy(good)
            bad[key] = value
            with self.assertRaises(Rejected):
                acceptance.verify(bad, good)

    def test_no_samples_stays_pending_not_fabricated_success(self):
        result = acceptance.verify(internal(), internal())
        self.assertIn("pending_business_samples", result)
        self.assertEqual(result["pending_business_samples"], ["K7-4", "R33"])
        self.assertFalse(result["live_verified"])
