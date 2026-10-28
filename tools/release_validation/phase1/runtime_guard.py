"""Shared runtime guards. Production values never appear in diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

BEFORE = "067_retro_completion"
AFTER = "069_remove_unused_shop_caches"
ORIGIN = "https://47.110.76.9"
DATABASE = "clothing_erp_prod_20261018"
CONFIG = Path("/run/production/runtime-config.json")
RELEASE = Path("/app/release.json")
PERMITS = {
    "start": Path("/run/release-guard/start-permit.json"),
    "migrate": Path("/run/release-guard/migrate-permit.json"),
}
PRESERVED = {
    "CREDENTIAL_MASTER_KEY",
    "JWT_SECRET",
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_PRIVATE",
    "R2_BUCKET_PUBLIC",
    "R2_BUCKET_CREDENTIALS",
    "R2_BUCKET_BACKUPS",
    "R2_PUBLIC_BASE_URL",
    "JWT_ALGORITHM",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
}
GENERATED = {
    "ENVIRONMENT",
    "LOG_LEVEL",
    "DB_ECHO",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "CORS_ALLOWED_ORIGINS",
    "DATABASE_URL_APP",
    "DATABASE_URL_BYPASS",
    "DATABASE_URL_SYNC",
    "DATABASE_URL_ARCHIVER",
    "REDIS_URL_CACHE",
    "REDIS_URL_CELERY_BROKER",
    "REDIS_URL_CELERY_BACKEND",
}
REQUIRED = {
    "CREDENTIAL_MASTER_KEY",
    "JWT_SECRET",
    "R2_ENDPOINT_URL",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_PRIVATE",
} | GENERATED


class GuardError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise GuardError(code)


def parse(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            need(key not in result, "duplicate_json")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def read_protected(path):
    """Fixed path, root-owned 0600 regular file, no link swaps."""
    before = path.lstat()
    need(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == 0
        and stat.S_IMODE(before.st_mode) == 0o600
        and before.st_nlink == 1,
        "protected_file",
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        after = os.fstat(handle.fileno())
        need(
            (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), "protected_file_changed"
        )
        raw = handle.read(65537)
    need(len(raw) <= 65536, "protected_file_size")
    return raw


def validate_release(data, expected_commit):
    need(
        type(data) is dict
        and set(data) == {"source_commit", "source_tree", "schema", "origin", "phase"},
        "release_fields",
    )
    need(
        type(expected_commit) is str and bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit)),
        "expected_commit",
    )
    need(
        data["source_commit"] == expected_commit
        and type(data["source_tree"]) is str
        and bool(re.fullmatch(r"[0-9a-f]{40}", data["source_tree"])),
        "release_identity",
    )
    need(
        (data["schema"], data["origin"], data["phase"]) == (AFTER, ORIGIN, "candidate-only"),
        "release_target",
    )
    return data


def validate_config(data):
    need(type(data) is dict and REQUIRED <= set(data) <= PRESERVED | GENERATED, "config_fields")
    need(
        all(
            type(k) is str and type(v) is str and "\0" not in v and "\n" not in v and "\r" not in v
            for k, v in data.items()
        ),
        "config_values",
    )
    need(
        data["ENVIRONMENT"] == "production" and data["CORS_ALLOWED_ORIGINS"] == ORIGIN,
        "config_environment",
    )
    for key, role, driver in (
        ("DATABASE_URL_APP", "clothing_app", "asyncpg"),
        ("DATABASE_URL_BYPASS", "clothing_bypass", "asyncpg"),
        ("DATABASE_URL_SYNC", "clothing_bypass", "psycopg2"),
        ("DATABASE_URL_ARCHIVER", "clothing_archiver", "asyncpg"),
    ):
        value = urlsplit(data[key])
        need(
            value.scheme == "postgresql+" + driver
            and value.hostname == "prod-db"
            and value.port == 5432
            and value.username == role
            and value.password
            and value.path == "/" + DATABASE
            and not value.query
            and not value.fragment,
            "database_target",
        )
    for key, index in (
        ("REDIS_URL_CACHE", 0),
        ("REDIS_URL_CELERY_BROKER", 1),
        ("REDIS_URL_CELERY_BACKEND", 2),
    ):
        need(data[key] == f"redis://prod-redis:6379/{index}", "redis_target")
    return data


def validate_permit(
    data, operation, expected_commit, release_hash, config_hash, role=None, beat_db_format=None
):
    common = {
        "schema_version",
        "operation",
        "source_commit",
        "release_sha256",
        "config_sha256",
        "approval_ref",
    }
    extra = (
        {"backup_sha256", "from_revision", "target_revision", "phase"}
        if operation == "migrate"
        else {"schema", "allowed_roles", "beat_db_format"}
    )
    need(
        operation in PERMITS and type(data) is dict and set(data) == common | extra, "permit_fields"
    )
    need(
        data["schema_version"] == 1
        and type(data["schema_version"]) is int
        and data["operation"] == operation,
        "permit_operation",
    )
    need(
        data["source_commit"] == expected_commit
        and data["release_sha256"] == release_hash
        and data["config_sha256"] == config_hash,
        "permit_identity",
    )
    need(
        type(data["approval_ref"]) is str
        and bool(re.fullmatch(r"[A-Za-z0-9_.-]{8,100}", data["approval_ref"])),
        "approval_reference",
    )
    if operation == "migrate":
        need(
            (data["from_revision"], data["target_revision"], data["phase"])
            == (BEFORE, AFTER, "backup_verified_writes_paused"),
            "migration_permit_state",
        )
        need(
            type(data["backup_sha256"]) is str
            and bool(re.fullmatch(r"[0-9a-f]{64}", data["backup_sha256"])),
            "backup_reference",
        )
    else:
        allowed = data["allowed_roles"]
        need(
            data["schema"] == AFTER
            and type(allowed) is list
            and len(set(allowed)) == len(allowed)
            and set(allowed) <= {"api", "worker", "beat"}
            and role in allowed,
            "start_permit_role",
        )
        need(
            data["beat_db_format"] in ("dbm.gnu", "dbm.dumb")
            if "beat" in allowed
            else data["beat_db_format"] is None,
            "beat_format_permit",
        )
        if role == "beat":
            need(beat_db_format == data["beat_db_format"], "beat_format_mismatch")
    return data


def load(operation, expected_commit, role=None, beat_db_format=None):
    need(os.geteuid() == 0 and not Path("/app/.env").exists(), "runtime_identity")
    raw_release = RELEASE.read_bytes()
    validate_release(parse(raw_release), expected_commit)
    raw_config = read_protected(CONFIG)
    config = validate_config(parse(raw_config))
    raw_permit = read_protected(PERMITS[operation])
    validate_permit(
        parse(raw_permit),
        operation,
        expected_commit,
        hashlib.sha256(raw_release).hexdigest(),
        hashlib.sha256(raw_config).hexdigest(),
        role,
        beat_db_format,
    )
    # No inherited Python injection, proxy variables or alternate DB URLs.
    clean = {
        "PATH": "/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        "HOME": "/root",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        **config,
    }
    os.environ.clear()
    os.environ.update(clean)
    return config


async def verify_connection(conn, role, bypass, revision):
    async with conn.transaction(readonly=True):
        row = await conn.fetchrow(
            "SELECT current_database() db,current_user usr,current_setting('server_version_num') version,current_setting('default_transaction_read_only') ro,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        need(
            row["db"] == DATABASE
            and row["usr"] == role
            and row["version"] == "180006"
            and row["ro"] == "off"
            and not row["rolsuper"]
            and not row["rolcreaterole"]
            and not row["rolcreatedb"]
            and row["rolbypassrls"] == bypass,
            "database_identity",
        )
        need(
            [
                r["version_num"]
                for r in await conn.fetch("SELECT version_num FROM public.alembic_version")
            ]
            == [revision],
            "schema_mismatch",
        )
        if bypass:
            need(
                await conn.fetchval(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.promotion'::regclass"
                )
                == "clothing_bypass",
                "migration_owner",
            )


async def connect(config, key):
    import asyncpg

    return await asyncpg.connect(
        config[key].replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=10,
        command_timeout=15,
    )
