"""Synthetic OS boundaries: scheduling/receipt contracts, never live evidence."""

from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from contracts import BEFORE, Rejected, canonical, digest
import host_backup_control as control
from tests.test_migration_process import NoSocketAsyncCase

NAME = "20261024T120000Z-" + "a" * 32 + ".dump"
DUMP = hashlib.sha256(b"PGDMP-complete-test-data").hexdigest()
BASELINE = {role: str(index + 1) * 64 for index, role in enumerate(control.ROLES)}
COMPARISON = {key + "_sha256": "a" * 64 for key in (*control.CATALOGS, "rows", "rls")}
COMPARISON.update(pg_version="18.6", head=BEFORE)


class Directory:
    def __init__(self):
        self.files = {}

    def create(self, name, raw):
        if name in self.files:
            raise FileExistsError(name)
        self.files[name] = raw

    def read(self, name, limit):
        value = self.files[name]
        if len(value) > limit:
            raise ValueError("limit")
        return value


def facts():
    return {
        role: {
            "id": identity,
            "image": control.PG_IMAGE if role == "db" else "sha256:" + "b" * 64,
            "paused": False,
            "restarting": False,
            "oom": False,
            "running": role not in ("worker", "beat"),
            "restart_policy": "no",
        }
        for role, identity in BASELINE.items()
    }


def metadata(size, hashed, inode):
    return {
        "bytes": size,
        "sha256": hashed,
        "mtime_ns": 150,
        "ctime_ns": 150,
        "inode": inode,
        "device": 1,
    }


def session_counts():
    return {
        role: {
            "idle": idle,
            "active": 0,
            "idle_in_transaction": 0,
            "unknown": 0,
            "total": idle,
        }
        for role, idle in (
            ("clothing_app", 4),
            ("clothing_bypass", 1),
            ("clothing_archiver", 0),
        )
    }


def session_windows():
    return {
        "stage": "business_quiescent_api_pool_may_be_idle",
        "windows": [
            {"monotonic_ns": tick, "roles": session_counts()}
            for tick in (1, 30 * 10**9 + 1)
        ],
        "all_connections_absent": False,
    }


class HostBackupTests(NoSocketAsyncCase):
    def setUp(self):
        self.spec = SimpleNamespace(
            data={"source_commit": "a" * 40, "release_id": "flow78-" + "a" * 12},
            sha256="f" * 64,
            _binding=canonical({"baseline_sha256": digest(BASELINE)}),
        )
        self.history = [
            {
                "kind": "intent",
                "payload": {"name": "BACKUP", "action_id": "backup-action"},
            }
        ]
        self.journal = SimpleNamespace(directory=Directory(), read=lambda: self.history)
        self.sidecar = {
            "archive_list": True,
            "archive_full_read": True,
            "offhost_verified": True,
            "bytes": 25,
            "sha256": DUMP,
            "existing_objects_changed": False,
        }
        self.before = {
            "backup.lock": metadata(0, hashlib.sha256(b"").hexdigest(), 1),
            "20261023T120000Z-" + "b" * 32 + ".dump": metadata(20, "c" * 64, 2),
        }
        self.after = {
            **deepcopy(self.before),
            NAME: metadata(25, DUMP, 3),
            NAME + ".json": metadata(
                len(canonical(self.sidecar)), digest(self.sidecar), 4
            ),
        }
        self.rows = facts()
        self.mounts = {
            "id": BASELINE["api"],
            "image": self.rows["api"]["image"],
            "mounts": [
                {
                    "source": control.ROOT + "/build/control",
                    "destination": "/opt/production",
                    "rw": False,
                    "type": "bind",
                }
            ],
        }
        self.capture = AsyncMock(
            return_value=canonical(
                {
                    "status": "PRODUCTION_BACKUP_VERIFIED",
                    "bytes": 25,
                    "local_and_offhost": True,
                    "retention_deletion_enabled": False,
                }
            )
        )
        self.mounts["mounts"].append(
            {
                "source": control.ROOT + "/backups",
                "destination": "/run/backups",
                "rw": False,
                "type": "bind",
            }
        )
        self.mocks = {}
        values = {
            "_action": Mock(return_value="backup-action"),
            "_frozen": AsyncMock(
                return_value={"containers": self.rows, "sessions": session_windows()}
            ),
            "_script_pins": Mock(),
            "_mounts": AsyncMock(return_value=self.mounts),
            "_snapshot": Mock(
                side_effect=lambda: (
                    deepcopy(
                        self.before if self.capture.await_count == 0 else self.after
                    ),
                    20 * 1024**3,
                )
            ),
            "_database_snapshot": AsyncMock(return_value=deepcopy(COMPARISON)),
            "_sidecar": Mock(side_effect=lambda _: canonical(self.sidecar)),
            "_capture": self.capture,
            "require_host_authority": Mock(),
            "manual_freeze": Mock(),
        }
        for key, value in values.items():
            context = patch.object(control, key, value)
            context.start()
            self.addCleanup(context.stop)
            self.mocks[key] = value
        self.clock = patch.object(control.time, "time_ns", side_effect=[100, 200])
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def run_backup(self):
        return await control.backup(
            self.spec, self.journal, BASELINE, schema_before_sha256="f" * 64
        )

    async def test_success_one_original_host_call_receipt_bound(self):
        result = await self.run_backup()
        self.assertEqual(result["mode"], "host_observed")
        self.assertEqual(result["dump_sha256"], DUMP)
        self.assertEqual(
            result["receipt_sha256"],
            digest(control.parse(self.journal.directory.files["backup-private.json"])),
        )
        self.assertEqual(
            control.parse(self.journal.directory.files["backup-observed.json"]), result
        )
        self.capture.assert_awaited_once_with(
            ("/usr/bin/python3", control.ROOT + "/build/control/backup.py"),
            timeout=2100,
        )
        self.assertIn("backup-invocation.json", self.journal.directory.files)
        private = control.parse(self.journal.directory.files["backup-private.json"])
        for key in ("freeze_before", "freeze_after"):
            self.assertEqual(private[key]["sessions"], session_windows())
            self.assertEqual(
                private[key]["sessions"]["windows"][0]["roles"]["clothing_app"][
                    "total"
                ],
                4,
            )
            self.assertFalse(private[key]["sessions"]["all_connections_absent"])

    async def test_failed_or_lost_reply_never_retries(self):
        self.capture.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            await self.run_backup()
        with self.assertRaises(FileExistsError):
            await self.run_backup()
        self.assertEqual(self.capture.await_count, 1)
        self.assertNotIn("backup-observed.json", self.journal.directory.files)

    async def test_missing_sidecar_no_observed_receipt(self):
        self.mocks["_sidecar"].side_effect = FileNotFoundError()
        with self.assertRaises(FileNotFoundError):
            await self.run_backup()
        self.assertNotIn("backup-observed.json", self.journal.directory.files)

    async def test_changed_original_file_rejected(self):
        self.after["backup.lock"]["inode"] += 1
        with self.assertRaisesRegex(Rejected, "existing_backup_changed"):
            await self.run_backup()

    async def test_extra_file_rejected(self):
        self.after["unexpected.dump"] = metadata(2, "d" * 64, 5)
        with self.assertRaisesRegex(Rejected, "unique_backup_pair"):
            await self.run_backup()

    async def test_incomplete_r2_read_or_wrong_hash_rejected(self):
        self.sidecar["offhost_verified"] = False
        self.after[NAME + ".json"] = metadata(
            len(canonical(self.sidecar)), digest(self.sidecar), 4
        )
        with self.assertRaisesRegex(Rejected, "backup_sidecar"):
            await self.run_backup()

    async def test_r2_hash_mismatch_rejected(self):
        self.sidecar["sha256"] = "e" * 64
        self.after[NAME + ".json"] = metadata(
            len(canonical(self.sidecar)), digest(self.sidecar), 4
        )
        with self.assertRaisesRegex(Rejected, "backup_readback"):
            await self.run_backup()

    async def test_local_short_read_size_rejected(self):
        self.after[NAME]["bytes"] -= 1
        with self.assertRaisesRegex(Rejected, "backup_readback"):
            await self.run_backup()

    async def test_wrong_mount_blocks_before_invocation(self):
        self.mounts["mounts"][0]["source"] += "-wrong"
        with self.assertRaisesRegex(Rejected, "backup_api_control_mount"):
            await self.run_backup()
        self.capture.assert_not_awaited()

    async def test_overlay_script_mount_blocks_invocation(self):
        self.mounts["mounts"].append(
            {
                "source": "/different.py",
                "destination": "/opt/production/upload_backup.py",
                "rw": False,
                "type": "bind",
            }
        )
        with self.assertRaisesRegex(Rejected, "backup_api_control_mount"):
            await self.run_backup()
        self.capture.assert_not_awaited()

    async def test_wrong_api_id_blocks_before_invocation(self):
        self.mounts["id"] = "9" * 64
        with self.assertRaisesRegex(Rejected, "backup_api_control_mount"):
            await self.run_backup()
        self.capture.assert_not_awaited()

    async def test_stale_new_file_rejected(self):
        self.after[NAME]["mtime_ns"] = 99
        with self.assertRaisesRegex(Rejected, "backup_wall_window"):
            await self.run_backup()

    async def test_source_changed_during_backup_rejected(self):
        self.mocks["_database_snapshot"].side_effect = [
            COMPARISON,
            {**COMPARISON, "rows_sha256": "0" * 64},
        ]
        with self.assertRaisesRegex(Rejected, "backup_database_changed"):
            await self.run_backup()

    async def prepare_restore(self):
        result = await self.run_backup()
        self.history.extend(
            [
                {
                    "kind": "result",
                    "payload": {
                        "completed": True,
                        "action_id": "backup-action",
                        "facts_sha256": digest(result),
                    },
                },
                {
                    "kind": "intent",
                    "payload": {"name": "RESTORE", "action_id": "restore-action"},
                },
            ]
        )
        self.mocks["_action"].return_value = "restore-action"
        self.data = {
            "path": control.ROOT
            + "/releases/"
            + self.spec.data["release_id"]
            + "/drill/data",
            "inode": 30,
            "device": 1,
        }
        self.mocks["_database_snapshot"].side_effect = None
        for key, value in {
            "_new_drill_directory": Mock(return_value=self.data),
            "_drill_identity": AsyncMock(return_value={"id": "9" * 64}),
            "_ready": AsyncMock(),
            "_postgres_uid": AsyncMock(return_value=999),
            "_sql": AsyncMock(return_value=b""),
        }.items():
            context = patch.object(control, key, value)
            context.start()
            self.addCleanup(context.stop)
            self.mocks[key] = value
        self.capture.reset_mock()
        self.capture.side_effect = [b"9" * 64 + b"\n", b"started", b""]
        self.mocks["_snapshot"].side_effect = lambda: (
            deepcopy(self.after),
            20 * 1024**3,
        )

    async def test_restore_success_real_steps_composed_and_bound(self):
        await self.prepare_restore()
        result = await control.restore(self.spec, self.journal, BASELINE)
        self.assertEqual(result["mode"], "host_observed")
        self.assertEqual(
            result["receipt_sha256"],
            digest(control.parse(self.journal.directory.files["restore-private.json"])),
        )
        create = self.capture.await_args_list[0].args[0]
        self.assertIn("--network=none", create)
        self.assertIn("--cap-drop=ALL", create)
        self.assertIn("POSTGRES_HOST_AUTH_METHOD=trust", create)
        self.assertIn("PGDATA=/var/lib/postgresql", create)
        self.assertEqual(
            create[create.index("--tmpfs") + 1], control.restore_drill.SOCKET_TMPFS
        )
        self.assertEqual(
            self.mocks["_drill_identity"].await_args_list[-1].kwargs,
            {"owner": (999, 0)},
        )
        self.assertEqual(
            {v for v in create if v.startswith("--cap-add=")},
            {"--cap-add=CHOWN", "--cap-add=SETUID", "--cap-add=SETGID"},
        )
        self.assertFalse(
            any(v in create for v in ("--privileged", "--rm", "--publish"))
        )
        self.mocks["_sql"].assert_awaited_once_with("9" * 64, control.ROLE_SQL)
        self.assertIn("pg_restore", self.capture.await_args_list[-1].args[0])
        self.assertNotIn("--no-acl", self.capture.await_args_list[-1].args[0])
        private = control.parse(self.journal.directory.files["restore-private.json"])
        self.assertEqual(private["freeze_before"]["sessions"], session_windows())
        self.assertEqual(private["freeze_after"]["sessions"], session_windows())

    async def test_restore_failure_retains_target_without_retry(self):
        await self.prepare_restore()
        self.capture.side_effect = [b"9" * 64, b"started", TimeoutError()]
        with self.assertRaises(TimeoutError):
            await control.restore(self.spec, self.journal, BASELINE)
        self.assertNotIn("restore-observed.json", self.journal.directory.files)
        self.assertIn("restore-created.json", self.journal.directory.files)
        with self.assertRaises(FileExistsError):
            await control.restore(self.spec, self.journal, BASELINE)
        self.assertEqual(self.capture.await_count, 3)

    async def test_restore_data_and_acl_mismatch_fail(self):
        await self.prepare_restore()
        self.mocks["_database_snapshot"].side_effect = [
            COMPARISON,
            {**COMPARISON, "acl_sha256": "e" * 64},
            COMPARISON,
        ]
        with self.assertRaisesRegex(Rejected, "restore_baseline_acl_sha256"):
            await control.restore(self.spec, self.journal, BASELINE)
        self.assertNotIn("restore-observed.json", self.journal.directory.files)

    async def test_backup_journal_hash_tamper_blocks_restore(self):
        await self.prepare_restore()
        self.history[1]["payload"]["facts_sha256"] = "0" * 64
        with self.assertRaisesRegex(Rejected, "restore_backup_journal_binding"):
            await control.restore(self.spec, self.journal, BASELINE)
        self.capture.assert_not_awaited()

    async def test_restore_rows_mismatch_fail(self):
        await self.prepare_restore()
        self.mocks["_database_snapshot"].side_effect = [
            COMPARISON,
            {**COMPARISON, "rows_sha256": "e" * 64},
            COMPARISON,
        ]
        with self.assertRaisesRegex(Rejected, "restore_baseline_rows_sha256"):
            await control.restore(self.spec, self.journal, BASELINE)
        self.assertNotIn("restore-observed.json", self.journal.directory.files)

    async def test_existing_restore_directory_stops_before_create(self):
        await self.prepare_restore()
        self.mocks["_new_drill_directory"].side_effect = FileExistsError()
        with self.assertRaises(FileExistsError):
            await control.restore(self.spec, self.journal, BASELINE)
        self.capture.assert_not_awaited()


class NarrowBoundaryTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.producer = patch(
            "host_producer_control.verify_frozen", AsyncMock(return_value={})
        )
        self.producer.start()
        self.addCleanup(self.producer.stop)

    async def test_frozen_gate_refuses_replaced_original_identity(self):
        spec = SimpleNamespace(
            _binding=canonical({"baseline_sha256": digest(BASELINE)})
        )
        rows = facts()
        rows["api"]["id"] = "9" * 64
        with (
            patch.object(control, "require_host_authority"),
            patch.object(
                control,
                "collect",
                AsyncMock(side_effect=lambda operation, role: {"facts": rows[role]}),
            ),
        ):
            with self.assertRaisesRegex(Rejected, "backup_container_identity"):
                await control._frozen(spec, BASELINE, None)

    async def test_frozen_gate_refuses_business_session(self):
        spec = SimpleNamespace(
            _binding=canonical({"baseline_sha256": digest(BASELINE)})
        )
        rows = facts()
        active = session_counts()
        active["clothing_app"]["active"] = 1
        active["clothing_app"]["total"] += 1
        with (
            patch.object(control, "require_host_authority"),
            patch.object(
                control,
                "collect",
                AsyncMock(side_effect=lambda operation, role: {"facts": rows[role]}),
            ),
            patch.object(
                control,
                "observe_timer",
                AsyncMock(return_value={"timer": {"active": False, "enabled": False}}),
            ),
            patch.object(control, "_sql", AsyncMock(return_value=canonical(active))),
        ):
            with self.assertRaisesRegex(Rejected, "session_not_quiescent"):
                await control._frozen(spec, BASELINE, None)

    async def test_frozen_gate_preserves_both_idle_windows(self):
        spec = SimpleNamespace(
            _binding=canonical({"baseline_sha256": digest(BASELINE)})
        )
        rows = facts()
        with (
            patch.object(control, "require_host_authority"),
            patch.object(
                control,
                "collect",
                AsyncMock(side_effect=lambda operation, role: {"facts": rows[role]}),
            ),
            patch.object(
                control,
                "observe_timer",
                AsyncMock(return_value={"timer": {"active": False, "enabled": False}}),
            ),
            patch.object(
                control, "two_idle_windows", AsyncMock(return_value=session_windows())
            ) as windows,
        ):
            result = await control._frozen(spec, BASELINE, None)
        windows.assert_awaited_once_with(BASELINE["db"])
        self.assertEqual(result["sessions"], session_windows())
        self.assertEqual(result["containers"], rows)

    async def test_rls_checks_real_app_privileges_and_visibility(self):
        app = canonical({"usr": "clothing_app", "super": False, "bypass": False})
        with (
            patch.object(control, "_sql", AsyncMock(side_effect=[app, b"e" * 64])),
            patch.object(control, "_query_hash", AsyncMock(return_value="a" * 64)),
        ):
            with self.assertRaisesRegex(Rejected, "restore_rls_visibility"):
                await control._rls("a" * 64, [])
        with patch.object(
            control,
            "_sql",
            AsyncMock(
                return_value=canonical(
                    {"usr": "clothing_app", "super": False, "bypass": True}
                )
            ),
        ):
            with self.assertRaisesRegex(Rejected, "restore_app_role"):
                await control._rls("a" * 64, [])

    async def test_actual_app_connection_is_used(self):
        with patch.object(control, "_capture", AsyncMock(return_value=b"ok")) as call:
            await control._sql("a" * 64, b"SELECT 1;", role="clothing_app")
        self.assertIn("--username=clothing_app", call.await_args.args[0])
        self.assertNotIn(b"SET ROLE", call.await_args.kwargs["stdin"])

    async def test_wrong_restore_mount_or_network_rejected(self):
        data = {"path": control.ROOT + "/releases/flow78-aaaaaaaaaaaa/drill/data"}
        row = {
            "id": "9" * 64,
            "image": control.PG_IMAGE,
            "network": "host",
            "ports": {},
            "mounts": [],
        }
        with (
            patch.object(control, "_mounts", AsyncMock(return_value=row)),
            patch.object(control, "_data_identity"),
        ):
            with self.assertRaisesRegex(Rejected, "restore_container_isolation"):
                await control._drill_identity("9" * 64, data, NAME)

    async def test_restore_requires_exact_socket_tmpfs_and_owner_stage(self):
        data = {"path": control.ROOT + "/releases/flow78-aaaaaaaaaaaa/drill/data"}
        original = {
            "id": "9" * 64,
            "image": control.PG_IMAGE,
            "network": "none",
            "ports": {},
            "tmpfs": {
                "/var/run/postgresql": control.restore_drill.SOCKET_TMPFS.split(":", 1)[
                    1
                ]
            },
            "mounts": [
                {
                    "source": data["path"],
                    "destination": "/var/lib/postgresql",
                    "rw": True,
                    "type": "bind",
                },
                {
                    "source": control.ROOT + "/backups/" + NAME,
                    "destination": "/input/database.dump",
                    "rw": False,
                    "type": "bind",
                },
                {
                    "source": "",
                    "destination": "/var/run/postgresql",
                    "rw": True,
                    "type": "tmpfs",
                },
            ],
        }
        with (
            patch.object(
                control, "_mounts", AsyncMock(return_value=deepcopy(original))
            ),
            patch.object(control, "_data_identity") as owner,
        ):
            result = await control._drill_identity("9" * 64, data, NAME, owner=(999, 0))
            owner.assert_called_once_with(data, owner=(999, 0))
            self.assertEqual(
                result["data_owner"], {"uid": 999, "gid": 0, "mode": 0o700}
            )
        for kind in ("missing", "options", "bind", "readonly"):
            changed = deepcopy(original)
            if kind == "missing":
                changed["mounts"].pop()
            elif kind == "options":
                changed["tmpfs"] = {"/var/run/postgresql": "rw,size=1g"}
            elif kind == "bind":
                changed["mounts"][-1]["type"] = "bind"
            else:
                changed["mounts"][-1]["rw"] = False
            with (
                self.subTest(kind=kind),
                patch.object(control, "_mounts", AsyncMock(return_value=changed)),
                patch.object(control, "_data_identity") as owner,
                self.assertRaisesRegex(Rejected, "restore_container_isolation"),
            ):
                await control._drill_identity("9" * 64, data, NAME)
            owner.assert_not_called()

    def test_data_owner_transition_preserves_private_outer_directory(self):
        data = {"path": "/fixture/drill/data", "inode": 30, "device": 1}
        root = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o40700)
        for uid in (0, 999):
            info = SimpleNamespace(
                st_uid=uid, st_gid=0, st_mode=0o40700, st_ino=30, st_dev=1
            )
            with (
                patch.object(control, "_directory", return_value=8),
                patch.object(control.os, "O_DIRECTORY", 0, create=True),
                patch.object(control.os, "O_NOFOLLOW", 0, create=True),
                patch.object(control.os, "open", return_value=9),
                patch.object(control.os, "close"),
                patch.object(control.os, "fstat", side_effect=[root, info]),
            ):
                control._data_identity(data, owner=(uid, 0))
        for target, key, value in (
            ("outer", "st_uid", 999),
            ("outer", "st_gid", 999),
            ("outer", "st_mode", 0o40755),
            ("data", "st_uid", 0),
            ("data", "st_gid", 999),
            ("data", "st_mode", 0o40755),
            ("data", "st_ino", 31),
        ):
            outer = deepcopy(root)
            info = SimpleNamespace(
                st_uid=999, st_gid=0, st_mode=0o40700, st_ino=30, st_dev=1
            )
            setattr(outer if target == "outer" else info, key, value)
            with (
                self.subTest(target=target, key=key),
                patch.object(control, "_directory", return_value=8),
                patch.object(control.os, "O_DIRECTORY", 0, create=True),
                patch.object(control.os, "O_NOFOLLOW", 0, create=True),
                patch.object(control.os, "open", return_value=9),
                patch.object(control.os, "close"),
                patch.object(control.os, "fstat", side_effect=[outer, info]),
                self.assertRaises(Rejected),
            ):
                control._data_identity(data, owner=(999, 0))

    async def test_snapshot_global_timeout_applies(self):
        with patch.object(
            control, "_database_snapshot_body", AsyncMock(side_effect=TimeoutError())
        ):
            with self.assertRaises(TimeoutError):
                await control._database_snapshot("a" * 64)

    def test_original_public_script_and_role_pins_match(self):
        root = Path(__file__).resolve().parents[2]
        for name, value in (
            ("production-infra-1018/backup.py", control.BACKUP_SHA),
            ("production-infra-1018/upload_backup.py", control.UPLOAD_SHA),
        ):
            self.assertEqual(
                hashlib.sha256((root / name).read_bytes()).hexdigest(), value
            )
        self.assertEqual(
            (root / "app-trial-infra-1018/bootstrap-roles.sql").read_bytes(),
            control.ROLE_SQL,
        )

    def test_complete_multiset_hash_uses_all_rows(self):
        query = control._hash_query("SELECT * FROM public.promotion")
        self.assertIn("row_to_json(q)", query)
        self.assertIn("string_agg(h,'' ORDER BY h)", query)
        self.assertNotIn("DISTINCT", query)
        self.assertNotIn("LIMIT", query)


if __name__ == "__main__":
    unittest.main()
