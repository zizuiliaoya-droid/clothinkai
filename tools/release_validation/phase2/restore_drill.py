"""Isolated restore description and independent owner/ACL/RLS/dataset comparisons."""

import re

from contracts import BEFORE, digest, exact, require, sha

R = "/srv/clothinkai/production-20261018"
PGDATA_ENV = "PGDATA=/var/lib/postgresql"
SOCKET_TMPFS = "/var/run/postgresql:rw,noexec,nosuid,size=16m,mode=3775"


def plan(commit, dump_name, image):
    sha(commit, 40)
    require(
        re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}\.dump", dump_name), "backup_name"
    )
    require(image.startswith("sha256:"), "pg_image")
    sha(image[7:])
    name = "clothinkai-flow78-drill-" + commit[:12]
    data = R + "/releases/flow78-" + commit[:12] + "/drill/data"
    argv = (
        "docker",
        "create",
        "--name",
        name,
        "--restart=no",
        "--network=none",
        "--pull=never",
        "--memory=1536m",
        "--cpus=1",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--tmpfs",
        SOCKET_TMPFS,
        "--mount",
        "type=bind,src=" + data + ",dst=/var/lib/postgresql",
        "--mount",
        "type=bind,src="
        + R
        + "/backups/"
        + dump_name
        + ",dst=/input/database.dump,readonly",
        "-e",
        PGDATA_ENV,
        image,
    )
    return {
        "network": "none",
        "ports": [],
        "data_path": data,
        "automatic_remove": False,
        "require_absent_target": True,
        "restore_seconds": 1800,
        "create_argv": argv,
        "restore_argv": (
            "docker",
            "exec",
            name,
            "pg_restore",
            "--exit-on-error",
            "--host=/var/run/postgresql",
            "--username=root",
            "--dbname=clothing_erp_prod_20261018",
            "/input/database.dump",
        ),
        "executable": False,
        "requires_reviewed_role_bootstrap": True,
    }


def verify(observed, expected):
    exact(observed, expected)
    require(
        observed["network_mode"] == "none"
        and observed["ports"] == []
        and observed["production_mounts"] == []
        and observed["data_path"].endswith("/drill/data"),
        "drill_isolation",
    )
    require(
        observed["pg_version"] == "18.6"
        and observed["head"] == BEFORE
        and type(observed["exit_code"]) is int
        and observed["exit_code"] == 0,
        "restore_incomplete",
    )
    require(
        observed["app_superuser"] is False
        and observed["app_bypassrls"] is False
        and observed["rls_app_checked"] is True
        and observed["writes_still_paused"] is True,
        "restored_role_or_write_gate",
    )
    for key in (
        "container",
        "data_path",
        "dump_sha256",
        "schema_sha256",
        "rows_sha256",
        "acl_sha256",
        "owners_sha256",
        "roles_sha256",
        "references_sha256",
        "original_db_id",
        "original_redis_id",
    ):
        require(observed[key] == expected[key], "restore_baseline_" + key)
    return {
        "dump_sha256": observed["dump_sha256"],
        "receipt_sha256": digest(observed),
        "mode": "synthetic",
    }
