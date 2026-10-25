"""Idle pools are permitted only before STOP_API; no real database is accessed."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

from host_session_contract import ROLES, two_idle_windows, validate_sessions
from tests.test_migration_process import NoSocketAsyncCase


def idle_counts():
    return {
        role: {
            "idle": count,
            "active": 0,
            "idle_in_transaction": 0,
            "unknown": 0,
            "total": count,
        }
        for role, count in zip(ROLES, (4, 1, 0))
    }


class SessionTests(NoSocketAsyncCase):
    async def test_real_shape_idle_pools_are_counted_not_converted_to_zero(self):
        row = idle_counts()
        self.assertEqual(validate_sessions(row), row)
        self.assertEqual(sum(v["total"] for v in row.values()), 5)
        with self.assertRaises(Exception):
            validate_sessions(row, require_absent=True)

    async def test_all_connections_zero_only_passes_post_api_gate(self):
        row = {
            role: {key: 0 for key in counts} for role, counts in idle_counts().items()
        }
        self.assertEqual(validate_sessions(row, require_absent=True), row)

    async def test_active_transaction_unknown_and_count_laundering_fail(self):
        for key in ("active", "idle_in_transaction", "unknown"):
            row = deepcopy(idle_counts())
            row[ROLES[0]][key] = 1
            row[ROLES[0]]["total"] += 1
            with self.assertRaises(Exception):
                validate_sessions(row)
        row = idle_counts()
        row[ROLES[0]]["total"] = 0
        with self.assertRaises(Exception):
            validate_sessions(row)

    async def test_two_windows_preserve_full_counts_and_true_absence_status(self):
        first, second = idle_counts(), idle_counts()
        second[ROLES[0]]["idle"] = second[ROLES[0]]["total"] = 3
        with (
            patch(
                "host_session_contract.observe_sessions", side_effect=[first, second]
            ),
            patch("host_session_contract.asyncio.sleep", new=AsyncMock()) as sleep,
            patch(
                "host_session_contract.time.monotonic_ns",
                side_effect=[10**9, 31 * 10**9],
            ),
        ):
            result = await two_idle_windows("a" * 64)
        sleep.assert_awaited_once_with(30)
        self.assertFalse(result["all_connections_absent"])
        self.assertEqual(result["windows"][0]["roles"], first)
        self.assertEqual(result["windows"][1]["roles"], second)
