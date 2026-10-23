"""Public SQLAlchemy callback candidate for one asyncpg DDL connection.

No DB/engine is created here. Live installation is disabled. The synthetic seam
preserves original env.py/Alembic execution; no replacement env.py or PGOPTIONS.
"""

import re
from contextlib import contextmanager
from copy import deepcopy
from urllib.parse import unquote, urlsplit

from sql_policy import (
    AFTER,
    BEFORE,
    DATABASE,
    LOCK_KEY,
    SqlRejected,
    canonical,
    fields,
    need,
    parse,
    pinned_guard,
    sha,
    validate_spec,
)

READBACK_SQL = """SELECT pg_backend_pid() pid,
  extract(epoch from backend_start)::text backend_start,
  current_setting('application_name') application_name,
  (SELECT setting::bigint FROM pg_settings WHERE name='lock_timeout') lock_ms,
  (SELECT setting::bigint FROM pg_settings WHERE name='statement_timeout') statement_ms
  FROM pg_stat_activity WHERE pid=pg_backend_pid()"""
LOCK_READBACK_SQL = """SELECT EXISTS (SELECT 1 FROM pg_locks
  WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted
  AND classid=0 AND objid=$1 AND objsubid=1)"""
DATABASE_FACTS = {
    "database": DATABASE,
    "role": "clothing_bypass",
    "server_version_num": 180006,
    "bypassrls": True,
    "superuser": False,
    "createrole": False,
    "createdb": False,
    "default_read_only": False,
    "promotion_owner": "clothing_bypass",
}


def validate_identity(identity, spec):
    fields(
        identity,
        {"pid", "backend_start", "application_name"},
        "backend_identity_fields",
    )
    need(
        type(identity["pid"]) is int and 0 < identity["pid"] <= 2147483647,
        "backend_pid",
    )
    need(
        type(identity["backend_start"]) is str
        and re.fullmatch(r"[0-9]{9,12}(?:\.[0-9]{1,6})?", identity["backend_start"]),
        "backend_start",
    )
    need(identity["application_name"] == spec.application_name, "backend_application")
    return deepcopy(identity)


def validate_evidence(proof, spec, *, complete=True):
    fields(
        proof,
        {
            "policy_sha256",
            "connect_attempts",
            "connections",
            "identity",
            "database",
            "gucs",
            "advisory",
            "pre_head",
            "post_head",
            "statements",
            "closed",
        },
        "proof_fields",
    )
    need(
        proof["policy_sha256"] == spec.sha256
        and type(proof["connect_attempts"]) is int
        and proof["connect_attempts"] == 1
        and type(proof["connections"]) is int
        and proof["connections"] == 1,
        "proof_connection_count",
    )
    identity = validate_identity(proof["identity"], spec)
    need(canonical(proof["database"]) == canonical(DATABASE_FACTS), "proof_database")
    need(
        type(proof["gucs"]) is dict
        and proof["gucs"] == {"lock_ms": 10000, "statement_ms": 150000}
        and all(type(v) is int for v in proof["gucs"].values()),
        "proof_gucs",
    )
    need(
        proof["advisory"]
        == {
            "key": LOCK_KEY,
            "pid": identity["pid"],
            "same_connection": True,
            "held_at_postcheck": complete,
        },
        "proof_lock",
    )
    need(
        type(proof["advisory"]["key"]) is int
        and type(proof["advisory"]["pid"]) is int
        and proof["advisory"]["same_connection"] is True
        and proof["advisory"]["held_at_postcheck"] is complete,
        "proof_lock_types",
    )
    need(proof["pre_head"] == BEFORE, "proof_pre_head")
    need(
        type(proof["statements"]) is int
        and proof["statements"] >= (1 if complete else 0),
        "proof_statements",
    )
    need(type(proof["closed"]) is bool, "proof_closed_type")
    if complete:
        need(
            proof["post_head"] == AFTER and proof["closed"] is True, "proof_completion"
        )
    return deepcopy(proof)


class SqlMigrationDriver:
    def __init__(self, spec, checkpoint=None):
        self.spec = validate_spec(spec)
        self.guard = pinned_guard(dict(spec._artifacts))
        self.attempts = 0
        self.connections = 0
        self.raw = None
        self.record = None
        self.invalid = False
        self.checkpoint = checkpoint
        self.proof = {
            "policy_sha256": spec.sha256,
            "connect_attempts": 0,
            "connections": 0,
            "identity": None,
            "database": None,
            "gucs": None,
            "advisory": None,
            "pre_head": None,
            "post_head": None,
            "statements": 0,
            "closed": False,
        }

    def prepare_connect(self, dialect, cparams):
        self.attempts += 1
        self.proof["connect_attempts"] = self.attempts
        try:
            need(not self.invalid and self.attempts == 1, "extra_connection")
            need(
                dialect.name == "postgresql" and dialect.driver == "asyncpg",
                "driver_target",
            )
            need(
                type(cparams) is dict
                and set(cparams)
                in (
                    {"host", "port", "database", "user", "password"},
                    {"host", "port", "database", "user", "password", "server_settings"},
                ),
                "driver_parameters",
            )
            url = urlsplit(parse(self.spec.artifact("config"))["DATABASE_URL_BYPASS"])
            need(
                cparams["host"] == url.hostname == "prod-db"
                and type(cparams["port"]) is int
                and cparams["port"] == url.port == 5432
                and cparams["database"] == unquote(url.path[1:]) == DATABASE
                and cparams["user"] == unquote(url.username) == "clothing_bypass"
                and cparams["password"] == unquote(url.password),
                "driver_target",
            )
            settings = {
                "lock_timeout": "10000ms",
                "statement_timeout": "150000ms",
                "application_name": self.spec.application_name,
            }
            if "server_settings" in cparams:
                need(
                    type(cparams["server_settings"]) is dict
                    and cparams["server_settings"] == settings,
                    "driver_setting_conflict",
                )
            cparams["server_settings"] = settings
            if self.spec.data["mode"] == "linux_ci":
                from linux_transport import verify_ci_network

                verify_ci_network()
                cparams["host"] = "127.0.0.1"
            return cparams
        except BaseException:
            self.invalid = True
            raise

    def do_connect(self, dialect, record, args, params):
        try:
            need(not args, "driver_positional_parameters")
            self.prepare_connect(dialect, params)
            self.record = record
        except BaseException:
            self.invalid = True
            raise

    async def _readback(self, raw):
        async with raw.transaction(readonly=True):
            row = await raw.fetchrow(READBACK_SQL)
        need(row is not None, "readback_missing")
        identity = validate_identity(
            {k: row[k] for k in ("pid", "backend_start", "application_name")}, self.spec
        )
        need(identity["pid"] == raw.get_server_pid(), "driver_pid_mismatch")
        gucs = {k: row[k] for k in ("lock_ms", "statement_ms")}
        need(
            gucs == {"lock_ms": 10000, "statement_ms": 150000}
            and all(type(v) is int for v in gucs.values()),
            "sql_timeout_readback",
        )
        return identity, gucs

    async def verify_connection(self, raw):
        self.connections += 1
        self.proof["connections"] = self.connections
        try:
            need(
                not self.invalid and self.attempts == 1 and self.connections == 1,
                "connect_event_order",
            )
            self.raw = raw
            if self.spec.data["mode"] == "linux_ci":
                need(
                    await raw.fetchval("SELECT nonce FROM public.flow78_disposable_ci")
                    == self.spec.data["nonce"],
                    "ci_database_marker",
                )
            identity, gucs = await self._readback(raw)
            self.proof["identity"], self.proof["gucs"] = identity, gucs
            await self.guard.verify_connection(raw, "clothing_bypass", True, BEFORE)
            self.proof["database"] = dict(DATABASE_FACTS)
            need(
                await raw.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY) is True,
                "migration_busy",
            )
            await self.guard.verify_connection(raw, "clothing_bypass", True, BEFORE)
            need(
                await raw.fetchval(LOCK_READBACK_SQL, LOCK_KEY) is True,
                "migration_lock_readback",
            )
            self.proof["advisory"] = {
                "key": LOCK_KEY,
                "pid": identity["pid"],
                "same_connection": True,
                "held_at_postcheck": False,
            }
            self.proof["pre_head"] = BEFORE
            if self.checkpoint is not None:
                self.checkpoint(deepcopy(self.proof))
        except BaseException:
            self.invalid = True
            raise

    def connect(self, dbapi_connection, record):
        try:
            need(record is self.record and record is not None, "connect_record")
            dbapi_connection.run_async(self.verify_connection)
        except BaseException:
            self.invalid = True
            raise

    def before_cursor_execute(
        self, connection, cursor, statement, parameters, context, many
    ):
        # Never inspect/emit statement, parameters, or customer rows.
        try:
            raw = connection.connection.driver_connection
            need(
                not self.invalid
                and not self.proof["closed"]
                and self.proof["post_head"] is None
                and raw is self.raw
                and self.proof["pre_head"] == BEFORE
                and self.proof["advisory"] is not None
                and raw.get_server_pid() == self.proof["identity"]["pid"],
                "statement_connection_guard",
            )
            self.proof["statements"] += 1
        except BaseException:
            self.invalid = True
            raise

    async def verify_postcondition(self, raw):
        try:
            need(
                not self.invalid and raw is self.raw and self.proof["statements"] > 0,
                "postcheck_order",
            )
            identity, gucs = await self._readback(raw)
            need(
                identity == self.proof["identity"] and gucs == self.proof["gucs"],
                "postcheck_identity",
            )
            await self.guard.verify_connection(raw, "clothing_bypass", True, AFTER)
            need(
                await raw.fetchval(LOCK_READBACK_SQL, LOCK_KEY) is True,
                "postcheck_lock",
            )
            self.proof["advisory"]["held_at_postcheck"] = True
            self.proof["post_head"] = AFTER
        except BaseException:
            self.invalid = True
            raise

    def checkin(self, dbapi_connection, record):
        # Pool checkin listeners must not prevent NullPool's following close.
        try:
            need(
                record is self.record and dbapi_connection is not None, "checkin_record"
            )
            dbapi_connection.run_async(self.verify_postcondition)
        except BaseException:
            self.invalid = True

    def close(self, dbapi_connection, record):
        # Never throw from Pool.close: the real close follows this notification.
        if record is self.record:
            self.proof["closed"] = True
        else:
            self.invalid = True

    def result(self):
        need(not self.invalid, "driver_invalid")
        return validate_evidence(self.proof, self.spec)


def install_live_events(*args, **kwargs):
    raise SqlRejected("live_disabled")


@contextmanager
def candidate_events(driver, events, engine_target):
    """Private child scope; never leaves listeners installed after an exception."""
    installed = []
    try:
        for name in (
            "do_connect",
            "connect",
            "before_cursor_execute",
            "checkin",
            "close",
        ):
            callback = getattr(driver, name)
            events.listen(engine_target, name, callback)
            installed.append((name, callback))
        yield
    finally:
        for name, callback in reversed(installed):
            events.remove(engine_target, name, callback)


def run_synthetic_child(spec, runtime):
    """Injected isolated-child seam; no paths/processes/real engines constructed.

    Future live runtime must call original Alembic command.upgrade and retain the
    sealed env.py/migration files. This seam does not implement that live runtime.
    """
    need(
        getattr(runtime, "execution_mode", None) == "synthetic"
        and getattr(runtime, "isolated_child", False) is True,
        "isolated_synthetic_child_only",
    )
    driver = SqlMigrationDriver(spec)
    need(
        sha(runtime.env_bytes()) == sha(spec.artifact("env_py"))
        and runtime.migration_tree_bytes() == spec.artifact("migration_tree"),
        "child_source_binding",
    )
    callbacks = {
        "do_connect": driver.do_connect,
        "connect": driver.connect,
        "before_cursor_execute": driver.before_cursor_execute,
        "checkin": driver.checkin,
        "close": driver.close,
    }
    installed = []
    try:
        for name, callback in callbacks.items():
            runtime.events.listen(runtime.engine_target, name, callback)
            installed.append((name, callback))
        runtime.upgrade_original_env("/app/alembic.ini", AFTER)
        return driver.result()
    finally:
        for name, callback in reversed(installed):
            runtime.events.remove(runtime.engine_target, name, callback)
