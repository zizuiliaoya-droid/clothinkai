"""Fixed stdin program for the ORIGINAL API container; only counts leave it.

No command arguments, arbitrary Redis keys, SQL or paths. Does not import the app,
dispatch Celery remote-control broadcasts, mutate Redis or output configuration.
"""

import asyncio
import json
import os
import stat
from urllib.parse import urlsplit


def _config():
    fd = os.open("/run/production/runtime-config.json", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_nlink == 1
            and not info.st_mode & 0o077
            and 0 < info.st_size <= 65536
        ):
            raise ValueError("config_metadata")
        raw = os.read(fd, 65537)
        if len(raw) != info.st_size:
            raise ValueError("config_changed")
    finally:
        os.close(fd)

    def pairs(values):
        if len(values) != len(dict(values)):
            raise ValueError("duplicate_config")
        return dict(values)

    value = json.loads(raw, object_pairs_hook=pairs)
    if type(value) is not dict or not all(
        type(k) is str and type(v) is str for k, v in value.items()
    ):
        raise ValueError("config_type")
    if (
        value.get("ENVIRONMENT") != "production"
        or value.get("CORS_ALLOWED_ORIGINS") != "https://47.110.76.9"
    ):
        raise ValueError("config_environment")
    return value


def validate_config(config):
    database = urlsplit(config["DATABASE_URL_BYPASS"])
    broker = urlsplit(config["REDIS_URL_CELERY_BROKER"])
    if (
        database.scheme not in {"postgresql", "postgresql+asyncpg"}
        or database.hostname != "prod-db"
        or database.port != 5432
        or database.username != "clothing_bypass"
        or database.path != "/clothing_erp_prod_20261018"
        or database.query
        or database.fragment
    ):
        raise ValueError("database_binding")
    if (
        broker.scheme != "redis"
        or broker.hostname != "prod-redis"
        or broker.port != 6379
        or broker.path != "/1"
        or broker.query
        or broker.fragment
        or config["REDIS_URL_CELERY_BROKER"] != "redis://prod-redis:6379/1"
    ):
        raise ValueError("broker_binding")


async def observe():
    import asyncpg
    import redis.asyncio as redis

    config = _config()
    validate_config(config)
    connection = await asyncpg.connect(
        config["DATABASE_URL_BYPASS"].replace(
            "postgresql+asyncpg://", "postgresql://", 1
        ),
        timeout=5,
        command_timeout=5,
        server_settings={
            "default_transaction_read_only": "on",
            "application_name": "flow78-count-observer",
        },
    )
    client = redis.from_url(
        config["REDIS_URL_CELERY_BROKER"], socket_connect_timeout=5, socket_timeout=5
    )
    try:
        if (
            await connection.fetchval("SELECT current_setting('transaction_read_only')")
            != "on"
        ):
            raise ValueError("observer_not_readonly")
        if (
            await connection.fetchval("SELECT current_setting('server_version_num')")
            != "180006"
        ):
            raise ValueError("observer_pg_version")
        # Fixed Kombu default priority keys; settings must independently be bound
        # before these counts can contribute to drain acceptance.
        ready = {}
        for queue in ("default", "backup", "crawler", "report"):
            ready[queue] = [
                await client.llen(
                    queue if priority == 0 else queue + "\x06\x16" + str(priority)
                )
                for priority in (0, 3, 6, 9)
            ]
        count = await connection.fetchval("""SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND pid<>pg_backend_pid()
            AND usename IN ('clothing_app','clothing_bypass')""")
        return {
            "ready": ready,
            "unacked_hash": await client.hlen("unacked"),
            "unacked_index": await client.zcard("unacked_index"),
            "business_sessions": count,
        }
    finally:
        try:
            await client.aclose()
        finally:
            await connection.close()


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(observe()), sort_keys=True, separators=(",", ":")))
    except BaseException:
        # Neither connection errors nor server errors may expose credential URLs.
        raise SystemExit(2) from None
