"""Durable synthetic journal + fixed mocked OS boundaries; zero production IO."""

import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts import STEPS, TARGET, canonical, digest
from dashboard_contract import (
    BOARD,
    BOARD_ID,
    BOARD_IMAGE,
    ERP_NETWORKS,
    NETWORK,
    NETWORK_ID,
)
from fixed_host_transport import ROLES, ROOT
from host_collectors import UNIT_PINS
from host_sequence import HostCoordinator, HostJournal, replay
from host_timer_control import freeze_timers
from host_web_network import ensure_web_network
from journal import Journal
from sql_policy import parse, sha
from state_machine import next_action
from tests.test_host_policy import policy_fixture
from tests.test_migration_process import NoSocketAsyncCase


class FixtureDirectory:
    def __init__(self, root):
        self.root = root

    def create(self, name, raw):
        with (self.root / name).open("xb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())

    def read(self, name, maximum=16384):
        raw = (self.root / name).read_bytes()
        assert len(raw) <= maximum
        return raw


class FiniteHostTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.disk = Journal(Path(self.temp.name))
        self.disk.acquire()
        self.directory = FixtureDirectory(Path(self.temp.name))
        self.spec, _, _ = policy_fixture()
        self.baseline = {role: str(index) * 64 for index, role in enumerate(ROLES, 1)}
        binding = parse(self.spec._binding)
        now = datetime.now(timezone.utc)
        attestation = canonical(
            {
                "purpose": "operator-attested-manual-backup-renew-freeze",
                "target": TARGET,
                "release_id": self.spec.data["release_id"],
                "boot_id": binding["boot_id"],
                "entries": ["manual-backup", "manual-renew"],
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
            }
        )
        self.directory.create("manual-freeze.json", attestation)
        binding.update(
            baseline_sha256=digest(self.baseline), manual_freeze_sha256=sha(attestation)
        )
        self.spec = replace(self.spec, _binding=canonical(binding))
        self.journal = object.__new__(HostJournal)
        self.journal.root = (
            Path(ROOT) / "releases" / self.spec.data["release_id"] / "state"
        )
        self.journal.lock, self.journal.directory = self.disk.lock, self.directory
        self.journal.read, self.journal.append = self.disk.read, self.disk.append

    async def asyncTearDown(self):
        self.disk.release()
        self.temp.cleanup()

    def seed(self, step, intent=False):
        for _ in range(STEPS.index(step)):
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
        if intent:
            self.disk.append(
                "intent",
                asdict(next_action(self.spec, replay(self.spec, self.disk.read()))),
            )

    def timer_boundary(self, failure=False, drift=False):
        states = {
            name: {"active": True, "enabled": True} for name in ("backup", "renew")
        }
        calls = []

        async def observe(operation, subject):
            name = subject.removesuffix("-service")
            suffix = (
                ".service"
                if subject.endswith("-service") or operation == "service_show"
                else ".timer"
            )
            unit = "clothinkai-production-" + name + suffix
            metadata = {
                "unit_sha256": "0" * 64 if drift else UNIT_PINS[unit],
                "cgroup_pid_count": 0,
            }
            if operation == "unit_source_show":
                facts = {
                    "unit": unit,
                    "fragment_path": "/etc/systemd/system/" + unit,
                    "dropins": [],
                }
            elif operation == "timer_show":
                facts = {
                    **states[name],
                    "masked": False,
                    "healthy": True,
                    "queued_jobs": 0,
                    "unit_metadata": metadata,
                }
            else:
                facts = {
                    "healthy": True,
                    "queued_jobs": 0,
                    "service_running": False,
                    "main_pid": 0,
                    "control_pid": 0,
                    "unit_metadata": metadata,
                }
            return {"facts": deepcopy(facts)}

        async def execute(argv, **kwargs):
            self.assertTrue((self.directory.root / "original-timers.json").exists())
            self.assertEqual(self.disk.read()[-1]["kind"], "intent")
            self.assertEqual(argv[0], "/usr/bin/systemctl")
            calls.append(argv)
            name = "backup" if "backup" in argv[-1] else "renew"
            states[name]["active" if argv[1] == "stop" else "enabled"] = False
            if failure:
                raise RuntimeError("synthetic-committed-reply-lost")
            return b""

        return calls, states, observe, execute

    async def test_timer_freeze_saves_original_before_four_fixed_mutations(self):
        self.seed("FREEZE_TIMERS")
        calls, states, observe, execute = self.timer_boundary()
        coordinator = HostCoordinator(self.spec, self.journal, self.baseline)
        with (
            patch("host_sequence.require_host_authority"),
            patch("host_timer_control.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_timer_control.collect", side_effect=observe),
            patch("host_timer_control._capture", side_effect=execute),
        ):
            result = await coordinator.run_next()
        self.assertEqual(result["status"], "STEP_RECORDED")
        self.assertEqual(len(calls), 4)
        self.assertTrue(
            all(not row["active"] and not row["enabled"] for row in states.values())
        )
        original = parse(self.directory.read("original-timers.json"))
        self.assertTrue(original["backup"]["timer"]["active"])
        self.assertEqual(
            replay(self.spec, self.disk.read()).completed[-1], "FREEZE_TIMERS"
        )

    async def test_timer_unknown_reply_preserves_snapshot_and_cannot_repeat(self):
        self.seed("FREEZE_TIMERS")
        calls, _, observe, execute = self.timer_boundary(failure=True)
        coordinator = HostCoordinator(self.spec, self.journal, self.baseline)
        with (
            patch("host_sequence.require_host_authority"),
            patch("host_timer_control.require_host_authority"),
            patch("host_sequence.collect", new=AsyncMock()),
            patch("host_timer_control.collect", side_effect=observe),
            patch("host_timer_control._capture", side_effect=execute),
        ):
            self.assertEqual((await coordinator.run_next())["status"], "HOLD_UNKNOWN")
            self.assertEqual((await coordinator.run_next())["status"], "HOLD")
        self.assertEqual(len(calls), 1)
        self.assertIsNotNone(self.disk.lock)
        self.assertIsNotNone(replay(self.spec, self.disk.read()).pending)

    async def test_timer_source_drift_stops_before_any_mutation(self):
        self.seed("FREEZE_TIMERS", intent=True)
        calls, _, observe, execute = self.timer_boundary(drift=True)
        with (
            patch("host_timer_control.require_host_authority"),
            patch("host_timer_control.collect", side_effect=observe),
            patch("host_timer_control._capture", side_effect=execute),
        ):
            with self.assertRaises(Exception):
                await freeze_timers(self.spec, self.journal)
        self.assertEqual(calls, [])
        self.assertFalse((self.directory.root / "original-timers.json").exists())

    def network_boundary(self, failure=False):
        web_id, connected, calls = "7" * 64, False, []
        image = parse(self.spec._binding)["frontend_image"]

        async def observe(operation, subject):
            networks = {**ERP_NETWORKS, **({NETWORK: NETWORK_ID} if connected else {})}
            if operation == "inspect":
                facts = {
                    "id": web_id,
                    "name": "/clothinkai-prod-web-20261018",
                    "image": image,
                    "networks": networks,
                }
            elif operation == "web_control_show":
                facts = {
                    "id": web_id,
                    "image": image,
                    "mounts": [
                        {
                            "source": ROOT
                            + "/releases/"
                            + self.spec.data["release_id"]
                            + "/web-control",
                            "destination": "/opt/production",
                            "rw": False,
                        }
                    ],
                }
            elif operation == "dashboard_board":
                facts = {
                    "id": BOARD_ID,
                    "image": BOARD_IMAGE,
                    "networks": {NETWORK: NETWORK_ID},
                    "aliases": ["progress-dashboard"],
                }
            else:
                facts = {
                    "id": NETWORK_ID,
                    "name": NETWORK,
                    "internal": True,
                    "driver": "bridge",
                    "scope": "local",
                    "members": {
                        BOARD_ID: BOARD,
                        **(
                            {web_id: "clothinkai-prod-web-20261018"}
                            if connected
                            else {}
                        ),
                    },
                }
            return {"facts": facts}

        async def execute(argv, **kwargs):
            nonlocal connected
            self.assertTrue((self.directory.root / "web-connect-intent.json").exists())
            self.assertEqual(argv[-4:], ("network", "connect", NETWORK_ID, web_id))
            calls.append(argv)
            connected = True
            if failure:
                raise RuntimeError("synthetic-connect-committed-reply-lost")
            return b""

        return web_id, calls, observe, execute

    async def test_web_connect_is_fixed_and_receipt_follows_full_readback(self):
        self.seed("REPLACE_WEB", intent=True)
        web_id, calls, observe, execute = self.network_boundary()
        with (
            patch("host_web_network.require_host_authority"),
            patch("host_web_network.collect", side_effect=observe),
            patch("host_web_network._capture", side_effect=execute),
        ):
            result = await ensure_web_network(self.spec, self.journal, web_id)
        self.assertEqual(result["status"], "CONNECTED_VERIFIED")
        self.assertEqual(len(calls), 1)
        self.assertTrue(
            parse(self.directory.read("web-connect-result.json"))["connected"]
        )

    async def test_web_connect_committed_unknown_only_observes_next_call(self):
        self.seed("REPLACE_WEB", intent=True)
        web_id, calls, observe, execute = self.network_boundary(failure=True)
        with (
            patch("host_web_network.require_host_authority"),
            patch("host_web_network.collect", side_effect=observe),
            patch("host_web_network._capture", side_effect=execute),
        ):
            first = await ensure_web_network(self.spec, self.journal, web_id)
            second = await ensure_web_network(self.spec, self.journal, web_id)
        self.assertEqual(first["status"], "HOLD_UNKNOWN")
        self.assertEqual(second["status"], "OBSERVED_CONNECTED_HOLD")
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.directory.root / "web-connect-result.json").exists())
        self.assertIsNotNone(self.disk.lock)
