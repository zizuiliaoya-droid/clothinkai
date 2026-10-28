"""Closed input contract. This candidate has no live capability."""

from dataclasses import dataclass
import hashlib
import json
import re

PHASE1_SHA = "158d1063c2144c07798a72cb2d5671f7473b012bb5bcf36154f9970e6e003c2d"
BEFORE = "067_retro_completion"
AFTER = "069_remove_unused_shop_caches"
TARGET = {
    "instance": "i-bp13rq87f4kzz3aalaf7",
    "disk_uuid": "7338e100-eb5b-489a-b457-1579c5200e9b",
    "origin": "https://47.110.76.9",
}
POLICY = {
    "observation_seconds": 30,
    "drain_seconds": 600,
    "stop_seconds": 120,
    "backup_seconds": 2100,
    "restore_seconds": 1800,
    "build_seconds": 600,
    "migration_seconds": 210,
    "stable_seconds": 30,
    "ttl_seconds": 300,
    "sql_lock_ms": 10000,
    "sql_statement_ms": 150000,
}
STEPS = (
    "PREPARE",
    "BUILD_BACKEND",
    "BUILD_FRONTEND",
    "PREFLIGHT",
    "FREEZE_TIMERS",
    "DISABLE_RESTART",
    "CLOSE_GATE",
    "STOP_BEAT",
    "FREEZE_PRODUCERS",
    "DRAIN",
    "STOP_WORKER",
    "FREEZE_VERIFY",
    "BACKUP",
    "RESTORE",
    "STOP_API",
    "MIGRATE",
    "VERIFY_SCHEMA",
    "REPLACE_API",
    "REPLACE_WEB",
    "ACCEPT_INTERNAL",
    "START_WORKER",
    "VERIFY_WORKER",
    "START_BEAT",
    "VERIFY_JOBS",
    "OPEN_GATE",
    "VERIFY_OPEN",
    "RESTORE_RESTART",
    "RESTORE_TIMERS",
    "VERIFY_COMPLETE",
)
READ_ONLY = frozenset(
    {
        "PREFLIGHT",
        "DRAIN",
        "FREEZE_VERIFY",
        "VERIFY_SCHEMA",
        "ACCEPT_INTERNAL",
        "VERIFY_WORKER",
        "VERIFY_JOBS",
        "VERIFY_OPEN",
        "VERIFY_COMPLETE",
    }
)


class Rejected(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def canonical(data):
    return json.dumps(
        data, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(data):
    return hashlib.sha256(canonical(data)).hexdigest()


def exact(data, fields):
    require(type(data) is dict and set(data) == set(fields), "fields")


def sha(value, width=64):
    require(
        type(value) is str
        and re.fullmatch("[0-9a-f]{" + str(width) + "}", value) is not None,
        "sha",
    )


@dataclass(frozen=True)
class ReleaseInput:
    encoded: bytes

    @property
    def data(self):
        return json.loads(self.encoded)

    @property
    def sha256(self):
        return hashlib.sha256(self.encoded).hexdigest()


def validate_input(data):
    exact(
        data,
        (
            "schema_version",
            "mode",
            "live_enabled",
            "deployable",
            "source_commit",
            "source_tree",
            "phase1_pins_sha256",
            "release_id",
            "boot_id",
            "target",
            "from_revision",
            "target_revision",
            "baseline_sha256",
            "collector_sha256",
            "policy",
        ),
    )
    require(
        type(data["schema_version"]) is int and data["schema_version"] == 1, "schema"
    )
    require(
        data["mode"] == "synthetic"
        and data["live_enabled"] is False
        and data["deployable"] is False,
        "live_disabled",
    )
    sha(data["source_commit"], 40)
    sha(data["source_tree"], 40)
    for key in ("baseline_sha256", "collector_sha256"):
        sha(data[key])
    require(data["phase1_pins_sha256"] == PHASE1_SHA, "phase1_dependency")
    require(
        data["release_id"] == "flow78-" + data["source_commit"][:12], "release_identity"
    )
    require(
        type(data["boot_id"]) is str
        and re.fullmatch(r"fixture-boot-[0-9]{3}", data["boot_id"]),
        "synthetic_boot",
    )
    require(
        data["target"] == TARGET
        and data["from_revision"] == BEFORE
        and data["target_revision"] == AFTER,
        "target",
    )
    require(
        data["policy"] == POLICY
        and all(type(v) is int for v in data["policy"].values()),
        "policy_unfrozen",
    )
    return ReleaseInput(canonical(data))


def parse_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate_key")
            out[key] = value
        return out

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(Rejected("nonfinite")),
        )
    except (ValueError, TypeError):
        raise Rejected("json") from None
