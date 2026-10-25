"""Finite migration ownership failures and receipt binding; no Docker or SQL IO."""

import tempfile
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contracts import digest
from host_migration_control import migrate, recorded_facts, verify_backend_absence
from host_sequence import replay
from sql_policy import canonical
from state_machine import next_action
from tests.test_host_build_control import release_fixture
from tests.test_host_finite_operations import FixtureDirectory
from tests.test_host_policy import policy_fixture
from tests.test_migration_process import NoSocketAsyncCase


class MigrationHostTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.directory = FixtureDirectory(Path(self.temp.name))
        self.spec = release_fixture()
        self.rows = []
        self.journal = SimpleNamespace(directory=self.directory, read=lambda: self.rows)
        self.baseline = {
            role: str(i) * 64
            for i, role in enumerate(("api", "web", "worker", "beat", "db", "redis"), 1)
        }

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_receipt_is_unusable_without_completed_result_and_exact_hash(self):
        facts = {"mode": "host_observed", "image": {"id": "sha256:" + "4" * 64}}
        self.directory.create("step-build_backend-facts.json", canonical(facts))
        with self.assertRaises(Exception):
            recorded_facts(self.spec, self.journal, "BUILD_BACKEND")
        for _ in range(2):
            action = next_action(self.spec, replay(self.spec, self.rows))
            self.rows.append({"kind": "intent", "payload": asdict(action)})
            self.rows.append(
                {
                    "kind": "result",
                    "payload": {
                        "action_id": action.action_id,
                        "input_sha256": self.spec.sha256,
                        "facts_sha256": digest(facts),
                        "completed": True,
                    },
                }
            )
        self.assertEqual(
            recorded_facts(self.spec, self.journal, "BUILD_BACKEND"), facts
        )
        (Path(self.temp.name) / "step-build_backend-facts.json").write_bytes(
            canonical({**facts, "mode": "synthetic"})
        )
        with self.assertRaises(Exception):
            recorded_facts(self.spec, self.journal, "BUILD_BACKEND")

    async def ownership_failure(self, stage):
        calls = []
        policy, _, _ = policy_fixture()
        policy = SimpleNamespace(
            data=policy.data,
            _binding=policy._binding,
            _authority=canonical({}),
            _artifacts=policy._artifacts,
        )

        async def capture(argv, **kwargs):
            calls.append(argv)
            if "{{.HostConfig.NetworkMode}}" in argv:
                return ("2" * 64).encode()
            if "create" in argv:
                self.assertTrue(
                    (Path(self.temp.name) / "migration-create-intent.json").exists()
                )
                self.assertNotIn("--rm", argv)
                self.assertIn("--pull=never", argv)
                if stage == "create":
                    raise TimeoutError("synthetic-create-reply-lost")
                return ("7" * 64).encode()
            if "start" in argv:
                self.assertTrue(
                    (Path(self.temp.name) / "migration-container.json").exists()
                )
                self.assertTrue(
                    (Path(self.temp.name) / "migration-policy.json").exists()
                )
                raise TimeoutError("synthetic-start-reply-lost")
            return b""

        async def observe(operation, role):
            return {
                "facts": {
                    "id": self.baseline[role],
                    "running": False,
                    "pid": 0,
                    "restart_policy": "no",
                    "networks": {"internal": "2" * 64},
                }
            }

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "host_migration_control.database_clock",
                    return_value="2026-10-24T01:00:00.000000Z",
                )
            )
            stack.enter_context(
                patch("host_session_contract.observe_sessions", return_value={})
            )
            for target in ("require_step", "migration_artifacts"):
                stack.enter_context(patch("host_migration_control." + target))
            stack.enter_context(
                patch(
                    "host_migration_control.recorded_facts",
                    return_value={"image": {"id": "sha256:" + "4" * 64}},
                )
            )
            stack.enter_context(
                patch("host_migration_control.collect", side_effect=observe)
            )
            stack.enter_context(
                patch("host_migration_control._capture", side_effect=capture)
            )
            stack.enter_context(
                patch("host_migration_control.derive_policy", return_value=policy)
            )
            stack.enter_context(
                patch(
                    "host_migration_control._container",
                    return_value={
                        "id": "7" * 64,
                        "image": "sha256:" + "4" * 64,
                        "running": False,
                        "restart_policy": "no",
                        "networks": {"internal": "2" * 64},
                    },
                )
            )
            stack.enter_context(
                patch("host_build_control._release_fd", return_value=123)
            )
            for operation in ("mkdir", "fsync", "close"):
                stack.enter_context(patch("host_migration_control.os." + operation))
            with self.assertRaises(TimeoutError):
                await migrate(self.spec, self.journal, self.baseline)
        self.assertFalse((Path(self.temp.name) / "migration-observed.json").exists())
        return calls

    async def test_unknown_create_preserves_intent_without_start_or_guessing_id(self):
        calls = await self.ownership_failure("create")
        self.assertFalse(any("start" in argv or "stop" in argv for argv in calls))
        self.assertFalse((Path(self.temp.name) / "migration-container.json").exists())

    async def test_unknown_start_stops_only_owned_full_id_and_never_reports_success(
        self,
    ):
        calls = await self.ownership_failure("start")
        stops = [argv for argv in calls if "stop" in argv]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0][-1], "7" * 64)

    async def test_independent_sql_readback_requires_exact_absence_and_target_head(
        self,
    ):
        policy, _, _ = policy_fixture()
        identity = {
            "pid": 123,
            "backend_start": "1780000000.123456",
            "application_name": policy.application_name,
        }
        calls = []

        async def capture(argv, **kwargs):
            calls.append(argv)
            return canonical(
                {
                    "present": False,
                    "lock_present": False,
                    "heads": ["067_retro_completion"],
                }
            )

        with patch("host_migration_control._capture", side_effect=capture):
            await verify_backend_absence(policy, self.baseline["db"], identity)
        self.assertIn(self.baseline["db"], calls[0])
        self.assertIn(identity["backend_start"], calls[0][-1])
        with patch(
            "host_migration_control._capture",
            return_value=canonical(
                {
                    "present": True,
                    "lock_present": False,
                    "heads": ["067_retro_completion"],
                }
            ),
        ):
            with self.assertRaises(Exception):
                await verify_backend_absence(policy, self.baseline["db"], identity)
