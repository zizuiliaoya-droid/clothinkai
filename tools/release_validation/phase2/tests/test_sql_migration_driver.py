"""Synthetic bytes and fake connections only; no production files or sockets."""

import asyncio
from pathlib import Path
import unittest
from types import SimpleNamespace

from sql_policy import (
    AFTER,
    BEFORE,
    DATABASE,
    LOCK_KEY,
    PHASE1_SHA,
    TIMEOUTS,
    MigrationSpec,
    SqlRejected,
    canonical,
    parse,
    pinned_guard,
    prepare_policy,
    safe_failure,
    sha,
)
from sql_migration_driver import (
    LOCK_READBACK_SQL,
    READBACK_SQL,
    SqlMigrationDriver,
    install_live_events,
    run_synthetic_child,
)
from tests.test_migration_process import NoSocketAsyncCase, NoSocketLoop


def fixture_policy():
    """Reads only frozen public pins/guard. Every other artifact is synthetic."""
    phase1 = Path(__file__).resolve().parents[2] / "release-flow78-1021"
    if not phase1.is_dir():
        phase1 = Path(__file__).resolve().parents[2] / "phase1"
    artifacts = {
        "phase1_pins": (phase1 / "implementation-pins.json").read_bytes(),
        "phase1_guard": (phase1 / "runtime_guard.py").read_bytes(),
    }
    guard = pinned_guard(artifacts)
    config = {key: "fixture-value" for key in guard.REQUIRED}
    config.update(
        {
            "ENVIRONMENT": "production",
            "CORS_ALLOWED_ORIGINS": guard.ORIGIN,
            "R2_ENDPOINT_URL": "https://fixture.invalid",
            "LOG_LEVEL": "INFO",
            "DB_ECHO": "false",
            "DB_POOL_SIZE": "2",
            "DB_MAX_OVERFLOW": "0",
        }
    )
    for key, role, dialect in (
        ("APP", "app", "asyncpg"),
        ("BYPASS", "bypass", "asyncpg"),
        ("SYNC", "bypass", "psycopg2"),
        ("ARCHIVER", "archiver", "asyncpg"),
    ):
        config["DATABASE_URL_" + key] = (
            f"postgresql+{dialect}://clothing_{role}:fixture-password@prod-db:5432/{DATABASE}"
        )
    for key, index in (("CACHE", 0), ("CELERY_BROKER", 1), ("CELERY_BACKEND", 2)):
        config["REDIS_URL_" + key] = f"redis://prod-redis:6379/{index}"
    commit, tree = "1" * 40, "2" * 40
    artifacts["release"] = canonical(
        {
            "source_commit": commit,
            "source_tree": tree,
            "schema": AFTER,
            "origin": guard.ORIGIN,
            "phase": "candidate-only",
        }
    )
    artifacts["config"] = canonical(config)
    backup_sha = sha(b"synthetic-dump-no-customer-data")
    artifacts["permit"] = canonical(
        {
            "schema_version": 1,
            "operation": "migrate",
            "source_commit": commit,
            "release_sha256": sha(artifacts["release"]),
            "config_sha256": sha(artifacts["config"]),
            "approval_ref": "fixture-only-not-approval",
            "backup_sha256": backup_sha,
            "from_revision": BEFORE,
            "target_revision": AFTER,
            "phase": "backup_verified_writes_paused",
        }
    )
    for purpose in ("backup", "restore"):
        artifacts[purpose + "_receipt"] = canonical(
            {
                "schema_version": 1,
                "mode": "synthetic",
                "purpose": purpose,
                "verified": True,
                "writes_paused": True,
                "revision": BEFORE,
                "backup_sha256": backup_sha,
                "config_sha256": sha(artifacts["config"]),
            }
        )
    artifacts["env_py"] = b"# synthetic env.py identity; never execute product\n"
    revisions = [f"{i:03}_fixture" for i in range(1, 66)] + [BEFORE, AFTER]
    artifacts["migration_tree"] = canonical(
        {
            "source_commit": commit,
            "source_tree": tree,
            "env_sha256": sha(artifacts["env_py"]),
            "nodes": [
                {
                    "revision": revision,
                    "down_revision": revisions[i - 1] if i else None,
                    "sha256": sha(revision.encode()),
                }
                for i, revision in enumerate(revisions)
            ],
        }
    )
    data = {
        "schema_version": 1,
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "source_commit": commit,
        "source_tree": tree,
        "release_id": "flow78-" + commit[:12],
        "operation": "migrate",
        "from_revision": BEFORE,
        "target_revision": AFTER,
        "lock_key": LOCK_KEY,
        "timeouts": dict(TIMEOUTS),
        "hashes": {name: sha(raw) for name, raw in artifacts.items()},
    }
    return data, artifacts


def fixture_spec():
    return prepare_policy(*fixture_policy())


def params():
    return {
        "host": "prod-db",
        "port": 5432,
        "database": DATABASE,
        "user": "clothing_bypass",
        "password": "fixture-password",
    }


DIALECT = SimpleNamespace(name="postgresql", driver="asyncpg")


class FakeRaw:
    def __init__(self, spec):
        self.pid, self.heads, self.locked, self.busy = 12345, [BEFORE], False, False
        self.lock_readback, self.depth, self.owner, self.events = (
            True,
            0,
            "clothing_bypass",
            [],
        )
        self.gucs = {
            "pid": self.pid,
            "backend_start": "1792500000.123456",
            "application_name": spec.application_name,
            "lock_ms": 10000,
            "statement_ms": 150000,
        }
        self.roles = {
            "db": DATABASE,
            "usr": "clothing_bypass",
            "version": "180006",
            "ro": "off",
            "rolsuper": False,
            "rolcreaterole": False,
            "rolcreatedb": False,
            "rolbypassrls": True,
        }

    def get_server_pid(self):
        return self.pid

    def transaction(self, *, readonly):
        if readonly is not True:
            raise AssertionError("only readback transactions allowed")
        raw = self

        class Transaction:
            async def __aenter__(self):
                raw.depth += 1

            async def __aexit__(self, *_args):
                raw.depth -= 1

        return Transaction()

    async def fetchrow(self, sql):
        self.events.append("readback" if sql == READBACK_SQL else "role")
        return dict(self.gucs if sql == READBACK_SQL else self.roles)

    async def fetch(self, sql):
        self.events.append("head")
        return [{"version_num": head} for head in self.heads]

    async def fetchval(self, sql, *args):
        if "pg_get_userbyid" in sql:
            return self.owner
        if sql == "SELECT pg_try_advisory_lock($1)":
            if args != (LOCK_KEY,) or self.depth:
                raise AssertionError("wrong lock key or lingering transaction")
            self.events.append("lock")
            self.locked = not self.busy
            return self.locked
        if sql == LOCK_READBACK_SQL:
            self.events.append("lock_readback")
            return self.locked and self.lock_readback
        raise AssertionError("unexpected query")


def statement(driver, raw):
    driver.before_cursor_execute(
        SimpleNamespace(connection=SimpleNamespace(driver_connection=raw)),
        None,
        "SECRET_SQL",
        {"secret": "SECRET_PARAMETER"},
        None,
        False,
    )


class FakeEvents:
    def __init__(self):
        self.callbacks = {}

    def listen(self, target, name, callback):
        self.callbacks[name] = callback

    def remove(self, target, name, callback):
        if self.callbacks.pop(name) != callback:
            raise AssertionError("wrong callback removed")


class FakeRuntime:
    execution_mode, isolated_child = "synthetic", True

    def __init__(self, spec):
        self.spec, self.events, self.engine_target = spec, FakeEvents(), object()
        self.raw, self.skip, self.upgrades = FakeRaw(spec), None, []

    def env_bytes(self):
        return self.spec.artifact("env_py")

    def migration_tree_bytes(self):
        return self.spec.artifact("migration_tree")

    def upgrade_original_env(self, path, target):
        self.upgrades.append((path, target))
        callbacks, record, raw = self.events.callbacks, object(), self.raw
        with asyncio.Runner(loop_factory=NoSocketLoop) as runner:
            adapted = SimpleNamespace(run_async=lambda fn: runner.run(fn(raw)))
            if self.skip != "do_connect":
                callbacks["do_connect"](DIALECT, record, [], params())
            if self.skip != "connect":
                callbacks["connect"](adapted, record)
            callbacks["before_cursor_execute"](
                SimpleNamespace(connection=SimpleNamespace(driver_connection=raw)),
                None,
                None,
                None,
                None,
                False,
            )
            raw.heads = [AFTER]
            if self.skip != "checkin":
                callbacks["checkin"](adapted, record)
            callbacks["close"](adapted, record)


class FirstNegativeTests(unittest.TestCase):
    def test_policy_rejects_live_and_missing_bindings(self):
        with self.assertRaises(SqlRejected):
            prepare_policy({"mode": "live", "live_enabled": True}, {})

    def test_conflicting_driver_settings_are_rejected(self):
        driver = SqlMigrationDriver(fixture_spec())
        with self.assertRaises(SqlRejected):
            driver.prepare_connect(
                DIALECT, {**params(), "server_settings": {"statement_timeout": "0"}}
            )

    def test_live_event_installation_is_disabled(self):
        with self.assertRaisesRegex(SqlRejected, "live_disabled"):
            install_live_events()


class PolicyAndEventTests(unittest.TestCase):
    def test_frozen_dependency_and_immutable_spec(self):
        data, artifacts = fixture_policy()
        self.assertEqual(sha(artifacts["phase1_pins"]), PHASE1_SHA)
        spec = prepare_policy(data, artifacts)
        data["mode"] = "live"
        artifacts["config"] = b"changed"
        self.assertEqual(spec.data["mode"], "synthetic")
        self.assertNotIn("fixture-password", repr(spec))
        self.assertEqual(SqlMigrationDriver(spec).spec.sha256, spec.sha256)

    def test_each_artifact_drift_rejected(self):
        for name in fixture_policy()[1]:
            with self.subTest(name=name):
                data, artifacts = fixture_policy()
                artifacts[name] += b" "
                with self.assertRaises(SqlRejected):
                    prepare_policy(data, artifacts)

    def test_reforged_dependency_rejected(self):
        for name in ("phase1_pins", "phase1_guard"):
            data, artifacts = fixture_policy()
            artifacts[name] += b" "
            data["hashes"][name] = sha(artifacts[name])
            with self.assertRaises(SqlRejected):
                prepare_policy(data, artifacts)

    def test_cross_bindings(self):
        for name, key, value in (
            ("permit", "source_commit", "a" * 40),
            ("permit", "config_sha256", "a" * 64),
            ("permit", "approval_ref", "actual-approval"),
            ("restore_receipt", "backup_sha256", "a" * 64),
            ("backup_receipt", "writes_paused", False),
            ("migration_tree", "env_sha256", "a" * 64),
            ("release", "source_tree", "a" * 40),
        ):
            with self.subTest(name=name, key=key):
                data, artifacts = fixture_policy()
                obj = parse(artifacts[name])
                obj[key] = value
                artifacts[name] = canonical(obj)
                data["hashes"][name] = sha(artifacts[name])
                with self.assertRaises(SqlRejected):
                    prepare_policy(data, artifacts)

    def test_duplicate_json_and_nan(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}'):
            with self.assertRaises(SqlRejected):
                parse(raw)

    def test_unsealed_spec_and_timeout_change(self):
        with self.assertRaises(SqlRejected):
            SqlMigrationDriver(MigrationSpec(canonical({"mode": "synthetic"})))
        data, artifacts = fixture_policy()
        data["timeouts"]["lock_ms"] = 1
        with self.assertRaises(SqlRejected):
            prepare_policy(data, artifacts)

    def test_driver_target_drifts(self):
        for key, value in (
            ("host", "other"),
            ("port", True),
            ("database", "other"),
            ("user", "postgres"),
            ("password", "other"),
            ("timeout", 1),
        ):
            with self.subTest(key=key):
                driver = SqlMigrationDriver(fixture_spec())
                with self.assertRaises(SqlRejected):
                    driver.prepare_connect(DIALECT, {**params(), key: value})

    def test_driver_and_extra_connection(self):
        driver = SqlMigrationDriver(fixture_spec())
        with self.assertRaises(SqlRejected):
            driver.prepare_connect(
                SimpleNamespace(name="postgresql", driver="psycopg2"), params()
            )
        driver = SqlMigrationDriver(fixture_spec())
        self.assertEqual(
            driver.prepare_connect(DIALECT, params())["server_settings"][
                "lock_timeout"
            ],
            "10000ms",
        )
        with self.assertRaisesRegex(SqlRejected, "extra_connection"):
            driver.prepare_connect(DIALECT, params())

    def test_event_seam_and_listener_cleanup(self):
        spec = fixture_spec()
        runtime = FakeRuntime(spec)
        self.assertEqual(run_synthetic_child(spec, runtime)["post_head"], AFTER)
        self.assertEqual(runtime.upgrades, [("/app/alembic.ini", AFTER)])
        self.assertEqual(runtime.events.callbacks, {})

    def test_missing_events_and_cleanup(self):
        for missing in ("do_connect", "connect", "checkin"):
            with self.subTest(missing=missing):
                spec = fixture_spec()
                runtime = FakeRuntime(spec)
                runtime.skip = missing
                with self.assertRaises(SqlRejected):
                    run_synthetic_child(spec, runtime)
                self.assertEqual(runtime.events.callbacks, {})

    def test_source_or_child_scope_drift(self):
        spec = fixture_spec()
        runtime = FakeRuntime(spec)
        runtime.env_bytes = lambda: b"changed"
        with self.assertRaises(SqlRejected):
            run_synthetic_child(spec, runtime)
        runtime = FakeRuntime(spec)
        runtime.isolated_child = False
        with self.assertRaises(SqlRejected):
            run_synthetic_child(spec, runtime)

    def test_sqlstate_redaction(self):
        exc = RuntimeError("SECRET_SQL postgresql://password@server")
        exc.sqlstate = "55P03"
        self.assertEqual(
            safe_failure(exc), {"code": "execution_failed", "sqlstate": "55P03"}
        )
        exc.sqlstate = "SECRET"
        self.assertIsNone(safe_failure(exc)["sqlstate"])

    def test_close_not_blocked_by_guard_errors(self):
        driver = SqlMigrationDriver(fixture_spec())
        driver.record = record = object()

        def bad(_fn):
            raise RuntimeError("SECRET")

        driver.checkin(SimpleNamespace(run_async=bad), record)
        driver.close(None, record)
        self.assertTrue(driver.invalid)
        self.assertTrue(driver.proof["closed"])


class DriverAsyncTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.spec = fixture_spec()
        self.driver = SqlMigrationDriver(self.spec)
        self.raw = FakeRaw(self.spec)
        self.driver.prepare_connect(DIALECT, params())

    async def test_same_ddl_connection_gucs_and_lock_order(self):
        await self.driver.verify_connection(self.raw)
        statement(self.driver, self.raw)
        self.raw.heads = [AFTER]
        await self.driver.verify_postcondition(self.raw)
        self.driver.proof["closed"] = True
        proof = self.driver.result()
        self.assertEqual(proof["advisory"]["pid"], self.raw.pid)
        self.assertTrue(proof["advisory"]["held_at_postcheck"])
        self.assertEqual(self.raw.depth, 0)
        self.assertEqual(
            self.raw.events[:7],
            ["readback", "role", "head", "lock", "role", "head", "lock_readback"],
        )

    async def test_wrong_guc_identity_readback(self):
        for key, value in (
            ("lock_ms", 0),
            ("statement_ms", 0),
            ("pid", 12346),
            ("application_name", "other"),
            ("backend_start", "not-time"),
        ):
            driver, raw = SqlMigrationDriver(self.spec), FakeRaw(self.spec)
            driver.prepare_connect(DIALECT, params())
            raw.gucs[key] = value
            with self.subTest(key=key), self.assertRaises(SqlRejected):
                await driver.verify_connection(raw)
            self.assertFalse(raw.locked)

    async def test_wrong_role_version_schema(self):
        for key, value in (
            ("version", "170000"),
            ("usr", "postgres"),
            ("rolsuper", True),
            ("rolbypassrls", False),
            ("db", "other"),
        ):
            driver, raw = SqlMigrationDriver(self.spec), FakeRaw(self.spec)
            driver.prepare_connect(DIALECT, params())
            raw.roles[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await driver.verify_connection(raw)
        for heads in ([AFTER], [], [BEFORE, BEFORE]):
            driver, raw = SqlMigrationDriver(self.spec), FakeRaw(self.spec)
            driver.prepare_connect(DIALECT, params())
            raw.heads = heads
            with self.assertRaises(ValueError):
                await driver.verify_connection(raw)

    async def test_busy_or_missing_lock(self):
        self.raw.busy = True
        with self.assertRaises(SqlRejected):
            await self.driver.verify_connection(self.raw)
        driver, raw = SqlMigrationDriver(self.spec), FakeRaw(self.spec)
        driver.prepare_connect(DIALECT, params())
        raw.lock_readback = False
        with self.assertRaises(SqlRejected):
            await driver.verify_connection(raw)

    async def test_missing_connect_or_changed_connection(self):
        with self.assertRaises(SqlRejected):
            statement(self.driver, self.raw)
        with self.assertRaises(SqlRejected):
            await self.driver.verify_connection(self.raw)
        self.driver = SqlMigrationDriver(self.spec)
        self.driver.prepare_connect(DIALECT, params())
        await self.driver.verify_connection(self.raw)
        with self.assertRaises(SqlRejected):
            statement(self.driver, FakeRaw(self.spec))

    async def test_statement_after_postcheck_rejected(self):
        await self.driver.verify_connection(self.raw)
        statement(self.driver, self.raw)
        self.raw.heads = [AFTER]
        await self.driver.verify_postcondition(self.raw)
        with self.assertRaises(SqlRejected):
            statement(self.driver, self.raw)

    async def test_wrong_post_head_or_dropped_lock(self):
        await self.driver.verify_connection(self.raw)
        statement(self.driver, self.raw)
        with self.assertRaises(ValueError):
            await self.driver.verify_postcondition(self.raw)
        driver, raw = SqlMigrationDriver(self.spec), FakeRaw(self.spec)
        driver.prepare_connect(DIALECT, params())
        await driver.verify_connection(raw)
        statement(driver, raw)
        raw.heads = [AFTER]
        raw.locked = False
        with self.assertRaises(SqlRejected):
            await driver.verify_postcondition(raw)

    async def test_duplicate_connect_is_terminal(self):
        await self.driver.verify_connection(self.raw)
        with self.assertRaises(SqlRejected):
            await self.driver.verify_connection(self.raw)
        with self.assertRaises(SqlRejected):
            statement(self.driver, self.raw)

    async def test_schema_changed_while_waiting_for_lock_is_rechecked(self):
        original = self.raw.fetchval

        async def fetchval(sql, *args):
            value = await original(sql, *args)
            if sql == "SELECT pg_try_advisory_lock($1)":
                self.raw.heads = [AFTER]
            return value

        self.raw.fetchval = fetchval
        with self.assertRaises(ValueError):
            await self.driver.verify_connection(self.raw)
        self.assertTrue(self.raw.locked)
        self.assertIsNone(self.driver.proof["pre_head"])
        self.assertEqual(self.driver.proof["statements"], 0)

    async def test_wrong_migration_owner_rejected(self):
        self.raw.owner = "postgres"
        with self.assertRaises(ValueError):
            await self.driver.verify_connection(self.raw)
        self.assertFalse(self.raw.locked)
