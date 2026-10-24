"""Closed synthetic SQL policy. Reads no paths, environment or sockets.

The exact reviewed phase1 guard bytes are the sole compiled dependency. Only its
pure validators are called. Hash binding is not live permission or Git proof.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import ModuleType
from urllib.parse import urlsplit

PHASE1_SHA = "bdee6b80fb53c83dda5ed69b6082ada68290c667c33631a120559608cf90f5ba"
GUARD_SHA = "da3e0ed4e331cc6a3a41dba709a5c39bf4f6610fa565afcb4097423e59826201"
BEFORE = "066_platform_metric_setting"
AFTER = "067_retro_completion"
DATABASE = "clothing_erp_prod_20261018"
LOCK_KEY = 1007067066
TIMEOUTS = {
    "lock_ms": 10000,
    "statement_ms": 150000,
    "child_seconds": 180,
    "terminate_seconds": 5,
    "overall_seconds": 210,
}
ARTIFACTS = frozenset(
    {
        "phase1_pins",
        "phase1_guard",
        "release",
        "config",
        "permit",
        "env_py",
        "migration_tree",
        "backup_receipt",
        "restore_receipt",
    }
)


class SqlRejected(ValueError):
    """Internal fixed diagnostic codes only; never include input or SQL text."""


def need(ok, code):
    if not ok:
        raise SqlRejected(code)


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError):
        raise SqlRejected("json_value") from None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    need(type(raw) is bytes and 0 < len(raw) <= 1048576, "artifact_size")

    def pairs(items):
        data = {}
        for key, value in items:
            need(key not in data, "duplicate_json")
            data[key] = value
        return data

    def bad_constant(_value):
        raise SqlRejected("json_constant")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise SqlRejected("json_parse") from None


def fields(data, keys, code):
    need(type(data) is dict and set(data) == set(keys), code)


def is_sha(value, length=64):
    return type(value) is str and bool(
        re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value)
    )


def pinned_guard(artifacts):
    need(sha(artifacts["phase1_pins"]) == PHASE1_SHA, "phase1_pins_mismatch")
    need(sha(artifacts["phase1_guard"]) == GUARD_SHA, "phase1_guard_mismatch")
    pins = parse(artifacts["phase1_pins"])
    entry = pins["files"]["runtime_guard.py"]
    need(
        entry == {"bytes": len(artifacts["phase1_guard"]), "sha256": GUARD_SHA},
        "guard_pin",
    )
    guard = ModuleType("phase2_frozen_guard")
    # Trusted bytes: hardcoded hash and reviewed pinfile both match before exec.
    exec(
        compile(artifacts["phase1_guard"], "<pinned-phase1-runtime-guard>", "exec"),
        guard.__dict__,
    )
    return guard


@dataclass(frozen=True)
class MigrationSpec:
    encoded: bytes = field(repr=False)
    _artifacts: tuple = field(default=(), repr=False)

    @property
    def data(self):
        return parse(self.encoded)

    @property
    def sha256(self):
        return sha(self.encoded)

    def artifact(self, name):
        return dict(self._artifacts)[name]

    @property
    def application_name(self):
        return "flow78-" + self.sha256[:32]


def prepare_policy(data, artifacts):
    return _prepare_policy(data, artifacts, MigrationSpec)


def _prepare_policy(data, artifacts, spec_type):
    host = spec_type is HostMigrationSpec
    need(spec_type in (MigrationSpec, HostMigrationSpec), "policy_type")
    fields(
        data,
        {
            "schema_version",
            "mode",
            "live_enabled",
            "deployable",
            "source_commit",
            "source_tree",
            "release_id",
            "operation",
            "from_revision",
            "target_revision",
            "lock_key",
            "timeouts",
            "hashes",
        },
        "policy_fields",
    )
    need(
        type(data["schema_version"]) is int and data["schema_version"] == 1,
        "policy_schema",
    )
    need(
        data["mode"] == ("host_candidate" if host else "synthetic")
        and data["live_enabled"] is False
        and data["deployable"] is False,
        "live_disabled",
    )
    need(
        is_sha(data["source_commit"], 40) and is_sha(data["source_tree"], 40),
        "source_identity",
    )
    need(data["release_id"] == "flow78-" + data["source_commit"][:12], "release_id")
    need(
        (data["operation"], data["from_revision"], data["target_revision"])
        == ("migrate", BEFORE, AFTER),
        "migration_target",
    )
    need(type(data["lock_key"]) is int and data["lock_key"] == LOCK_KEY, "lock_key")
    need(
        type(data["timeouts"]) is dict
        and data["timeouts"] == TIMEOUTS
        and all(type(v) is int for v in data["timeouts"].values()),
        "timeout_policy",
    )
    fields(artifacts, ARTIFACTS, "artifact_fields")
    fields(data["hashes"], ARTIFACTS, "hash_fields")
    for name in ARTIFACTS:
        raw = artifacts[name]
        need(type(raw) is bytes and 0 < len(raw) <= 1048576, "artifact_size")
        need(
            is_sha(data["hashes"][name]) and sha(raw) == data["hashes"][name],
            "artifact_hash",
        )
    guard = pinned_guard(artifacts)
    release, config, permit = (
        parse(artifacts[n]) for n in ("release", "config", "permit")
    )
    try:
        guard.validate_release(release, data["source_commit"])
        guard.validate_config(config)
        guard.validate_permit(
            permit,
            "migrate",
            data["source_commit"],
            sha(artifacts["release"]),
            sha(artifacts["config"]),
        )
    except (ValueError, KeyError, TypeError):
        raise SqlRejected("phase1_validation") from None
    need(release["source_tree"] == data["source_tree"], "release_tree")
    if host:
        # A descriptive production policy is not an authority to execute. It cannot
        # reuse CI identities, fixture approvals, or claim synthetic backup proof.
        need(
            not permit["approval_ref"].startswith("fixture-"), "host_approval_reference"
        )
        need(
            urlsplit(config["R2_ENDPOINT_URL"]).hostname != "fixture.invalid",
            "host_config_not_ci",
        )
        need(
            all(
                not config[key].startswith("fixture-")
                for key in (
                    "CREDENTIAL_MASTER_KEY",
                    "JWT_SECRET",
                    "R2_ACCESS_KEY_ID",
                    "R2_SECRET_ACCESS_KEY",
                )
            ),
            "host_config_not_fixture",
        )
        need(
            all(
                urlsplit(config[key]).password != "fixture-password"
                for key in (
                    "DATABASE_URL_APP",
                    "DATABASE_URL_BYPASS",
                    "DATABASE_URL_SYNC",
                    "DATABASE_URL_ARCHIVER",
                )
            ),
            "host_database_not_ci",
        )
    else:
        # Refuse real config/permits accidentally supplied to this synthetic candidate.
        need(permit["approval_ref"].startswith("fixture-"), "synthetic_permit_only")
        for key in (
            "CREDENTIAL_MASTER_KEY",
            "JWT_SECRET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        ):
            need(config[key].startswith("fixture-"), "synthetic_config_only")
        need(
            urlsplit(config["R2_ENDPOINT_URL"]).hostname == "fixture.invalid",
            "synthetic_config_only",
        )
        for key in (
            "DATABASE_URL_APP",
            "DATABASE_URL_BYPASS",
            "DATABASE_URL_SYNC",
            "DATABASE_URL_ARCHIVER",
        ):
            need(
                urlsplit(config[key]).password == "fixture-password",
                "synthetic_config_only",
            )
    for name, purpose in (("backup_receipt", "backup"), ("restore_receipt", "restore")):
        receipt = parse(artifacts[name])
        fields(
            receipt,
            {
                "schema_version",
                "mode",
                "purpose",
                "verified",
                "writes_paused",
                "revision",
                "backup_sha256",
                "config_sha256",
            },
            "receipt_fields",
        )
        need(
            type(receipt["schema_version"]) is int
            and receipt["schema_version"] == 1
            and receipt["mode"] == ("host_observed" if host else "synthetic")
            and receipt["purpose"] == purpose
            and receipt["verified"] is True
            and receipt["writes_paused"] is True
            and receipt["revision"] == BEFORE
            and receipt["backup_sha256"] == permit["backup_sha256"]
            and receipt["config_sha256"] == sha(artifacts["config"]),
            "receipt_binding",
        )
    tree = parse(artifacts["migration_tree"])
    fields(tree, {"source_commit", "source_tree", "env_sha256", "nodes"}, "tree_fields")
    need(
        tree["source_commit"] == data["source_commit"]
        and tree["source_tree"] == data["source_tree"]
        and tree["env_sha256"] == sha(artifacts["env_py"]),
        "tree_binding",
    )
    need(type(tree["nodes"]) is list and 2 <= len(tree["nodes"]) <= 256, "tree_nodes")
    revisions = []
    for index, node in enumerate(tree["nodes"]):
        fields(node, {"revision", "down_revision", "sha256"}, "tree_node_fields")
        need(
            type(node["revision"]) is str
            and re.fullmatch(r"[0-9]{3}_[a-z0-9_]+", node["revision"])
            and is_sha(node["sha256"]),
            "tree_node",
        )
        need(node["down_revision"] == (revisions[-1] if index else None), "tree_chain")
        need(node["revision"] not in revisions, "tree_duplicate")
        revisions.append(node["revision"])
    need(revisions[-2:] == [BEFORE, AFTER], "tree_head")
    return spec_type(canonical(data), tuple(sorted(artifacts.items())))


def validate_spec(spec):
    if type(spec) is HostMigrationSpec:
        return validate_host_policy(spec)
    if type(spec) is LinuxCISpec:
        return validate_linux_ci(spec)
    need(type(spec) is MigrationSpec, "spec_type")
    checked = prepare_policy(spec.data, dict(spec._artifacts))
    need(
        checked.encoded == spec.encoded and checked._artifacts == spec._artifacts,
        "spec_binding",
    )
    return checked


@dataclass(frozen=True)
class HostMigrationSpec(MigrationSpec):
    """Production-only semantic policy; never an executable permit or CI subtype."""

    _binding: bytes = field(default=b"", repr=False)
    _authority: bytes = field(default=b"", repr=False)

    @property
    def sha256(self):
        return sha(
            self.encoded
            + b"\0"
            + self._binding
            + (b"\0" + self._authority if self._authority else b"")
        )


def prepare_host_policy(data, artifacts, package, host_binding, release_spec=None):
    from contracts import TARGET
    from phase1_package_binding import BoundPackage

    need(
        type(package) is BoundPackage
        and package.get("semantic_verified") is True
        and package.get("mode") == "candidate_review",
        "host_full_package_required",
    )
    fields(
        host_binding,
        {
            "target",
            "package_sha256",
            "package_evidence_sha256",
            "source_commit",
            "source_tree",
            "backend_image",
            "container_id",
            "network_id",
            "approval_sha256",
            "baseline_sha256",
            "manual_freeze_sha256",
            "frontend_image",
            "boot_id",
        },
        "host_binding_fields",
    )
    need(host_binding["target"] == TARGET, "host_target")
    need(
        host_binding["package_sha256"] == package.get("archive_sha256")
        and is_sha(host_binding["package_sha256"])
        and host_binding["package_evidence_sha256"] == package.sha256,
        "host_package_binding",
    )
    need(
        all(
            host_binding[k] == package.get(k) == data[k]
            for k in ("source_commit", "source_tree")
        ),
        "host_source_binding",
    )
    need(
        artifacts["env_py"] == package.member("backend/alembic/env.py"),
        "host_original_env",
    )
    migration_pins = {
        pin["sha256"]
        for name, pin in package.get("source_members").items()
        if name.startswith("backend/alembic/versions/")
        and name.endswith(".py")
        and not name.endswith("/__init__.py")
    }
    need(
        {node["sha256"] for node in parse(artifacts["migration_tree"])["nodes"]}
        == migration_pins,
        "host_migration_source_hashes",
    )
    spec = _prepare_policy(data, artifacts, HostMigrationSpec)
    authority = b""
    if release_spec is not None:
        from host_release_input import validate_release

        validate_release(release_spec)
        need(release_spec.package == package, "migration_release_package")
        authority = canonical(
            {"data": release_spec.data, "binding": parse(release_spec._binding)}
        )
    return validate_host_policy(
        HostMigrationSpec(
            spec.encoded, spec._artifacts, canonical(host_binding), authority
        )
    )


def validate_host_policy(spec):
    from contracts import TARGET

    need(type(spec) is HostMigrationSpec, "host_spec_type")
    checked = _prepare_policy(spec.data, dict(spec._artifacts), HostMigrationSpec)
    need(
        checked.encoded == spec.encoded and checked._artifacts == spec._artifacts,
        "host_policy_binding",
    )
    binding = parse(spec._binding)
    if spec._authority:
        authority = parse(spec._authority)
        fields(authority, {"data", "binding"}, "migration_release_authority")
        origin, original_binding = authority["data"], authority["binding"]
        need(
            origin["mode"] == "host_release_candidate"
            and origin["live_enabled"] is False
            and origin["deployable"] is False
            and origin["config_sha256"] == spec.data["hashes"]["config"]
            and origin["approval_ref"] == parse(spec.artifact("permit"))["approval_ref"]
            and all(
                origin[k] == spec.data[k]
                for k in (
                    "source_commit",
                    "source_tree",
                    "release_id",
                    "from_revision",
                    "target_revision",
                )
            ),
            "migration_release_data",
        )
        need(
            all(
                original_binding[k] == binding[k]
                for k in (
                    "target",
                    "package_sha256",
                    "package_evidence_sha256",
                    "baseline_sha256",
                    "approval_sha256",
                    "manual_freeze_sha256",
                    "boot_id",
                    "network_id",
                )
            ),
            "migration_release_binding",
        )
    fields(
        binding,
        {
            "target",
            "package_sha256",
            "package_evidence_sha256",
            "source_commit",
            "source_tree",
            "backend_image",
            "container_id",
            "network_id",
            "approval_sha256",
            "baseline_sha256",
            "manual_freeze_sha256",
            "frontend_image",
            "boot_id",
        },
        "host_binding_fields",
    )
    need(
        binding["target"] == TARGET
        and all(binding[k] == spec.data[k] for k in ("source_commit", "source_tree")),
        "host_target_binding",
    )
    for key in (
        "package_sha256",
        "package_evidence_sha256",
        "container_id",
        "network_id",
        "approval_sha256",
        "baseline_sha256",
        "manual_freeze_sha256",
    ):
        need(is_sha(binding[key]), "host_binding_hash")
    for image_key in ("backend_image", "frontend_image"):
        need(
            type(binding[image_key]) is str
            and binding[image_key].startswith("sha256:")
            and is_sha(binding[image_key][7:]),
            "host_image",
        )
    need(
        type(binding["boot_id"]) is str
        and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", binding["boot_id"]
        ),
        "host_boot_id",
    )
    return spec


def require_host_authority(spec):
    # IO is isolated in the fixed host adapter; pure spec validation grants no IO.
    from host_release_input import validate_host_input

    validate_host_input(spec)
    from host_authority import verify_authority

    return verify_authority(spec)


@dataclass(frozen=True)
class LinuxCISpec(MigrationSpec):
    """Disposable CI only. No production permit is accepted or generated."""


def ci_config():
    config = {
        "ENVIRONMENT": "production",
        "LOG_LEVEL": "ERROR",
        "DB_ECHO": "false",
        "DB_POOL_SIZE": "2",
        "DB_MAX_OVERFLOW": "0",
        "CORS_ALLOWED_ORIGINS": "https://47.110.76.9",
        "CREDENTIAL_MASTER_KEY": "fixture-not-a-production-key",
        "JWT_SECRET": "fixture-not-a-production-secret-at-least-32-characters",
        "R2_ENDPOINT_URL": "https://fixture.invalid",
        "R2_ACCESS_KEY_ID": "fixture-key",
        "R2_SECRET_ACCESS_KEY": "fixture-secret",
        "R2_BUCKET_PRIVATE": "fixture-private",
    }
    for key, role, driver in (
        ("APP", "app", "asyncpg"),
        ("BYPASS", "bypass", "asyncpg"),
        ("SYNC", "bypass", "psycopg2"),
        ("ARCHIVER", "archiver", "asyncpg"),
    ):
        config["DATABASE_URL_" + key] = (
            f"postgresql+{driver}://clothing_{role}:fixture-password@prod-db:5432/{DATABASE}"
        )
    for key, number in (("CACHE", 0), ("CELERY_BROKER", 1), ("CELERY_BACKEND", 2)):
        config["REDIS_URL_" + key] = f"redis://prod-redis:6379/{number}"
    return config


def prepare_linux_ci(package, nonce, phase1_artifacts, tools_manifest):
    """Bind a verified original package to a disposable CI run, never authorize live."""
    from phase1_package_binding import BoundCISource, BoundPackage

    need(
        (type(package) is BoundPackage and package.get("semantic_verified") is True)
        or (type(package) is BoundCISource and package.get("source_verified") is True),
        "ci_package_required",
    )
    need(type(nonce) is str and re.fullmatch("[0-9a-f]{32}", nonce), "ci_nonce")
    artifacts = dict(phase1_artifacts)
    fields(artifacts, {"phase1_pins", "phase1_guard"}, "ci_phase1_artifacts")
    pinned_guard(artifacts)
    artifacts["config"] = canonical(ci_config())
    artifacts["env_py"] = package.member("backend/alembic/env.py")
    data = {
        "schema_version": 1,
        "mode": "linux_ci",
        "data_kind": "synthetic",
        "deployable": False,
        "live_enabled": False,
        "source_commit": package.get("source_commit"),
        "source_tree": package.get("source_tree"),
        "package_sha256": package.get("archive_sha256"),
        "package_semantics_verified": type(package) is BoundPackage,
        "package_evidence_sha256": package.sha256,
        "release_id": "flow78-" + package.get("source_commit")[:12],
        "operation": "migrate",
        "from_revision": BEFORE,
        "target_revision": AFTER,
        "lock_key": LOCK_KEY,
        "timeouts": TIMEOUTS,
        "nonce": nonce,
        "source_members": package.get("source_members"),
        "tools": tools_manifest,
        "hashes": {k: sha(v) for k, v in artifacts.items()},
    }
    return validate_linux_ci(
        LinuxCISpec(canonical(data), tuple(sorted(artifacts.items())))
    )


def validate_linux_ci(spec):
    need(type(spec) is LinuxCISpec, "ci_spec_type")
    data = spec.data
    fields(
        data,
        {
            "schema_version",
            "mode",
            "data_kind",
            "deployable",
            "live_enabled",
            "source_commit",
            "source_tree",
            "package_sha256",
            "package_evidence_sha256",
            "package_semantics_verified",
            "release_id",
            "operation",
            "from_revision",
            "target_revision",
            "lock_key",
            "timeouts",
            "nonce",
            "source_members",
            "tools",
            "hashes",
        },
        "ci_fields",
    )
    need(
        data["schema_version"] == 1
        and type(data["schema_version"]) is int
        and data["mode"] == "linux_ci"
        and data["data_kind"] == "synthetic"
        and data["deployable"] is False
        and data["live_enabled"] is False,
        "ci_mode",
    )
    need(
        type(data["nonce"]) is str and re.fullmatch("[0-9a-f]{32}", data["nonce"]),
        "ci_nonce",
    )
    need(
        all(is_sha(data[k], 40) for k in ("source_commit", "source_tree"))
        and is_sha(data["package_evidence_sha256"]),
        "ci_source",
    )
    need(
        (data["package_semantics_verified"] is True and is_sha(data["package_sha256"]))
        or (
            data["package_semantics_verified"] is False
            and data["package_sha256"] is None
        ),
        "ci_package_status",
    )
    need(data["release_id"] == "flow78-" + data["source_commit"][:12], "ci_release")
    need(
        (
            data["operation"],
            data["from_revision"],
            data["target_revision"],
            data["lock_key"],
        )
        == ("migrate", BEFORE, AFTER, LOCK_KEY)
        and type(data["lock_key"]) is int,
        "ci_operation",
    )
    need(
        data["timeouts"] == TIMEOUTS
        and all(type(x) is int for x in data["timeouts"].values()),
        "ci_timeouts",
    )
    fields(
        dict(spec._artifacts),
        {"phase1_pins", "phase1_guard", "config", "env_py"},
        "ci_artifacts",
    )
    fields(data["hashes"], dict(spec._artifacts), "ci_hashes")
    for name, raw in spec._artifacts:
        need(
            type(raw) is bytes and sha(raw) == data["hashes"][name], "ci_artifact_hash"
        )
    guard = pinned_guard(dict(spec._artifacts))
    need(parse(spec.artifact("config")) == ci_config(), "ci_config")
    guard.validate_config(ci_config())
    for section in ("source_members", "tools"):
        entries = data[section]
        need(type(entries) is dict and 1 <= len(entries) <= 4096, "ci_file_manifest")
        for name, pin in entries.items():
            need(
                type(name) is str
                and re.fullmatch("[A-Za-z0-9_./-]+", name)
                and not name.startswith("/")
                and all(p not in ("", ".", "..") for p in name.split("/")),
                "ci_member_name",
            )
            fields(pin, {"bytes", "sha256"}, "ci_pin")
            need(
                type(pin["bytes"]) is int
                and 0 <= pin["bytes"] <= 16 * 1024**2
                and is_sha(pin["sha256"]),
                "ci_pin",
            )
    need(
        data["source_members"].get("backend/alembic/env.py", {}).get("sha256")
        == sha(spec.artifact("env_py")),
        "ci_env_binding",
    )
    need(
        {
            "linux_alembic_runtime.py",
            "linux_transport.py",
            "linux_collectors.py",
            "sql_policy.py",
            "sql_migration_driver.py",
            "migration_process.py",
        }
        <= data["tools"].keys(),
        "ci_tools",
    )
    return spec


def safe_failure(exc):
    """SQLSTATE only: exception messages, SQL and parameters are never emitted."""
    sqlstate = getattr(exc, "sqlstate", None)
    # SQLAlchemy wraps asyncpg failures. Traverse only the bounded exception chain;
    # never include its SQL, parameters, messages or repr in the projection.
    current = exc
    for _ in range(3):
        if type(sqlstate) is str and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            break
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None)
        sqlstate = getattr(current, "sqlstate", None)
    if type(sqlstate) is not str or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        sqlstate = None
    code = "guard_rejected" if isinstance(exc, SqlRejected) else "execution_failed"
    return {"code": code, "sqlstate": sqlstate}
