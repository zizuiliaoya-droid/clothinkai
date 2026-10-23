"""Regression fixtures for tool review R1-R3; never opens production inputs."""

import asyncio
import dbm.dumb
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import migrate_067 as migrate
import release_builder as builder
import runtime_guard as guard
import start_067 as start
from schedule_test_fixture import values_for
import test_release_tools as fixtures


class OwnedChild:
    def __init__(self, events, terminate_exits=True):
        self.events = events
        self.returncode = None
        self.wait_entered = asyncio.Event()
        self.terminate_called = asyncio.Event()
        self.exit_event = asyncio.Event()
        self.terminate_exits = terminate_exits

    async def wait(self):
        self.wait_entered.set()
        await self.exit_event.wait()
        self.events.append("exit_confirmed")
        return self.returncode

    def terminate(self):
        self.events.append("terminate")
        self.terminate_called.set()
        if self.terminate_exits:
            self.returncode = -15
            self.exit_event.set()

    def kill(self):
        self.events.append("kill")
        self.returncode = -9
        self.exit_event.set()


class CancellationRegression(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stops_child_before_unlock(self):
        events = []
        child = OwnedChild(events)

        async def sql(query, key):
            events.append("unlock" if "unlock" in query else "lock")
            return True

        conn = Mock(fetchval=AsyncMock(side_effect=sql), close=AsyncMock())
        with patch.object(
            migrate, "connect", new_callable=AsyncMock, return_value=conn
        ), patch.object(migrate, "verify_connection", new_callable=AsyncMock), patch.object(
            migrate.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=child
        ):
            task = asyncio.create_task(migrate.apply({}))
            await child.wait_entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn("terminate", events)
        self.assertIsNotNone(child.returncode)
        self.assertLess(events.index("exit_confirmed"), events.index("unlock"))
        conn.close.assert_awaited_once()

    async def test_cancel_again_during_timeout_cleanup_still_waits_before_unlock(self):
        events = []
        child = OwnedChild(events, terminate_exits=False)

        async def sql(query, key):
            events.append("unlock" if "unlock" in query else "lock")
            return True

        conn = Mock(fetchval=AsyncMock(side_effect=sql), close=AsyncMock())
        with patch.object(
            migrate, "connect", new_callable=AsyncMock, return_value=conn
        ), patch.object(migrate, "verify_connection", new_callable=AsyncMock), patch.object(
            migrate.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=child
        ), patch.object(migrate, "CHILD_TIMEOUT", 0.01), patch.object(
            migrate, "TERMINATE_GRACE", 0.03
        ):
            task = asyncio.create_task(migrate.apply({}))
            await child.terminate_called.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            self.assertNotIn("unlock", events)
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(child.returncode, -9)
        self.assertLess(events.index("terminate"), events.index("kill"))
        self.assertLess(events.index("exit_confirmed"), events.index("unlock"))
        conn.close.assert_awaited_once()

    async def test_cancel_during_spawn_retrieves_and_stops_child(self):
        events = []
        child = OwnedChild(events)
        entered, finish_spawn = asyncio.Event(), asyncio.Event()

        async def spawn(*args, **kwargs):
            entered.set()
            await finish_spawn.wait()
            return child

        async def sql(query, key):
            events.append("unlock" if "unlock" in query else "lock")
            return True

        conn = Mock(fetchval=AsyncMock(side_effect=sql), close=AsyncMock())
        with patch.object(
            migrate, "connect", new_callable=AsyncMock, return_value=conn
        ), patch.object(migrate, "verify_connection", new_callable=AsyncMock), patch.object(
            migrate.asyncio, "create_subprocess_exec", side_effect=spawn
        ):
            task = asyncio.create_task(migrate.apply({}))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertNotIn("unlock", events)
            finish_spawn.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertLess(events.index("exit_confirmed"), events.index("unlock"))
        conn.close.assert_awaited_once()


class HardlinkRegression(unittest.TestCase):
    def test_dist_hardlink_is_rejected_before_output(self):
        fixture = fixtures.BuilderTests(
            methodName="test_deterministic_full_migrations_and_public_only"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        inside = fixture.dist / "assets/app.js"
        outside = fixture.root / "synthetic-outside.js"
        inside.rename(outside)
        os.link(outside, inside)
        self.assertEqual(inside.stat().st_nlink, 2)
        with self.assertRaisesRegex(builder.Rejected, "local_file"):
            fixture.build()
        self.assertFalse((fixture.root / "candidate.tar.gz").exists())

    def test_dependency_hardlink_is_rejected_before_output(self):
        fixture = fixtures.BuilderTests(
            methodName="test_deterministic_full_migrations_and_public_only"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        inside = fixture.dep / "wheelhouse/demo-1.0-py3-none-any.whl"
        outside = fixture.root / "synthetic-outside.whl"
        inside.rename(outside)
        os.link(outside, inside)
        with self.assertRaisesRegex(builder.Rejected, "local_file"):
            fixture.build()
        self.assertFalse((fixture.root / "candidate.tar.gz").exists())

    def test_control_hardlink_is_rejected_before_output(self):
        fixture = fixtures.BuilderTests(
            methodName="test_deterministic_full_migrations_and_public_only"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        controls = fixture.root / "controls"
        controls.mkdir()
        for name in builder.CONTROL + builder.TEMPLATES:
            (controls / name).write_bytes((builder.HERE / name).read_bytes())
        inside = controls / "start_067.py"
        outside = fixture.root / "synthetic-outside-control.py"
        inside.rename(outside)
        os.link(outside, inside)
        with patch.object(builder, "HERE", controls), self.assertRaisesRegex(
            builder.Rejected, "local_file"
        ):
            fixture.build()
        self.assertFalse((fixture.root / "candidate.tar.gz").exists())

    def test_hardlink_created_after_lstat_is_rejected_by_fstat(self):
        with tempfile.TemporaryDirectory(prefix="flow78-fstat-fixture-") as folder:
            path = Path(folder) / "public.txt"
            path.write_bytes(b"public fixture")
            info = path.stat()
            opened = SimpleNamespace(
                st_dev=info.st_dev,
                st_ino=info.st_ino,
                st_mode=info.st_mode,
                st_nlink=2,
                st_size=info.st_size,
            )
            with patch.object(builder.os, "fstat", return_value=opened), self.assertRaisesRegex(
                builder.Rejected, "local_file_changed"
            ):
                builder.read_local(path)


class BeatRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-beat-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "celerybeat-schedule"
        self.expected = {"business-" + str(i): "task" + str(i) for i in range(7)}
        (self.root / "expected.json").write_text(json.dumps(self.expected), encoding="utf-8")
        (self.root / "production-schedule.json").write_bytes(
            json.dumps(self.expected, sort_keys=True).encode()
        )
        schedule = {**start.EXCLUSIONS, **self.expected}
        self.celery = SimpleNamespace(
            conf=SimpleNamespace(beat_schedule={k: {"task": v} for k, v in schedule.items()}),
            start=Mock(),
        )

    def mapped_path(self, value):
        if str(value) == "/opt/release/beat-expected.json":
            return self.root / "expected.json"
        if str(value).startswith("/var/lib/celery"):
            return self.root / str(value).removeprefix("/var/lib/celery").lstrip("/")
        raise AssertionError("unexpected fixture path")

    def dispatch(self, **kwargs):
        with patch.object(start, "Path", side_effect=self.mapped_path), patch.object(
            start.os, "chdir"
        ), patch.object(start.sys, "path", list(sys.path)), patch.dict(
            sys.modules, {"app.core.celery_app": SimpleNamespace(celery_app=self.celery)}
        ):
            start.dispatch("beat", **kwargs)

    def test_unrelated_prefix_file_does_not_start_beat(self):
        (self.root / "celerybeat-schedule.unrelated").write_bytes(b"not a database")
        with self.assertRaises(guard.GuardError):
            self.dispatch(beat_db_format="dbm.dumb")
        self.celery.start.assert_not_called()

    def write_database(self):
        with dbm.dumb.open(str(self.base), "n") as database:
            for key, raw in values_for(self.expected).items():
                database[key] = raw

    def file_digests(self):
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.iterdir()
            if p.is_file()
        }

    def test_valid_existing_database_is_read_only_and_not_recreated(self):
        self.write_database()
        before = self.file_digests()
        with patch.object(pickle, "loads", side_effect=AssertionError("must not unpickle")):
            self.dispatch(beat_db_format="dbm.dumb")
        self.assertEqual(self.file_digests(), before)
        self.celery.start.assert_called_once()

    def test_backup_only_is_rejected_without_creating_main_database(self):
        (self.root / "celerybeat-schedule.bak").write_bytes(b"synthetic backup only")
        before = self.file_digests()
        with self.assertRaisesRegex(guard.GuardError, "schedule_file_layout"):
            self.dispatch(beat_db_format="dbm.dumb")
        self.assertEqual(self.file_digests(), before)
        self.celery.start.assert_not_called()

    def test_missing_dat_is_rejected(self):
        self.write_database()
        (self.root / "celerybeat-schedule.dat").unlink()
        with self.assertRaisesRegex(guard.GuardError, "schedule_file_layout"):
            self.dispatch(beat_db_format="dbm.dumb")
        self.celery.start.assert_not_called()

    def test_corrupt_database_is_rejected_without_rebuild(self):
        self.write_database()
        (self.root / "celerybeat-schedule.dir").write_bytes(b"BROKEN DATABASE")
        before = self.file_digests()
        with self.assertRaises(guard.GuardError):
            self.dispatch(beat_db_format="dbm.dumb")
        self.assertEqual(self.file_digests(), before)
        self.celery.start.assert_not_called()

    def test_corrupt_shelve_stream_is_rejected_without_unpickling(self):
        self.write_database()
        with dbm.dumb.open(str(self.base), "w") as database:
            database[b"entries"] = b"not a pickle"
        before = self.file_digests()
        with self.assertRaisesRegex(guard.GuardError, "schedule_database_unreadable"):
            self.dispatch(beat_db_format="dbm.dumb")
        self.assertEqual(self.file_digests(), before)
        self.celery.start.assert_not_called()

    def test_missing_or_mismatched_reviewed_format_is_rejected(self):
        self.write_database()
        for expected_format in (None, "dbm.gnu", "dbm.unknown"):
            with self.subTest(expected_format=expected_format), self.assertRaises(guard.GuardError):
                self.dispatch(beat_db_format=expected_format)
        self.celery.start.assert_not_called()

    def test_start_permit_binds_reviewed_database_format(self):
        permit = {
            "schema_version": 1,
            "operation": "start",
            "source_commit": "a" * 40,
            "release_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "approval_ref": "synthetic-approval",
            "schema": guard.AFTER,
            "allowed_roles": ["beat"],
            "beat_db_format": "dbm.dumb",
        }
        guard.validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "beat", "dbm.dumb")
        with self.assertRaisesRegex(guard.GuardError, "beat_format_mismatch"):
            guard.validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "beat", "dbm.gnu")


if __name__ == "__main__":
    unittest.main(verbosity=2)
