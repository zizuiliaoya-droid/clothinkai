import hashlib
from pathlib import Path
import tempfile
import unittest

from contracts import Rejected
from journal import Journal
import maintenance


def timers():
    return {
        name: {
            "active": True,
            "enabled": True,
            "masked": False,
            "unit_sha256": "a" * 64,
            "service_running": False,
            "queued_jobs": 0,
            "child_processes": 0,
        }
        for name in ("backup", "renew")
    }


class MaintenanceTests(unittest.TestCase):
    def test_timer_running_or_queued_prevents_maintenance(self):
        for field, value in (
            ("service_running", True),
            ("queued_jobs", 1),
            ("child_processes", 1),
        ):
            state = timers()
            state["renew"][field] = value
            with self.assertRaises(Rejected):
                maintenance.freeze_plan(state)

    def test_freeze_only_targets_original_timers_and_preserves_snapshot(self):
        state = timers()
        result = maintenance.freeze_plan(state)
        self.assertEqual(
            result["commands"],
            (
                ("systemctl", "stop", "clothinkai-production-backup.timer"),
                ("systemctl", "disable", "clothinkai-production-backup.timer"),
                ("systemctl", "stop", "clothinkai-production-renew.timer"),
                ("systemctl", "disable", "clothinkai-production-renew.timer"),
            ),
        )
        self.assertTrue(state["backup"]["enabled"])

    def test_public_write_aliases_and_ready_are_closed(self):
        for path in (
            "/",
            "/api",
            "/api/",
            "/api/auth/login",
            "/api/uploads/presign",
            "/%61pi/callback",
            "/api%2Fcallback",
            "/health",
            "/ready",
            "/.well-known/acme-challenge/../../api/callback",
        ):
            self.assertEqual(maintenance.expected_status(path), 503, path)
        self.assertEqual(
            maintenance.expected_status("/.well-known/acme-challenge/token_123"), 200
        )

    def test_reload_success_without_connection_drain_cannot_close_gate(self):
        facts = {
            "syntax_ok": True,
            "reload_ok": True,
            "old_nginx_workers": 1,
            "old_connections": 0,
            "public_status": maintenance.probe_results(),
            "acme_status": 200,
        }
        with self.assertRaises(Rejected):
            maintenance.verify_closed(facts)

    def test_atomic_switch_keeps_original_and_interruption_keeps_closed_state(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as folder:
            root = Path(folder)
            (root / "nginx-active.conf").write_bytes(b"synthetic open")
            with self.assertRaises(Rejected):
                maintenance.close_fixture(
                    Journal(root),
                    hashlib.sha256(b"synthetic open").hexdigest(),
                    b"synthetic closed",
                    "after_replace",
                )
            self.assertEqual((root / "old-active.conf").read_bytes(), b"synthetic open")
            self.assertEqual(
                (root / "nginx-active.conf").read_bytes(), b"synthetic closed"
            )

    def test_unknown_or_replaced_old_config_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as folder:
            root = Path(folder)
            (root / "nginx-active.conf").write_bytes(b"different bytes")
            with self.assertRaises(Rejected):
                maintenance.close_fixture(Journal(root), "a" * 64, b"closed")
            self.assertEqual(
                (root / "nginx-active.conf").read_bytes(), b"different bytes"
            )
