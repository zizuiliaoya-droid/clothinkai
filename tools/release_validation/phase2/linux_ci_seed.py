"""Disposable CI bootstrap child. Original Alembic 001→066, never a production tool."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    from linux_alembic_runtime import verify_staged_files
    from linux_transport import PrivateDirectory, verify_ci_network
    from sql_policy import (
        BEFORE,
        DATABASE,
        LinuxCISpec,
        canonical,
        ci_config,
        need,
        parse,
        validate_spec,
    )

    need(len(sys.argv) == 2, "seed_arguments")
    directory = PrivateDirectory(sys.argv[1])
    packed = parse(directory.read("policy.json"))
    spec = validate_spec(
        LinuxCISpec(
            canonical(packed["data"]),
            tuple(sorted((k, v.encode()) for k, v in packed["artifacts"].items())),
        )
    )
    need(spec.data["nonce"] == sys.argv[1], "seed_nonce")
    verify_staged_files(spec, directory)
    verify_ci_network()
    need(Path.cwd() == directory.root / "app/backend", "seed_cwd")
    os.environ.clear()
    os.environ.update(
        {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", **ci_config()}
    )
    sys.path.insert(0, str(directory.root / "app/backend"))
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    def connect(dialect, record, args, params):
        need(
            dialect.name == "postgresql" and dialect.driver == "asyncpg" and not args,
            "seed_driver",
        )
        need(
            params
            == {
                "host": "prod-db",
                "port": 5432,
                "database": DATABASE,
                "user": "clothing_bypass",
                "password": "fixture-password",
            },
            "seed_target",
        )
        params["host"] = "127.0.0.1"
        params["server_settings"] = {
            "statement_timeout": "150000ms",
            "lock_timeout": "10000ms",
        }

    async def verify(raw):
        need(
            await raw.fetchval("SELECT nonce FROM public.flow78_disposable_ci")
            == spec.data["nonce"],
            "seed_marker",
        )
        need(
            await raw.fetchval("SELECT current_setting('server_version_num')")
            == "180006",
            "seed_version",
        )

    def connected(dbapi, record):
        dbapi.run_async(verify)

    event.listen(Engine, "do_connect", connect)
    event.listen(Engine, "connect", connected)
    try:
        config = Config(str(directory.root / "app/backend/alembic.ini"))
        config.set_main_option(
            "script_location", str(directory.root / "app/backend/alembic")
        )
        command.upgrade(config, BEFORE)
    finally:
        event.remove(Engine, "connect", connected)
        event.remove(Engine, "do_connect", connect)
        directory.close()


if __name__ == "__main__":
    main()
