"""Fixed dashboard reader identity; synthetic transport, no Docker or HTTP."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_contract as board
import host_gate_control as gate
from contracts import Rejected, canonical


def complete(coroutine):
    """All mocked awaits finish inline; forbid any real event-loop IO."""
    try:
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        raise AssertionError("unexpected asynchronous IO in synthetic reader test")
    finally:
        coroutine.close()


class DashboardReaderTests(unittest.TestCase):
    def setUp(self):
        self.network = {
            "id": board.NETWORK_ID,
            "name": board.NETWORK,
            "internal": True,
            "driver": "bridge",
            "scope": "local",
            "members": {board.BOARD_ID: board.BOARD, board.OLD_WEB_ID: board.WEB},
        }
        self.container = {
            "id": board.BOARD_ID,
            "image": board.BOARD_IMAGE,
            "networks": {board.NETWORK: board.NETWORK_ID},
            "aliases": [board.BOARD, "progress-dashboard"],
        }
        self.snapshot = {
            "received_at": "2026-10-25T00:00:00Z",
            "generated_at": "2026-10-25T00:00:00Z",
            "received_epoch": 1792886400,
            "generated_epoch": 1792886400,
            "received_age_seconds": 1,
            "generated_age_seconds": 1,
            "schema_valid": True,
        }
        self.expected_argv = gate.DOCKER + (
            "exec",
            "--user",
            "10001:10001",
            "-i",
            board.BOARD_ID,
            "python",
            "-I",
            "-B",
            "-",
        )

    async def collect(self, operation, subject):
        self.assertEqual(subject, "web")
        self.assertIn(operation, ("dashboard_board", "dashboard_network"))
        return {
            "facts": self.container if operation == "dashboard_board" else self.network
        }

    def test_private_owner_read_requires_fixed_nonroot_uid_and_gid(self):
        async def owner_only(argv, stdin, timeout):
            # cap_drop ALL root cannot bypass a 10001-owned private file.
            if argv != self.expected_argv:
                raise Rejected("host_operation_failed")
            self.assertEqual(stdin, gate.BOARD_PROBE)
            self.assertEqual(timeout, 15)
            return canonical(self.snapshot)

        with (
            patch.object(gate, "collect", side_effect=self.collect),
            patch.object(gate, "_capture", side_effect=owner_only) as capture,
            patch.object(
                gate.asyncio,
                "to_thread",
                AsyncMock(return_value={"dashboard_routes_verified": True}),
            ),
        ):
            result = complete(gate.dashboard_state(board.OLD_WEB_ID))
            self.assertEqual(result["snapshot"], self.snapshot)
            capture.assert_awaited_once_with(
                self.expected_argv, stdin=gate.BOARD_PROBE, timeout=15
            )

    def test_permission_denied_holds_without_root_retry_or_public_success(self):
        with (
            patch.object(gate, "collect", side_effect=self.collect),
            patch.object(
                gate,
                "_capture",
                AsyncMock(side_effect=Rejected("host_operation_failed")),
            ) as capture,
            patch.object(gate.asyncio, "to_thread", AsyncMock()) as public,
        ):
            with self.assertRaisesRegex(Rejected, "host_operation_failed"):
                complete(gate.dashboard_state(board.OLD_WEB_ID))
            capture.assert_awaited_once_with(
                self.expected_argv, stdin=gate.BOARD_PROBE, timeout=15
            )
            public.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
