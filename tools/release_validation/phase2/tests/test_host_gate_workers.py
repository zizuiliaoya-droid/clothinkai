"""Simulated /proc transitions during graceful reload; no process mutations."""

from unittest.mock import AsyncMock, patch

from contracts import Rejected
from host_gate_control import nginx_workers, wait_old_workers

from tests.test_migration_process import NoSocketAsyncCase


MASTER = {
    "pid": 100,
    "start_ticks": 10,
    "state": "S",
    "ppid": 1,
    "command": b"nginx: master process nginx -c /opt/production/nginx-active.conf",
}
ORIGINAL = {
    "master": {"pid": 100, "start_ticks": 10},
    "workers": [{"pid": 101, "start_ticks": 11}],
}


class GateWorkerTests(NoSocketAsyncCase):
    async def test_old_worker_can_become_zombie_before_disappearing(self):
        old = {
            "pid": 101,
            "start_ticks": 11,
            "ppid": 100,
            "state": "S",
            "command": b"nginx: worker process",
        }
        observations = [old, {**old, "state": "Z", "command": b""}, None]
        reads = []
        final = {
            "master": ORIGINAL["master"],
            "workers": [{"pid": 102, "start_ticks": 12}],
        }

        def proc(pid):
            if pid == 100:
                return MASTER
            self.assertEqual(pid, 101)
            reads.append(pid)
            return observations.pop(0)

        def new_workers(pid):
            self.assertEqual(observations, [])
            return final

        with (
            patch("host_gate_control._proc", side_effect=proc),
            patch(
                "host_gate_control.nginx_workers", side_effect=new_workers
            ) as snapshot,
            patch("host_gate_control.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            self.assertEqual(await wait_old_workers(ORIGINAL), final)
        self.assertEqual(len(reads), 3)
        self.assertEqual(sleep.await_count, 2)
        snapshot.assert_called_once_with(100)

    async def test_master_identity_or_start_change_is_rejected_before_snapshot(self):
        for row, code in (
            (None, "nginx_master_identity"),
            ({**MASTER, "state": "Z"}, "nginx_master_identity"),
            ({**MASTER, "command": b"unrelated"}, "nginx_master_identity"),
            ({**MASTER, "start_ticks": 99}, "nginx_master_changed"),
        ):
            with (
                self.subTest(code=code),
                patch("host_gate_control._proc", return_value=row),
                patch("host_gate_control.nginx_workers") as snapshot,
                self.assertRaisesRegex(Rejected, code),
            ):
                await wait_old_workers(ORIGINAL)
            snapshot.assert_not_called()

    async def test_still_existing_old_worker_keeps_bounded_wait(self):
        with (
            patch(
                "host_gate_control._proc", side_effect=[MASTER, ORIGINAL["workers"][0]]
            ),
            patch("host_gate_control.time.monotonic", side_effect=[0, 121]),
            patch("host_gate_control.nginx_workers") as snapshot,
            self.assertRaisesRegex(Rejected, "nginx_old_workers_not_drained"),
        ):
            await wait_old_workers(ORIGINAL)
        snapshot.assert_not_called()

    def test_new_snapshot_still_rejects_unknown_live_child(self):
        with (
            patch("host_gate_control._nginx_master", return_value=MASTER),
            patch("host_gate_control.os.O_NOFOLLOW", 0, create=True),
            patch("host_gate_control.os.open", return_value=9),
            patch("host_gate_control.os.read", return_value=b"102"),
            patch("host_gate_control.os.close"),
            patch(
                "host_gate_control._proc",
                return_value={
                    "pid": 102,
                    "start_ticks": 12,
                    "ppid": 100,
                    "state": "S",
                    "command": b"unrelated live child",
                },
            ),
            self.assertRaisesRegex(Rejected, "nginx_child_identity"),
        ):
            nginx_workers(100)
