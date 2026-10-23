"""Synthetic Git/dist/pins only; no Docker, network, cloud, or production inputs."""

import asyncio
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import migrate_067 as migrate
import release_builder as builder
import runtime_guard as guard
import start_067 as start


def pin(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        schedule = {
            **builder.MAINTENANCE,
            **{"business-" + str(i): "app.tasks.fixture.task" + str(i) for i in range(7)},
        }
        sources = {
            "backend/app/main.py": b"APP = 'committed fixture'\n",
            "backend/app/core/celery_app.py": (
                "celery_app.conf.beat_schedule = "
                + repr({k: {"task": v} for k, v in schedule.items()})
                + "\n"
            ).encode(),
            "backend/alembic/env.py": b"# synthetic migration environment; never executed\n",
            "backend/alembic/versions/066.py": b"revision = '066_platform_metric_setting'\ndown_revision = None\n",
            "backend/alembic/versions/067.py": b"revision = '067_retro_completion'\ndown_revision = '066_platform_metric_setting'\n",
            "backend/alembic.ini": b"[alembic]\nscript_location = %(here)s/alembic\n",
            "backend/requirements.txt": b"demo==1.0\n",
            "frontend/package-lock.json": b'{"lockfileVersion":3}\n',
            "backend/.env": b"SYNTHETIC_EXCLUDED_ENV\n",
            "github-config.txt": b"ghp_" + b"x" * 36,
            "backend/tests/test_fixture.py": b"TEST_NOT_RELEASED = True\n",
        }
        for name, raw in sources.items():
            self.put(name, raw)
        self.commit()
        self.dist = self.root / "dist"
        self.dep = self.root / "deps"
        self.dist.mkdir()
        (self.dist / "assets").mkdir()
        self.dep.mkdir()
        (self.dep / "wheelhouse").mkdir()
        front = {
            "index.html": b'<script src="/assets/app.js"></script>',
            "assets/app.js": b'const API="https://47.110.76.9";',
        }
        wheel = b"SYNTHETIC_WHEEL_FIXTURE_NOT_INSTALLABLE"
        deps = {
            "wheelhouse/demo-1.0-py3-none-any.whl": wheel,
            "requirements-linux.lock": (
                "demo==1.0 --hash=sha256:" + builder.digest(wheel) + "\n"
            ).encode(),
        }
        for name, raw in front.items():
            (self.dist / name).write_bytes(raw)
        for name, raw in deps.items():
            (self.dep / name).write_bytes(raw)
        self.pins = {
            "schema_version": 1,
            "source_commit": self.head,
            "source_tree": self.tree,
            "from_revision": builder.BEFORE,
            "target_revision": builder.AFTER,
            "origin": builder.ORIGIN,
            "base_images": {
                n: {"local_tag": "clothinkai-trial-" + n + "-base:20261018", "image_id": v}
                for n, v in builder.BASE_IDS.items()
            },
            "backend_requirements_sha256": builder.digest(sources["backend/requirements.txt"]),
            "migration_sha256": builder.digest(sources["backend/alembic/versions/067.py"]),
            "dependencies": {
                "source_requirements_sha256": builder.digest(sources["backend/requirements.txt"]),
                "closure_reviewed": True,
                "files": {n: pin(v) for n, v in deps.items()},
            },
            "frontend": {
                "source_commit": self.head,
                "package_lock_sha256": builder.digest(sources["frontend/package-lock.json"]),
                "node_version": "24.14.0",
                "npm_version": "11.9.0",
                "api_origin": builder.ORIGIN,
                "files": {n: pin(v) for n, v in front.items()},
            },
            "controls": {
                n: pin((builder.HERE / n).read_bytes()) for n in builder.CONTROL + builder.TEMPLATES
            },
        }

    def git(self, *args, stdin=None):
        proc = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=" + str(self.root / "no-hooks"),
                "-C",
                str(self.repo),
                *args,
            ],
            input=stdin,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode(errors="replace"))
        return proc.stdout.decode().strip()

    def put(self, name, raw):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "synthetic fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def refresh_source_pins(self):
        self.commit()
        self.pins["source_commit"] = self.head
        self.pins["source_tree"] = self.tree
        self.pins["frontend"]["source_commit"] = self.head

    def build(self, name="candidate.tar.gz"):
        return builder.build(self.repo, self.head, self.pins, self.dist, self.dep, self.root / name)

    def reject(self, code):
        with self.assertRaisesRegex(builder.Rejected, "^" + code + "$"):
            self.build()
        self.assertFalse((self.root / "candidate.tar.gz").exists())

    def test_deterministic_full_migrations_and_public_only(self):
        one, two = self.build(), self.build("second.tar.gz")
        self.assertEqual(one["sha256"], two["sha256"])
        self.assertFalse(one["deployable"])
        with tarfile.open(self.root / "candidate.tar.gz") as archive:
            names = archive.getnames()
            for required in (
                "backend/alembic/versions/066.py",
                "backend/alembic/versions/067.py",
                "backend/alembic/env.py",
                "backend/alembic.ini",
                "control/start_067.py",
                "control/schedule_pickle_guard.py",
                "control/migrate_067.py",
            ):
                self.assertIn(required, names)
            for denied in (
                "backend/.env",
                "github-config.txt",
                "backend/tests/test_fixture.py",
                "frontend/package-lock.json",
            ):
                self.assertNotIn(denied, names)
            manifest = json.loads(archive.extractfile("release-manifest.json").read())
            schema = json.loads((builder.HERE / "release-manifest.schema.json").read_bytes())
            self.assertEqual(set(manifest), set(schema["required"]))
            self.assertEqual(set(manifest["pins"]), set(schema["$defs"]["pins"]["required"]))
            self.assertEqual(
                set(manifest["pins"]["controls"]),
                set(schema["$defs"]["pins"]["properties"]["controls"]["required"]),
            )
            self.assertIsNotNone(
                re.fullmatch(
                    schema["$defs"]["pins"]["properties"]["frontend"]["properties"]["node_version"][
                        "pattern"
                    ],
                    "24.14.0",
                )
            )
            self.assertFalse(manifest["deployable"])
            self.assertTrue(manifest["compatibility"]["preserve_certbot_metadata"])
            self.assertFalse(manifest["compatibility"]["implicit_migration"])
            for name, meta in manifest["files"].items():
                self.assertEqual(meta, pin(archive.extractfile(name).read()))

    def test_reads_committed_objects_not_dirty_worktree(self):
        self.put("backend/app/main.py", b"-----BEGIN PRIVATE KEY-----\nDIRTY_NEVER_READ\n")
        self.build()
        with tarfile.open(self.root / "candidate.tar.gz") as archive:
            self.assertEqual(
                archive.extractfile("backend/app/main.py").read(), b"APP = 'committed fixture'\n"
            )

    def test_refuses_unfrozen_commit(self):
        self.pins["source_commit"] = "HEAD"
        self.reject("unfrozen_git_identity")

    def test_refuses_wrong_tree(self):
        self.pins["source_tree"] = "a" * 40
        self.reject("tree_mismatch")

    def test_refuses_wrong_frontend_commit(self):
        self.pins["frontend"]["source_commit"] = "b" * 40
        self.reject("dist_source_mismatch")

    def test_refuses_unreviewed_dependency_closure(self):
        self.pins["dependencies"]["closure_reviewed"] = False
        self.reject("dependency_review_missing")

    def test_refuses_unknown_pin_field(self):
        self.pins["Authorization"] = "synthetic token"
        self.reject("unexpected_fields")

    def test_refuses_missing_control_pin(self):
        del self.pins["controls"]["migrate_067.py"]
        self.reject("control_allowlist")

    def test_refuses_changed_control_pin(self):
        self.pins["controls"]["start_067.py"]["sha256"] = "0" * 64
        self.reject("control_pin_mismatch")

    def test_refuses_changed_template_pin(self):
        self.pins["controls"]["Dockerfile.backend.offline.in"]["sha256"] = "0" * 64
        self.reject("control_pin_mismatch")

    def test_refuses_changed_base(self):
        self.pins["base_images"]["python"]["image_id"] = "sha256:" + "0" * 64
        self.reject("base_image_pin")

    def test_refuses_secret_file_inside_allowed_tree(self):
        self.put("backend/app/secrets.json", b'{"fixture":true}')
        self.refresh_source_pins()
        self.reject("private_path")

    def test_refuses_private_key_literal(self):
        self.put("backend/app/main.py", b"KEY='-----BEGIN PRIVATE KEY-----'\n")
        self.refresh_source_pins()
        self.reject("credential_literal")

    def test_refuses_git_symlink_without_os_symlink_privilege(self):
        oid = self.git("hash-object", "-w", "--stdin", stdin=b"../../outside.py")
        self.git("update-index", "--add", "--cacheinfo", "120000," + oid + ",backend/app/link.py")
        self.git("commit", "--quiet", "-m", "synthetic symlink")
        self.head = self.git("rev-parse", "HEAD")
        self.pins["source_commit"] = self.head
        self.pins["source_tree"] = self.git("rev-parse", "HEAD^{tree}")
        self.pins["frontend"]["source_commit"] = self.head
        self.reject("git_link_or_nonfile")

    def test_refuses_new_migration_head(self):
        self.put(
            "backend/alembic/versions/068.py",
            b"revision='068_unapproved'\ndown_revision='067_retro_completion'\n",
        )
        self.refresh_source_pins()
        self.reject("migration_heads")

    def test_refuses_missing_migration_parent(self):
        (self.repo / "backend/alembic/versions/066.py").unlink()
        self.refresh_source_pins()
        self.reject("migration_parent_missing")

    def test_refuses_changed_migration_contents(self):
        self.put(
            "backend/alembic/versions/067.py",
            b"revision='067_retro_completion'\ndown_revision='066_platform_metric_setting'\n# changed\n",
        )
        self.refresh_source_pins()
        self.reject("migration_hash_mismatch")

    def test_refuses_dist_tamper(self):
        (self.dist / "assets/app.js").write_bytes(b"tampered")
        self.reject("disk_pin_mismatch")

    def test_refuses_extra_dist_file(self):
        (self.dist / "extra.js").write_bytes(b"extra")
        self.reject("unexpected_disk_file")

    def test_refuses_missing_html_asset(self):
        raw = b'<script src="/assets/missing.js"></script>'
        (self.dist / "index.html").write_bytes(raw)
        self.pins["frontend"]["files"]["index.html"] = pin(raw)
        self.reject("missing_html_asset")

    def test_refuses_external_html_asset(self):
        raw = b'<script src="https://example.invalid/remote.js"></script>'
        (self.dist / "index.html").write_bytes(raw)
        self.pins["frontend"]["files"]["index.html"] = pin(raw)
        self.reject("external_html_asset")

    def test_refuses_case_colliding_pin_paths(self):
        self.pins["frontend"]["files"]["assets/App.js"] = self.pins["frontend"]["files"][
            "assets/app.js"
        ]
        self.reject("case_collision")

    def test_refuses_wrong_origin_even_if_repinned(self):
        raw = b'const API="http://localhost:3000";'
        (self.dist / "assets/app.js").write_bytes(raw)
        self.pins["frontend"]["files"]["assets/app.js"] = pin(raw)
        self.reject("frontend_origin_mismatch")

    def test_refuses_missing_wheel_hash_closure(self):
        raw = b"demo==1.0 --hash=sha256:" + b"0" * 64 + b"\n"
        (self.dep / "requirements-linux.lock").write_bytes(raw)
        self.pins["dependencies"]["files"]["requirements-linux.lock"] = pin(raw)
        self.reject("wheel_hash_closure")

    def test_refuses_network_dependency_directive(self):
        raw = b"--index-url https://example.invalid\n"
        (self.dep / "requirements-linux.lock").write_bytes(raw)
        self.pins["dependencies"]["files"]["requirements-linux.lock"] = pin(raw)
        self.reject("offline_lock_syntax")

    def test_refuses_output_overwrite(self):
        self.build()
        old = (self.root / "candidate.tar.gz").read_bytes()
        with self.assertRaisesRegex(builder.Rejected, "output_exists"):
            self.build()
        self.assertEqual((self.root / "candidate.tar.gz").read_bytes(), old)

    def test_combined_bundle_member_limit(self):
        with patch.object(builder, "MAX_FILES", 17):
            self.reject("bundle_file_limit")

    def test_paths_fail_closed(self):
        for name in (
            "../secret",
            "a/../secret",
            "/absolute",
            "C:/secret",
            "a\\b",
            "a//b",
            "a/.env",
            "a/secret.pem",
            "a/secrets.json",
            "a/private.local.json",
        ):
            with self.subTest(name=name), self.assertRaises(builder.Rejected):
                builder.safe_name(name)

    def test_duplicate_json_rejected(self):
        with self.assertRaisesRegex(builder.Rejected, "duplicate_json_key"):
            builder.json_bytes(b'{"a":1,"a":2}')


def synthetic_config():
    config = {name: "synthetic" for name in guard.REQUIRED}
    config.update({"ENVIRONMENT": "production", "CORS_ALLOWED_ORIGINS": guard.ORIGIN})
    for key, role, driver in (
        ("DATABASE_URL_APP", "clothing_app", "asyncpg"),
        ("DATABASE_URL_BYPASS", "clothing_bypass", "asyncpg"),
        ("DATABASE_URL_SYNC", "clothing_bypass", "psycopg2"),
        ("DATABASE_URL_ARCHIVER", "clothing_archiver", "asyncpg"),
    ):
        config[key] = f"postgresql+{driver}://{role}:synthetic@prod-db:5432/{guard.DATABASE}"
    for key, index in (
        ("REDIS_URL_CACHE", 0),
        ("REDIS_URL_CELERY_BROKER", 1),
        ("REDIS_URL_CELERY_BACKEND", 2),
    ):
        config[key] = f"redis://prod-redis:6379/{index}"
    return config


class RuntimeTests(unittest.TestCase):
    def test_config_rejects_wrong_database_role_and_env_injection(self):
        valid = synthetic_config()
        self.assertEqual(guard.validate_config(valid), valid)
        for key, value in (
            ("DATABASE_URL_APP", valid["DATABASE_URL_APP"].replace("clothing_app", "root")),
            ("PYTHONPATH", "/untrusted"),
            ("CORS_ALLOWED_ORIGINS", "http://localhost"),
        ):
            with self.subTest(key=key), self.assertRaises(guard.GuardError):
                guard.validate_config({**valid, key: value})

    def test_release_rejects_old_revision_or_other_source(self):
        valid = {
            "source_commit": "a" * 40,
            "source_tree": "b" * 40,
            "schema": guard.AFTER,
            "origin": guard.ORIGIN,
            "phase": "candidate-only",
        }
        guard.validate_release(valid, "a" * 40)
        for changed in ({**valid, "schema": guard.BEFORE}, {**valid, "source_commit": "c" * 40}):
            with self.assertRaises(guard.GuardError):
                guard.validate_release(changed, "a" * 40)

    def test_start_permit_cannot_authorize_worker_with_api_only(self):
        permit = {
            "schema_version": 1,
            "operation": "start",
            "source_commit": "a" * 40,
            "release_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "approval_ref": "synthetic-approval",
            "schema": guard.AFTER,
            "allowed_roles": ["api"],
            "beat_db_format": None,
        }
        guard.validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "api")
        with self.assertRaisesRegex(guard.GuardError, "start_permit_role"):
            guard.validate_permit(permit, "start", "a" * 40, "b" * 64, "c" * 64, "worker")

    def test_migration_permit_requires_exact_backup_pause_and_hashes(self):
        permit = {
            "schema_version": 1,
            "operation": "migrate",
            "source_commit": "a" * 40,
            "release_sha256": "b" * 64,
            "config_sha256": "c" * 64,
            "approval_ref": "synthetic-approval",
            "backup_sha256": "d" * 64,
            "from_revision": guard.BEFORE,
            "target_revision": guard.AFTER,
            "phase": "backup_verified_writes_paused",
        }
        guard.validate_permit(permit, "migrate", "a" * 40, "b" * 64, "c" * 64)
        for changed in (
            {**permit, "phase": "prepared"},
            {**permit, "backup_sha256": ""},
            {**permit, "config_sha256": "d" * 64},
        ):
            with self.assertRaises(guard.GuardError):
                guard.validate_permit(changed, "migrate", "a" * 40, "b" * 64, "c" * 64)

    def test_start_has_no_migration_and_dispatches_only_after_preflight(self):
        with patch.object(start, "load", return_value={}) as load, patch.object(
            start, "preflight", new_callable=AsyncMock
        ) as pre, patch.object(start, "dispatch") as dispatch, patch.object(
            migrate, "run"
        ) as migration:
            start.run("api", "a" * 40)
            load.assert_called_once_with("start", "a" * 40, "api")
            pre.assert_awaited_once_with({})
            dispatch.assert_called_once_with("api")
            migration.assert_not_called()

    def test_wrong_schema_never_starts_business(self):
        with patch.object(start, "load", return_value={}), patch.object(
            start,
            "preflight",
            new_callable=AsyncMock,
            side_effect=guard.GuardError("schema_mismatch"),
        ), patch.object(start, "dispatch") as dispatch:
            with self.assertRaisesRegex(guard.GuardError, "schema_mismatch"):
                start.run("api", "a" * 40)
            dispatch.assert_not_called()

    def test_missing_migration_permit_never_connects_or_runs(self):
        with patch.object(
            migrate, "load", side_effect=guard.GuardError("protected_file")
        ), patch.object(migrate, "verify_migration_tree") as tree, patch.object(
            migrate, "apply", new_callable=AsyncMock
        ) as apply:
            with self.assertRaises(guard.GuardError):
                migrate.run("a" * 40)
            tree.assert_not_called()
            apply.assert_not_called()

    def test_maintenance_exclusions_preserved_and_changes_rejected(self):
        schedule = {k: {"task": v} for k, v in start.EXCLUSIONS.items()}
        expected = {"b" + str(i): "task" + str(i) for i in range(7)}
        schedule.update({k: {"task": v} for k, v in expected.items()})
        self.assertEqual(set(start.filtered_schedule(schedule, expected)), set(expected))
        changed = copy.deepcopy(schedule)
        changed["backup-database-daily"]["task"] = "different"
        with self.assertRaises(guard.GuardError):
            start.filtered_schedule(changed, expected)


class MigrationAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_connection_guard_rejects_bypass_app_and_multiple_heads(self):
        class Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        row = {
            "db": guard.DATABASE,
            "usr": "clothing_app",
            "version": "180006",
            "ro": "off",
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": False,
        }
        conn = Mock(
            transaction=Mock(return_value=Transaction()),
            fetchrow=AsyncMock(return_value=row),
            fetch=AsyncMock(return_value=[{"version_num": guard.AFTER}]),
        )
        await guard.verify_connection(conn, "clothing_app", False, guard.AFTER)
        conn.transaction.assert_called_with(readonly=True)
        conn.fetchrow.return_value = {**row, "rolbypassrls": True}
        with self.assertRaisesRegex(guard.GuardError, "database_identity"):
            await guard.verify_connection(conn, "clothing_app", False, guard.AFTER)
        conn.fetchrow.return_value = row
        conn.fetch.return_value = [{"version_num": guard.AFTER}, {"version_num": "unapproved"}]
        with self.assertRaisesRegex(guard.GuardError, "schema_mismatch"):
            await guard.verify_connection(conn, "clothing_app", False, guard.AFTER)

    async def test_timeout_stops_child_and_releases_migration_lock(self):
        conn = Mock(fetchval=AsyncMock(return_value=True), close=AsyncMock())
        proc = Mock(
            returncode=None,
            wait=AsyncMock(side_effect=[asyncio.TimeoutError, -15]),
            terminate=Mock(),
            kill=Mock(),
        )
        proc.terminate.side_effect = lambda: setattr(proc, "returncode", -15)
        with patch.object(
            migrate, "connect", new_callable=AsyncMock, return_value=conn
        ), patch.object(migrate, "verify_connection", new_callable=AsyncMock), patch.object(
            migrate.asyncio, "create_subprocess_exec", new_callable=AsyncMock, return_value=proc
        ):
            with self.assertRaisesRegex(guard.GuardError, "migration_timeout"):
                await migrate.apply({})
            proc.terminate.assert_called_once()
            proc.kill.assert_not_called()
            self.assertEqual(
                conn.fetchval.await_args_list[-1].args[0], "SELECT pg_advisory_unlock($1)"
            )
            conn.close.assert_awaited_once()

    async def test_busy_lock_never_spawns_alembic(self):
        conn = Mock(fetchval=AsyncMock(return_value=False), close=AsyncMock())
        with patch.object(
            migrate, "connect", new_callable=AsyncMock, return_value=conn
        ), patch.object(migrate, "verify_connection", new_callable=AsyncMock), patch.object(
            migrate.asyncio, "create_subprocess_exec", new_callable=AsyncMock
        ) as spawn:
            with self.assertRaisesRegex(guard.GuardError, "migration_busy"):
                await migrate.apply({})
            spawn.assert_not_called()
            conn.close.assert_awaited_once()

    async def test_explicit_target_and_failed_process_release_lock(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                conn = Mock(fetchval=AsyncMock(return_value=True), close=AsyncMock())
                proc = Mock(returncode=returncode, wait=AsyncMock(return_value=returncode))
                with patch.object(
                    migrate, "connect", new_callable=AsyncMock, return_value=conn
                ), patch.object(
                    migrate, "verify_connection", new_callable=AsyncMock
                ) as verify, patch.object(
                    migrate.asyncio,
                    "create_subprocess_exec",
                    new_callable=AsyncMock,
                    return_value=proc,
                ) as spawn:
                    if returncode:
                        with self.assertRaisesRegex(guard.GuardError, "migration_process_failed"):
                            await migrate.apply({})
                    else:
                        await migrate.apply({})
                    self.assertEqual(spawn.call_args.args[-2:], ("upgrade", guard.AFTER))
                    self.assertNotIn("head", spawn.call_args.args)
                    self.assertEqual(
                        verify.await_args_list[-1].args[-1],
                        guard.AFTER if not returncode else guard.BEFORE,
                    )
                    self.assertEqual(
                        conn.fetchval.await_args_list[-1].args,
                        ("SELECT pg_advisory_unlock($1)", migrate.LOCK_KEY),
                    )
                    conn.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
