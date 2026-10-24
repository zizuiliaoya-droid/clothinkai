"""Host boundary contracts only; never call Docker, systemctl or a real API."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contracts import STEPS, Rejected
from fixed_host_transport import command, step_operations, validate_readonly
from host_collectors import container_projection, queue_projection, timer_projection


class HostTransportTests(unittest.TestCase):
    def test_inspect_is_projection_not_secret_environment(self):
        argv = command("inspect", "api")
        self.assertIn("--format", argv)
        self.assertNotIn(".Config.Env", " ".join(argv))
        self.assertEqual(argv[-1], "clothinkai-prod-api-20261018")

    def test_closed_operations_reject_argv_paths_and_roles(self):
        for operation, role in (
            ("exec", "api"),
            ("inspect", "../db"),
            ("stop", "db"),
            ("stop", "redis"),
        ):
            with self.assertRaises(Rejected):
                command(operation, role)

    def test_mutations_have_no_activation_switch(self):
        with self.assertRaisesRegex(Rejected, "production_authority_unbound"):
            validate_readonly("stop", "api")
        self.assertEqual(
            command("stop", "worker")[-3:],
            ("--time", "120", "clothinkai-prod-worker-20261018"),
        )

    def test_every_action_has_explicit_binding_and_missing_evidence(self):
        self.assertEqual(tuple(step_operations()), STEPS)
        for binding in step_operations().values():
            self.assertFalse(binding["live_enabled"])
            self.assertTrue(binding["requires"])
        self.assertEqual(step_operations()["BACKUP"]["commands"], (("backup", "api"),))

    def test_container_projection_strips_nothing_silently(self):
        row = dict(
            id="a" * 64,
            name="/clothinkai-prod-api-20261018",
            image="sha256:" + "b" * 64,
            running=True,
            paused=False,
            restarting=False,
            oom=False,
            exit_code=0,
            restarts=0,
            restart_policy="no",
            pid=123,
            networks={"clothinkai-prod-egress-20261018": "c" * 64},
        )
        result = container_projection(json.dumps(row).encode(), "api")
        self.assertEqual(result["id"], "a" * 64)
        row["password"] = "never-echo-this"
        with self.assertRaisesRegex(Rejected, "fields"):
            container_projection(json.dumps(row).encode(), "api")

    def test_timer_unknown_and_activating_are_not_idle(self):
        raw = b"Id=clothinkai-production-backup.timer\nLoadState=loaded\nActiveState=active\nSubState=waiting\nUnitFileState=enabled\nJob=0\n"
        self.assertTrue(timer_projection(raw, "backup")["active"])
        for replacement in (b"activating", b"unknown"):
            with self.assertRaises(Rejected):
                timer_projection(
                    raw.replace(b"ActiveState=active", b"ActiveState=" + replacement),
                    "backup",
                )

    def test_ready_zero_never_proves_celery_drained(self):
        rows = {q: [0, 0, 0, 0] for q in ("default", "backup", "crawler", "report")}
        result = queue_projection(
            {
                "ready": rows,
                "unacked_hash": 0,
                "unacked_index": 0,
                "business_sessions": 0,
            }
        )
        self.assertFalse(result["drain_verified"])
        self.assertIn("scheduled_retry_active", result["pending"])
        rows["default"][0] = True
        with self.assertRaises(Rejected):
            queue_projection(
                {
                    "ready": rows,
                    "unacked_hash": 0,
                    "unacked_index": 0,
                    "business_sessions": 0,
                }
            )

    def test_probe_uses_original_runtime_keys_and_database(self):
        import hashlib

        import original_api_observer
        from fixed_host_transport import API_OBSERVER_SHA256
        from sql_policy import DATABASE

        self.assertEqual(
            hashlib.sha256(
                Path(original_api_observer.__file__).read_bytes()
            ).hexdigest(),
            API_OBSERVER_SHA256,
        )

        config = {
            "DATABASE_URL_BYPASS": "postgresql+asyncpg://clothing_bypass:fixture@prod-db:5432/"
            + DATABASE,
            "REDIS_URL_CELERY_BROKER": "redis://prod-redis:6379/1",
        }
        original_api_observer.validate_config(config)
        for key, value in (
            ("REDIS_URL_CELERY_BROKER", "redis://evil:6379/1"),
            ("DATABASE_URL_BYPASS", config["DATABASE_URL_BYPASS"] + "?sslmode=disable"),
        ):
            with self.assertRaises(ValueError):
                original_api_observer.validate_config({**config, key: value})

    def test_public_gate_uses_verified_tls_and_never_follows_redirect(self):
        from unittest.mock import patch

        from public_gate_observer import observe_gate

        class Socket:
            def getpeercert(self):
                return {"notAfter": "Oct 23 00:00:00 2030 GMT"}

        class Response:
            status = 503

            def read(self, limit):
                return b""

        class Connection:
            def __init__(self, host, **kwargs):
                self.assertions = kwargs
                self.sock = Socket()
                assert host == "47.110.76.9"
                assert kwargs["context"].check_hostname

            def connect(self):
                pass

            def request(self, method, path, headers):
                assert method == "GET" and "Cookie" not in headers

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with patch("public_gate_observer.http.client.HTTPSConnection", Connection):
            result = observe_gate()
            self.assertFalse(result["gate_verified"])
            self.assertEqual(set(result["public_status"].values()), {503})
            Response.status = 302
            with self.assertRaisesRegex(Rejected, "http_redirect"):
                observe_gate()


if __name__ == "__main__":
    unittest.main()
