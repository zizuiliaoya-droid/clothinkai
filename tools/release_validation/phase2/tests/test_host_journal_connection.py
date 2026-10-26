"""Real temporary-file journal, mocked host boundary. No Linux/Docker claims."""

import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts import Rejected, digest
from fixed_host_transport import ROLES, ROOT
from host_sequence import HostCoordinator, HostJournal, _hold_error_code, replay
from journal import Journal
from sql_policy import SqlRejected, canonical, parse
from state_machine import next_action
from tests.test_host_policy import policy_fixture
from tests.test_migration_process import NoSocketAsyncCase


class HostJournalConnectionTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.disk = Journal(Path(self.temp.name))
        self.spec, _, _ = policy_fixture()
        self.baseline = {role: str(index) * 64 for index, role in enumerate(ROLES, 1)}
        binding = parse(self.spec._binding)
        binding["baseline_sha256"] = digest(self.baseline)
        self.spec = replace(self.spec, _binding=canonical(binding))
        self.disk.acquire()
        # Seed already completed prior steps explicitly as fixture history only.
        for _ in range(5):
            action = next_action(self.spec, replay(self.spec, self.disk.read()))
            self.disk.append("intent", asdict(action))
            self.disk.append(
                "result",
                {
                    "action_id": action.action_id,
                    "input_sha256": self.spec.sha256,
                    "facts_sha256": digest({"fixture_only": True}),
                    "completed": True,
                },
            )
        self.journal = object.__new__(HostJournal)
        self.journal.root = (
            Path(ROOT) / "releases" / self.spec.data["release_id"] / "state"
        )
        self.journal.lock = self.disk.lock
        self.journal.read = self.disk.read
        self.journal.append = self.disk.append
        from tests.test_host_finite_operations import FixtureDirectory

        self.journal.directory = FixtureDirectory(Path(self.temp.name))
        self.coordinator = HostCoordinator(self.spec, self.journal, self.baseline)

    async def asyncTearDown(self):
        self.disk.release()
        self.temp.cleanup()

    async def test_fsync_intent_precedes_effect_and_result_not_repeated(self):
        effects = []

        async def effect(*args):
            self.assertEqual(self.disk.read()[-1]["kind"], "intent")
            effects.append(args[0])
            return {"fixture_restart_disabled": True}

        with (
            patch("host_sequence.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_sequence._dispatch", side_effect=effect),
        ):
            first = await self.coordinator.run_next()
            second = await self.coordinator.run_next()
        self.assertEqual(first["status"], "STEP_RECORDED")
        self.assertEqual(second["status"], "STEP_RECORDED")
        self.assertEqual(effects, ["DISABLE_RESTART", "CLOSE_GATE"])
        self.assertIsNotNone(self.disk.lock)

    async def test_lost_reply_holds_durable_intent_and_never_replays(self):
        effects = AsyncMock(side_effect=RuntimeError("synthetic-reply-lost"))
        with (
            patch("host_sequence.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_sequence._dispatch", effects),
        ):
            first = await self.coordinator.run_next()
            second = await self.coordinator.run_next()
        self.assertEqual(first["status"], "HOLD_UNKNOWN")
        self.assertEqual(first["error_code"], "host_action_unknown")
        self.assertNotIn("synthetic-reply-lost", str(self.disk.read()) + str(first))
        self.assertEqual(second["status"], "HOLD")
        self.assertEqual(effects.await_count, 1)
        self.assertIsNotNone(replay(self.spec, self.disk.read()).pending)
        self.assertIsNotNone(self.disk.lock)

    async def test_fixed_error_code_is_durable_and_does_not_allow_replay(self):
        effects = AsyncMock(side_effect=Rejected("nginx_child_identity"))
        with (
            patch("host_sequence.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_sequence._dispatch", effects),
        ):
            first = await self.coordinator.run_next()
            second = await self.coordinator.run_next()
        self.assertEqual(first["error_code"], "nginx_child_identity")
        self.assertEqual(
            self.disk.read()[-1]["payload"]["error_code"], "nginx_child_identity"
        )
        self.assertEqual(second["status"], "HOLD")
        self.assertEqual(effects.await_count, 1)

    def test_only_fixed_contract_code_format_is_reported(self):
        self.assertEqual(
            _hold_error_code(SqlRejected("private_regular_file")),
            "private_regular_file",
        )
        for error in (
            RuntimeError("nginx_child_identity"),
            Rejected("password=secret"),
            Rejected("nginx_child_identity\nsecret"),
            Rejected("x" * 97),
            Rejected("nginx_child_identity", "secret"),
        ):
            with self.subTest(error_type=type(error).__name__):
                self.assertEqual(_hold_error_code(error), "host_action_unknown")

    async def test_intent_fsync_failure_launches_nothing(self):
        self.disk.fail_at = "file_fsync"
        effects = AsyncMock()
        with (
            patch("host_sequence.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_sequence._dispatch", effects),
        ):
            with self.assertRaises(Exception):
                await self.coordinator.run_next()
        self.assertEqual(effects.await_count, 0)
        self.assertIsNotNone(self.disk.lock)
