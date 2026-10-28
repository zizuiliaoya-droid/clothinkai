"""Fixed host baseline, mount/role/image/TLS checks before maintenance mutations."""

import os
import time

from contracts import BEFORE, canonical, digest, exact, require
from dashboard_contract import CURRENT_RELEASE, ERP_NETWORKS, NETWORK, NETWORK_ID, verify_members
from fixed_host_transport import ROLES, ROOT, collect
from host_backup_control import PG_IMAGE, _directory, _mounts, _read, _script_pins, _sql
from host_release_input import require_step, validate_release
from sql_policy import parse, sha

ROLE_SQL = b"""BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object('version',current_setting('server_version_num'),
'heads',(SELECT json_agg(version_num ORDER BY version_num) FROM alembic_version),
'roles',(SELECT json_object_agg(rolname,json_build_object('superuser',rolsuper,'bypassrls',rolbypassrls,'createdb',rolcreatedb,'createrole',rolcreaterole))
 FROM pg_roles WHERE rolname IN ('clothing_app','clothing_bypass','clothing_archiver')),
'promotion_owner',(SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.promotion'::regclass),
'database_bytes',pg_database_size(current_database()));
ROLLBACK;
"""


def mount_contract(role, row):
    """Exact current 067 bind sources and destinations; nested overlays also fail."""
    expected = {
        "db": [
            ("data", "/var/lib/postgresql", True),
            ("db-input", "/input", False),
            ("db-secrets", "/run/db", False),
            ("backups", "/backups", False),
        ],
        "redis": [("redis", "/data", True)],
        "web": [
            (CURRENT_RELEASE.removeprefix(ROOT + "/") + "/web-control", "/opt/production", False),
            ("acme", "/var/www/acme", False),
            ("letsencrypt", "/etc/letsencrypt", False),
        ],
    }
    app = [
        ("build/control", "/opt/production", False),
        ("secrets", "/run/production", False),
        ("backups", "/run/backups", False),
    ]
    for name in ("api", "worker", "beat"):
        expected[name] = app + [
            (CURRENT_RELEASE.removeprefix(ROOT + "/") + "/control", "/opt/update", False),
            (CURRENT_RELEASE.removeprefix(ROOT + "/") + "/guards/" + name, "/run/release-guard", False),
        ] + (
            [("beat", "/var/lib/celery", True)] if name == "beat" else []
        )
    wanted = [
        {
            "source": ROOT + "/" + source,
            "destination": destination,
            "rw": rw,
            "type": "bind",
        }
        for source, destination, rw in expected[role]
    ]
    actual = [m for m in row["mounts"] if m["type"] != "tmpfs"]
    require(
        sorted(actual, key=lambda r: r["destination"])
        == sorted(wanted, key=lambda r: r["destination"]),
        "preflight_mounts",
    )
    tmpfs = [m for m in row["mounts"] if m["type"] == "tmpfs"]
    require(
        all(m["destination"] == "/tmp" for m in tmpfs) and len(tmpfs) <= 1,
        "preflight_tmpfs",
    )
    if role == "web":
        exact(row["ports"], ("80/tcp", "443/tcp"))
        for port, values in row["ports"].items():
            require(
                type(values) is list and 1 <= len(values) <= 2, "preflight_web_bindings"
            )
            require(
                all(
                    v["HostPort"] == port.split("/")[0]
                    and v["HostIp"] in ("", "0.0.0.0", "::")
                    for v in values
                ),
                "preflight_public_ports",
            )
    else:
        require(row["ports"] in ({}, None), "preflight_unexpected_ports")
    return row


def database_contract(row):
    exact(row, ("version", "heads", "roles", "promotion_owner", "database_bytes"))
    require(
        row["version"] == "180006"
        and row["heads"] == [BEFORE]
        and row["promotion_owner"] == "clothing_bypass",
        "preflight_database",
    )
    require(
        row["roles"]
        == {
            name: {
                "superuser": False,
                "bypassrls": name == "clothing_bypass",
                "createdb": False,
                "createrole": False,
            }
            for name in ("clothing_app", "clothing_bypass", "clothing_archiver")
        },
        "preflight_roles",
    )
    require(
        type(row["database_bytes"]) is int and row["database_bytes"] > 0,
        "preflight_database_size",
    )
    return row


async def preflight(spec, journal, baseline):
    require_step(spec, journal, "PREFLIGHT")
    validate_release(spec)
    require(
        digest(baseline) == parse(spec._binding)["baseline_sha256"],
        "preflight_baseline",
    )
    rows, mounts = {}, {}
    for role in ROLES:
        row = (await collect("inspect", role))["facts"]
        require(
            row["id"] == baseline[role]
            and row["running"] is True
            and row["pid"] > 0
            and row["paused"] is False
            and row["restarting"] is False
            and row["oom"] is False
            and row["restarts"] == 0,
            "preflight_container_state",
        )
        networks = {
            k: v
            for k, v in ERP_NETWORKS.items()
            if role not in ("db", "redis") or "internal" in k
        }
        if role == "web":
            networks[NETWORK] = NETWORK_ID
        require(row["networks"] == networks, "preflight_network_membership")
        mounts[role] = mount_contract(role, await _mounts(row["id"]))
        require(
            mounts[role]["id"] == row["id"] and mounts[role]["image"] == row["image"],
            "preflight_mount_identity",
        )
        rows[role] = row
    require(rows["db"]["image"] == PG_IMAGE, "preflight_pg_image")
    board = (await collect("dashboard_board", "web"))["facts"]
    network = (await collect("dashboard_network", "web"))["facts"]
    verify_members(network, board, rows["web"]["id"])
    database = database_contract(
        parse(await _sql(baseline["db"], ROLE_SQL, timeout=15))
    )
    _script_pins()
    directory = _directory(ROOT + "/secrets")
    try:
        config = _read(directory, "runtime-config.json", 65536)
        require(sha(config) == spec.data["config_sha256"], "preflight_runtime_config")
    finally:
        os.close(directory)
    directory = _directory(ROOT)
    try:
        space = os.fstatvfs(directory)
        free = space.f_bavail * space.f_frsize
    finally:
        os.close(directory)
    require(
        free >= 10 * 1024**3 + 2 * database["database_bytes"],
        "preflight_backup_drill_space",
    )
    from asyncio import to_thread

    from public_gate_observer import observe_dashboard, observe_preflight_gate

    public = await to_thread(observe_preflight_gate)
    require(
        public["tls_verified"]
        and all(public["public_status"][p] == 200 for p in ("/", "/health", "/ready")),
        "preflight_public_health",
    )
    dashboard = await to_thread(observe_dashboard)
    from host_open_control import legacy_operations

    facts = {
        "mode": "host_observed",
        "containers": rows,
        "mounts": mounts,
        "database": database,
        "dashboard_network": network,
        "dashboard_board": board,
        "public": public,
        "dashboard": dashboard,
        "free_bytes": free,
        "config_sha256": spec.data["config_sha256"],
        "observed_monotonic_ns": time.monotonic_ns(),
        "legacy_operations": legacy_operations(),
    }
    journal.directory.create("preflight-observed.json", canonical(facts))
    return facts
