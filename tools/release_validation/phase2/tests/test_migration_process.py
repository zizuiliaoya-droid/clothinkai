"""Owned fake process/backend lifecycle; no OS process or DB connection."""

import asyncio
from copy import deepcopy
import selectors
import time
import unittest
from unittest.mock import patch
import migration_process
from migration_process import SyntheticTransport, run_guarded_migration
from sql_policy import AFTER, BEFORE, MigrationSpec, canonical


class NoSocketSelector(selectors.SelectSelector):
    def select(self, timeout=None):
        if self.get_map():
            raise AssertionError("IO forbidden in synthetic loop")
        if timeout:
            time.sleep(min(timeout, 0.005))
        return []


class NoSocketLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(selector=NoSocketSelector())

    def _make_self_pipe(self):
        pass

    def _write_to_self(self):
        pass

    def _close_self_pipe(self):
        pass


class NoSocketAsyncCase(unittest.IsolatedAsyncioTestCase):
    def _setupAsyncioRunner(self):
        self._asyncioRunner = asyncio.Runner(debug=True, loop_factory=NoSocketLoop)


class FirstProcessNegative(NoSocketAsyncCase):
    async def test_unconfirmed_backend_is_never_verified(self):
        transport = SyntheticTransport()
        result = await run_guarded_migration(
            MigrationSpec(canonical({"mode": "synthetic"})), transport
        )
        self.assertEqual(result["outcome"], "unknown")


class FakeChild:
    child_id, container_id = "fixture-child-001", "fixture-container-001"

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.exited = asyncio.Event()
        if returncode is not None:
            self.exited.set()
        self.terminates = self.kills = 0
        self.ignore_terminate = self.ignore_kill = False

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def terminate(self):
        self.terminates += 1
        if not self.ignore_terminate:
            self.returncode = -15
            self.exited.set()

    def kill(self):
        self.kills += 1
        if not self.ignore_kill:
            self.returncode = -9
            self.exited.set()


class FakeTransport(SyntheticTransport):
    def __init__(self, spec, proof, returncode=0):
        self.spec, self.proof, self.child = spec, proof, FakeChild(returncode)
        self.calls, self.released = [], False
        self.spawn_started, self.spawn_gate = asyncio.Event(), asyncio.Event()
        self.spawn_gate.set()
        self.backend_state, self.container_state = "absent", "stopped"
        self.backend_identity = deepcopy(proof["identity"])
        self.snapshot_policy = spec.sha256
        self.snapshot_head = AFTER if returncode == 0 else BEFORE
        self.failure = (
            None
            if returncode == 0
            else {"code": "execution_failed", "sqlstate": "55P03"}
        )
        self.release_result = True

    async def acquire_host_lock(self, spec):
        self.calls.append("host_lock")
        return {
            "policy_sha256": spec.sha256,
            "token": "fixture-update-001",
            "exclusive": True,
            "kind": "host-update-lock",
        }

    async def spawn(self, spec, host_lock):
        self.calls.append("spawn")
        self.spawn_started.set()
        await self.spawn_gate.wait()
        return self.child

    async def collect(self, child, spec):
        self.calls.append("collect")
        return {
            "policy_sha256": self.snapshot_policy,
            "identity": deepcopy(self.proof["identity"]),
            "proof": deepcopy(self.proof),
            "post_head": self.snapshot_head,
            "failure": self.failure,
        }

    async def inspect_backend(self, identity, spec):
        self.calls.append("backend")
        return {
            "policy_sha256": spec.sha256,
            "identity": deepcopy(self.backend_identity),
            "state": self.backend_state,
            "after_process_exit": True,
            "owned_lock_absent": self.backend_state == "absent",
        }

    async def inspect_container(self, child, spec):
        self.calls.append("container")
        return {
            "policy_sha256": spec.sha256,
            "container_id": child.container_id,
            "state": self.container_state,
            "after_process_exit": True,
        }

    async def release_host_lock(self, host_lock, spec):
        self.calls.append("release")
        self.released = self.release_result
        return self.release_result


async def fixture_transport(spec, *, complete=True, returncode=0):
    from tests.test_sql_migration_driver import DIALECT, FakeRaw, params, statement
    from sql_migration_driver import SqlMigrationDriver

    driver, raw = SqlMigrationDriver(spec), FakeRaw(spec)
    driver.prepare_connect(DIALECT, params())
    await driver.verify_connection(raw)
    if complete:
        statement(driver, raw)
        raw.heads = [AFTER]
        await driver.verify_postcondition(raw)
    driver.proof["closed"] = True
    return FakeTransport(spec, deepcopy(driver.proof), returncode)


class ProcessTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        from tests.test_sql_migration_driver import fixture_spec

        self.spec = fixture_spec()
        migration_process._HELD.clear()  # fixture isolation only, no live reconciliation API
        self.transport = await fixture_transport(self.spec)

    async def asyncTearDown(self):
        for state in list(migration_process._HELD.values()):
            for task in state["tasks"].values():
                if not task.done():
                    task.cancel()
        migration_process._HELD.clear()
        await asyncio.sleep(0)

    async def test_verified_requires_exact_backend_container_and_process_exit(self):
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "verified")
        self.assertEqual(
            result["owned_exit"], {"process": True, "container": True, "backend": True}
        )
        self.assertEqual(
            self.transport.calls,
            ["host_lock", "spawn", "collect", "container", "backend", "release"],
        )
        self.assertFalse(result["live_enabled"])
        self.assertFalse(result["deployable"])
        self.assertEqual(result["post_head"], AFTER)

    async def test_backend_alive_is_hold_and_no_retry(self):
        self.transport.backend_state = "present"
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(self.transport.released)
        again = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(again["outcome"], "unknown")
        self.assertEqual(self.transport.calls.count("spawn"), 1)

    async def test_pid_reuse_or_start_drift_holds(self):
        self.transport.backend_identity["backend_start"] = "1792500001.000000"
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(self.transport.released)

    async def test_container_running_holds_even_process_exit(self):
        self.transport.container_state = "running"
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertNotIn("release", self.transport.calls)

    async def test_missing_event_evidence_or_extra_connection_holds(self):
        for key, value in (
            ("connections", 2),
            ("connect_attempts", 0),
            ("post_head", None),
            ("closed", False),
        ):
            migration_process._HELD.clear()
            transport = await fixture_transport(self.spec)
            transport.proof[key] = value
            result = await run_guarded_migration(self.spec, transport)
            self.assertEqual(result["outcome"], "unknown", key)
            self.assertFalse(transport.released)

    async def test_policy_mismatch_or_incorrect_gucs_holds(self):
        self.transport.snapshot_policy = "a" * 64
        self.assertEqual(
            (await run_guarded_migration(self.spec, self.transport))["outcome"],
            "unknown",
        )
        migration_process._HELD.clear()
        transport = await fixture_transport(self.spec)
        transport.proof["gucs"]["statement_ms"] = 0
        self.assertEqual(
            (await run_guarded_migration(self.spec, transport))["outcome"], "unknown"
        )

    async def test_failed_known_requires_before_and_quiescence(self):
        transport = await fixture_transport(self.spec, complete=False, returncode=1)
        result = await run_guarded_migration(self.spec, transport)
        self.assertEqual(result["outcome"], "failed_known")
        self.assertTrue(transport.released)
        self.assertEqual(result["failure"]["sqlstate"], "55P03")
        self.assertEqual(result["action"], "HOLD")

    async def test_failed_process_with_after_head_is_unknown(self):
        transport = await fixture_transport(self.spec, complete=False, returncode=1)
        transport.snapshot_head = AFTER
        result = await run_guarded_migration(self.spec, transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(transport.released)

    async def test_cancel_during_spawn_and_repeat_cancel_keeps_owned_child(self):
        transport = await fixture_transport(self.spec, complete=False, returncode=None)
        transport.spawn_gate.clear()
        task = asyncio.create_task(run_guarded_migration(self.spec, transport))
        await transport.spawn_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        transport.spawn_gate.set()
        result = await task
        self.assertTrue(result["cancelled"])
        self.assertEqual(transport.child.terminates, 1)
        self.assertEqual(result["outcome"], "failed_known")
        self.assertTrue(transport.released)

    async def test_cancel_after_child_exit_does_not_return_verified(self):
        original_collect = self.transport.collect
        entered, release = asyncio.Event(), asyncio.Event()

        async def collect(child, spec):
            entered.set()
            await release.wait()
            return await original_collect(child, spec)

        self.transport.collect = collect
        task = asyncio.create_task(run_guarded_migration(self.spec, self.transport))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        result = await task
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(self.transport.released)

    async def test_cancel_during_confirmed_quiescent_unlock_returns_hold(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        original = self.transport.release_host_lock

        async def release(lock, spec):
            entered.set()
            await finish.wait()
            return await original(lock, spec)

        self.transport.release_host_lock = release
        task = asyncio.create_task(run_guarded_migration(self.spec, self.transport))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        finish.set()
        result = await task
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["action"], "HOLD")
        self.assertTrue(result["host_lock_released"])
        self.assertTrue(all(result["owned_exit"].values()))
        self.assertIs(
            migration_process._HELD[self.spec.sha256]["transport"], self.transport
        )

    async def test_timeout_terminate_kill_wait_path(self):
        transport = await fixture_transport(self.spec, complete=False, returncode=None)
        transport.child.ignore_terminate = True
        original_until = migration_process._until
        forced = 0

        async def force_timeout(task, deadline):
            nonlocal forced
            # Synthetic clock seam: force child deadline and terminate grace only.
            if task.get_coro().__qualname__ == "FakeChild.wait" and forced < 2:
                forced += 1
                raise TimeoutError
            return await original_until(task, deadline)

        with patch.object(migration_process, "_until", force_timeout):
            result = await run_guarded_migration(self.spec, transport)
        self.assertEqual(forced, 2)
        self.assertTrue(result["timed_out"])
        self.assertEqual((transport.child.terminates, transport.child.kills), (1, 1))
        self.assertEqual(result["outcome"], "failed_known")

    async def test_child_never_exits_retains_lock_and_handles(self):
        transport = await fixture_transport(self.spec, complete=False, returncode=None)
        transport.child.ignore_terminate = transport.child.ignore_kill = True
        original_until = migration_process._until

        async def force_timeout(task, deadline):
            if task.get_coro().__qualname__ == "FakeChild.wait":
                raise TimeoutError
            return await original_until(task, deadline)

        with patch.object(migration_process, "_until", force_timeout):
            result = await run_guarded_migration(self.spec, transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(transport.released)
        self.assertIs(
            migration_process._HELD[self.spec.sha256]["child"], transport.child
        )
        self.assertFalse(result["owned_exit"]["process"])

    async def test_untrusted_failure_snapshot_not_printed(self):
        self.transport.proof["SECRET_SQL"] = "SECRET_PASSWORD"
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertNotIn("SECRET", str(result))

    async def test_live_transport_or_host_unlock_unknown_never_verified(self):
        self.transport.execution_mode = "live"
        self.assertEqual(
            (await run_guarded_migration(self.spec, self.transport))["outcome"],
            "unknown",
        )
        self.assertEqual(self.transport.calls, [])
        self.transport.execution_mode = "synthetic"
        self.transport.release_result = False
        result = await run_guarded_migration(self.spec, self.transport)
        self.assertEqual(result["outcome"], "unknown")
        self.assertFalse(result["host_lock_released"])
