"""Local failure contracts only; no claim of Linux process or Docker validation."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from contracts import Rejected
from host_drain_control import validate_queue
from host_producer_control import freeze, same_group, terminate
from tests.test_migration_process import NoSocketAsyncCase


def group(state="T"):
    return {
        "pid": 123,
        "start_ticks": 456,
        "pid_namespace": "pid:[123]",
        "cgroup": "/docker/abc",
        "threads": [
            {"tid": 123, "start_ticks": 456, "state": state},
            {"tid": 124, "start_ticks": 457, "state": state},
        ],
    }


class FreezeTests(NoSocketAsyncCase):
    def test_all_thread_identities_and_states_are_required(self):
        same_group(group("S"), group(), frozen=True)
        for kind in (
            "pid_namespace",
            "cgroup",
            "start_ticks",
            "thread",
            "state",
            "missing",
        ):
            changed = group()
            if kind in ("pid_namespace", "cgroup", "start_ticks"):
                changed[kind] = "changed"
            elif kind == "thread":
                changed["threads"][1]["start_ticks"] += 1
            elif kind == "state":
                changed["threads"][1]["state"] = "S"
            else:
                changed["threads"].pop()
            with self.subTest(kind=kind), self.assertRaises(Rejected):
                same_group(group(), changed, frozen=True)

    def test_all_priority_queues_and_eta_unacked_counts_required(self):
        row = {
            "worker": "celery@fixture",
            "active": 0,
            "reserved": 0,
            "scheduled": 0,
            "ready": {
                k: [0, 0, 0, 0] for k in ("default", "backup", "crawler", "report")
            },
            "unacked_hash": 0,
            "unacked_index": 0,
            "priority_steps": [0, 3, 6, 9],
            "broker_transport": "redis",
        }
        validate_queue(row)
        for key in (
            "active",
            "reserved",
            "scheduled",
            "unacked_hash",
            "unacked_index",
            "priority",
        ):
            changed = copy.deepcopy(row)
            if key == "priority":
                changed["ready"]["crawler"][3] = 1
            else:
                changed[key] = 1
            with self.subTest(key=key), self.assertRaises(Rejected):
                validate_queue(changed)

    async def test_freeze_intent_precedes_signal_and_never_sends_continue(self):
        files = {}
        journal = SimpleNamespace(
            directory=SimpleNamespace(create=lambda n, b: files.setdefault(n, b))
        )
        current = {
            "id": "a" * 64,
            "pid": 123,
            "running": True,
            "paused": False,
            "restart_policy": "no",
        }
        signals = []

        def send(fd, sig):
            self.assertIn("api-freeze-intent.json", files)
            signals.append(sig)

        with (
            patch("host_producer_control.require_step"),
            patch("host_migration_control.recorded_facts"),
            patch(
                "host_producer_control.collect",
                AsyncMock(return_value={"facts": current}),
            ),
            patch(
                "host_producer_control.process_group",
                side_effect=[group("S"), group("S"), group()],
            ),
            patch("host_producer_control.os.pidfd_open", return_value=9, create=True),
            patch("host_producer_control.os.close"),
            patch("host_producer_control.signal.SIGSTOP", 19, create=True),
            patch(
                "host_producer_control.signal.pidfd_send_signal",
                side_effect=send,
                create=True,
            ),
        ):
            result = await freeze(None, journal, {"api": "a" * 64})
        self.assertEqual(signals, [19])
        self.assertFalse(result["automatic_resume_allowed"])

    async def test_freeze_unknown_signal_has_no_success_or_automatic_resume(self):
        files = {}
        journal = SimpleNamespace(
            directory=SimpleNamespace(create=lambda n, b: files.setdefault(n, b))
        )
        current = {
            "id": "a" * 64,
            "pid": 123,
            "running": True,
            "paused": False,
            "restart_policy": "no",
        }
        with (
            patch("host_producer_control.require_step"),
            patch("host_migration_control.recorded_facts"),
            patch(
                "host_producer_control.collect",
                AsyncMock(return_value={"facts": current}),
            ),
            patch("host_producer_control.process_group", return_value=group("S")),
            patch("host_producer_control.os.pidfd_open", return_value=9, create=True),
            patch("host_producer_control.os.close"),
            patch("host_producer_control.signal.SIGSTOP", 19, create=True),
            patch(
                "host_producer_control.signal.pidfd_send_signal",
                side_effect=OSError(),
                create=True,
            ) as send,
        ):
            with self.assertRaises(OSError):
                await freeze(None, journal, {"api": "a" * 64})
        send.assert_called_once()
        self.assertIn("api-freeze-intent.json", files)
        self.assertNotIn("api-frozen-observed.json", files)

    async def test_terminate_requires_frozen_proof_before_any_signal(self):
        with (
            patch("host_producer_control.require_step"),
            patch(
                "host_producer_control.verify_frozen",
                AsyncMock(side_effect=Rejected("not_frozen")),
            ),
            patch("host_producer_control.os.pidfd_open", create=True) as opened,
        ):
            with self.assertRaises(Rejected):
                await terminate(None, None, {"api": "a" * 64})
        opened.assert_not_called()
