"""Real disposable PG18.6 collectors/restore, never a host release authorization.

No runtime monkeypatch fabricates SQL rows, clocks, migration, or RLS outcomes.
Only the Docker client destination is changed to this GitHub service. Host entry
points/approval/freeze/HTTP/TTL/R2 are NOT exercised by this finite PG checkpoint.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_acceptance_control as acceptance
import host_backup_control as backup
from contracts import Rejected, canonical, require
from linux_transport import (
    LinuxCITransport,
    LinuxJournal,
    ci_root,
    require_linux,
)
from migration_process import run_guarded_migration
from phase1_package_binding import verify_ci_source
from run_linux import bootstrap, stage
from sql_policy import AFTER, BEFORE, DATABASE

HERE = Path(__file__).resolve().parent
DOCKER = ("/usr/bin/docker", "--host", "unix:///var/run/docker.sock")
ROOT_ROLE = "CREATE ROLE root LOGIN SUPERUSER INHERIT CREATEDB CREATEROLE REPLICATION BYPASSRLS;"


def call(*args, stdin=None, timeout=30):
    try:
        return subprocess.check_output(
            (*DOCKER, *args), input=stdin, timeout=timeout, stderr=subprocess.PIPE
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # This entry handles only disposable synthetic resources. Do not print
        # SQL stdin/argv; preserve a bounded error tail and keep the failure.
        raw = error.stderr or b""
        print(
            json.dumps(
                {
                    "data_kind": "synthetic_subprocess_failure",
                    "error_type": type(error).__name__,
                    "exit_code": getattr(error, "returncode", None),
                    "stderr_tail": raw[-8192:].decode("utf-8", errors="replace"),
                    "stderr_truncated": len(raw) > 8192,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise


def write(path, value):
    raw = canonical(value)
    with path.open("xb") as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


async def seed(spec, output):
    root = ci_root(spec.data["nonce"])
    with (output / "original-066-seed.log").open("xb") as log:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-B",
            str(root / "tools/linux_ci_seed.py"),
            spec.data["nonce"],
            cwd=root / "app/backend",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        try:
            require(
                await asyncio.wait_for(process.wait(), 180) == 0, "ci_original_066_seed"
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise


async def database_time(identity):
    value = json.loads(
        await backup._sql(
            identity,
            b"SELECT json_build_object('utc',to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'));",
        )
    )
    acceptance._utc(value["utc"])
    return value["utc"]


async def restore_failure_evidence(identity, image, data, output):
    """Read only the already-created synthetic full ID; never inspect Env."""
    require(
        re.fullmatch(r"[0-9a-f]{64}", identity)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", image),
        "ci_restore_diagnostic_identity",
    )
    projection = '{"id":{{json .Id}},"image":{{json .Image}},"status":{{json .State.Status}},"running":{{json .State.Running}},"pid":{{json .State.Pid}},"exit_code":{{json .State.ExitCode}},"oom":{{json .State.OOMKilled}},"error":{{json .State.Error}}}'
    state = json.loads(
        await backup._capture(
            DOCKER + ("inspect", "--format", projection, identity), timeout=10
        )
    )
    require(
        state["id"] == identity and state["image"] == image,
        "ci_restore_diagnostic_binding",
    )
    info = data.lstat()
    write(
        output / "restore-failure-state.json",
        {
            "data_kind": "synthetic_restore_failure",
            "container": state,
            "bind_parent": {
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": info.st_mode & 0o777,
            },
        },
    )
    # Both streams are captured; head bounds bytes even for one enormous line.
    # timeout bounds the entire read-only pipeline. A nonzero logs status is
    # diagnostic text only; the original restore failure is always re-raised.
    raw = await backup._capture(
        (
            "/usr/bin/timeout",
            "15",
            "/bin/sh",
            "-c",
            '/usr/bin/docker --host unix:///var/run/docker.sock logs --tail 80 "$1" 2>&1 | /usr/bin/head -c 65536',
            "restore-fixture-diagnostic",
            identity,
        ),
        timeout=20,
    )
    require(len(raw) <= 65536, "ci_restore_diagnostic_bound")
    with (output / "restore-failure.log").open("xb") as target:
        target.write(raw)
    write(
        output / "restore-failure-log-meta.json",
        {
            "container_id": identity,
            "bytes": len(raw),
            "possibly_truncated": len(raw) == 65536,
            "tail_lines": 80,
        },
    )


async def restore(identity, image, temporary, output):
    """Real dump and same isolation/init/restore primitives as host module.

    Fixture DB root ownership is normalized before seed, so DB/extension owner
    comparison is meaningful. This is not original API backup.py or R2 proof.
    """
    postgres_uid = await backup._postgres_uid(identity)
    entrypoint = await backup._capture(
        DOCKER + ("exec", identity, "cat", "/usr/local/bin/docker-entrypoint.sh"),
        timeout=10,
    )
    require(0 < len(entrypoint) <= 65536, "ci_pg_entrypoint_bound")
    with (output / "pg-original-entrypoint.sh").open("xb") as public_script:
        public_script.write(entrypoint)
    write(
        output / "pg-runtime-public.json",
        {
            "data_kind": "synthetic_image_public_runtime",
            "service_id": identity,
            "image_id": image,
            "postgres_uid": postgres_uid,
            "new_data_initial_owner": [0, 0],
            "new_data_ready_owner": [postgres_uid, 0],
            "entrypoint_bytes": len(entrypoint),
            "entrypoint_sha256": hashlib.sha256(entrypoint).hexdigest(),
        },
    )
    before = await backup._database_snapshot(identity)
    dump = temporary / "database.dump"
    with dump.open("xb") as target:
        await asyncio.to_thread(
            subprocess.run,
            (
                *DOCKER,
                "exec",
                identity,
                "pg_dump",
                "--format=custom",
                "--host=/var/run/postgresql",
                "--username=root",
                "--dbname=" + DATABASE,
            ),
            stdout=target,
            stderr=subprocess.PIPE,
            timeout=180,
            check=True,
        )
    require(0 < dump.stat().st_size <= 64 * 1024**2, "ci_dump_bound")
    dump_hash = hashlib.sha256(dump.read_bytes()).hexdigest()
    data = temporary / "data"
    data.mkdir(mode=0o700)
    inode = (data.stat().st_dev, data.stat().st_ino)
    data_identity = {"path": str(data), "device": inode[0], "inode": inode[1]}
    backup._data_identity(data_identity)
    owned = None
    started = time.monotonic()
    failed = False
    try:
        owned = (
            call(
                "create",
                "--name",
                "flow78-acceptance-ci-" + secrets.token_hex(8),
                "--restart=no",
                "--pull=never",
                "--network=none",
                "--memory=1536m",
                "--cpus=1",
                "--cap-drop=ALL",
                "--cap-add=CHOWN",
                "--cap-add=SETUID",
                "--cap-add=SETGID",
                "--security-opt=no-new-privileges=true",
                "--tmpfs",
                backup.restore_drill.SOCKET_TMPFS,
                "--mount",
                "type=bind,src=" + str(data) + ",dst=/var/lib/postgresql",
                "--mount",
                "type=bind,src=" + str(dump) + ",dst=/input/database.dump,readonly",
                "-e",
                backup.restore_drill.PGDATA_ENV,
                "-e",
                "POSTGRES_USER=root",
                "-e",
                "POSTGRES_DB=" + DATABASE,
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                image,
                "postgres",
                "-c",
                "listen_addresses=",
            )
            .decode()
            .strip()
        )
        require(re.fullmatch(r"[0-9a-f]{64}", owned), "ci_restore_full_id")
        call("start", owned)
        await backup._ready(owned)
        backup._data_identity(data_identity, owner=(postgres_uid, 0))
        projection = '{"id":{{json .Id}},"image":{{json .Image}},"network":{{json .HostConfig.NetworkMode}},"ports":{{json .HostConfig.PortBindings}},"privileged":{{json .HostConfig.Privileged}},"cap_add":{{json .HostConfig.CapAdd}},"cap_drop":{{json .HostConfig.CapDrop}}}'
        isolated = json.loads(call("inspect", "--format", projection, owned))
        require(
            isolated["id"] == owned
            and isolated["image"] == image
            and isolated["network"] == "none"
            and isolated["ports"] in ({}, None)
            and isolated["privileged"] is False
            and set(isolated["cap_add"]) == {"CHOWN", "SETUID", "SETGID"}
            and isolated["cap_drop"] == ["ALL"],
            "ci_restore_isolation",
        )
        mounts = await backup._mounts(owned)
        expected_mounts = [
            {
                "source": str(data),
                "destination": "/var/lib/postgresql",
                "rw": True,
                "type": "bind",
            },
            {
                "source": str(dump),
                "destination": "/input/database.dump",
                "rw": False,
                "type": "bind",
            },
            {
                "source": "",
                "destination": "/var/run/postgresql",
                "rw": True,
                "type": "tmpfs",
            },
        ]
        require(
            mounts["id"] == owned
            and mounts["image"] == image
            and mounts["tmpfs"]
            == {
                "/var/run/postgresql": backup.restore_drill.SOCKET_TMPFS.split(":", 1)[
                    1
                ]
            }
            and sorted(mounts["mounts"], key=lambda row: row["destination"])
            == sorted(expected_mounts, key=lambda row: row["destination"]),
            "ci_restore_exact_mounts",
        )
        isolated["mounts"] = mounts
        require(
            (data.stat().st_dev, data.stat().st_ino) == inode,
            "ci_restore_data_identity",
        )
        await backup._sql(owned, backup.ROLE_SQL)
        call(
            "exec",
            "-u",
            "0",
            owned,
            "pg_restore",
            "--exit-on-error",
            "--host=/var/run/postgresql",
            "--username=root",
            "--dbname=" + DATABASE,
            "/input/database.dump",
            timeout=180,
        )
        after = await backup._database_snapshot(owned)
        backup._data_identity(data_identity, owner=(postgres_uid, 0))
        write(
            output / "restore-comparison.json",
            {"before": before, "after": after, "isolation": isolated},
        )
        require(before == after, "ci_restore_complete_snapshot")
        require(
            await backup._database_snapshot(identity) == before,
            "ci_source_changed_during_restore",
        )
        require(
            hashlib.sha256(dump.read_bytes()).hexdigest() == dump_hash,
            "ci_dump_changed",
        )
        return {
            "dump_sha256": dump_hash,
            "dump_bytes": dump.stat().st_size,
            "comparison": before,
            "restore_seconds": time.monotonic() - started,
            "image_id": image,
            "isolated_full_id": owned,
            "data_owner": {"uid": postgres_uid, "gid": 0, "mode": 0o700},
            "outer_directory_root_private": True,
            "original_api_backup_tested": False,
            "r2_tested": False,
        }
    except BaseException:
        failed = True
        if owned is not None and re.fullmatch(r"[0-9a-f]{64}", owned):
            try:
                await restore_failure_evidence(owned, image, data, output)
            except BaseException as diagnostic_error:  # noqa: BLE001 - preserve original restore failure
                print(
                    json.dumps(
                        {
                            "data_kind": "synthetic_restore_diagnostic_failure",
                            "error_type": type(diagnostic_error).__name__,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
        raise
    finally:
        if owned is not None and re.fullmatch(r"[0-9a-f]{64}", owned):
            # This exact ID was created above, never a container name or glob.
            try:
                call("rm", "-f", owned)
            except BaseException:
                if not failed:
                    raise
                print(
                    '{"data_kind":"synthetic_restore_cleanup_failed_original_error_preserved"}',
                    file=sys.stderr,
                    flush=True,
                )


async def negative_cases(identity, spec, before, after, migrated, counts, window):
    """Mutate only fixture rows, collect actual SQL again, require precise errors."""
    checks = {}

    async def refresh(*names):
        rows = deepcopy(after)
        for name in names:
            rows[name] = await acceptance._rows(
                identity, acceptance.EXTRA[name], acceptance._budget()
            )
        return rows

    def rejected(name, expected, fn):
        try:
            fn()
        except Rejected as error:
            require(str(error) == expected, "ci_negative_wrong_reason")
            checks[name] = {"rejected": True, "code": expected, "actual_sql": True}
        else:
            raise AssertionError("negative accepted: " + name)

    freight_id = "80000000-0000-0000-0000-000000000001"
    for column, invalid, original, label in (
        ("amount", "31", "30", "r33_amount"),
        (
            "tenant_id",
            "'10000000-0000-0000-0000-000000000002'",
            "'10000000-0000-0000-0000-000000000001'",
            "r33_tenant",
        ),
    ):
        await backup._sql(
            identity,
            f"UPDATE return_freight_payment SET {column}={invalid} WHERE id='{freight_id}';".encode(),
        )
        try:
            rows = await refresh("freight", "freight_violations")
            rejected(
                label,
                "acceptance_freight_relationship",
                lambda observed=rows: acceptance._financial(observed, observed),
            )
        finally:
            await backup._sql(
                identity,
                f"UPDATE return_freight_payment SET {column}={original} WHERE id='{freight_id}';".encode(),
            )
    await backup._sql(
        identity,
        "UPDATE promotion SET return_shipping_payer='到付' WHERE id='40000000-0000-0000-0000-000000000004';".encode(),
    )
    try:
        rows = await refresh("finance_promotions", "freight_violations")
        rejected(
            "r33_payer",
            "acceptance_freight_relationship",
            lambda: acceptance._financial(rows, rows),
        )
    finally:
        await backup._sql(
            identity,
            "UPDATE promotion SET return_shipping_payer='博主垫付' WHERE id='40000000-0000-0000-0000-000000000004';".encode(),
        )
    await backup._sql(identity, b"ALTER ROLE clothing_app BYPASSRLS;")
    try:
        try:
            await acceptance._app_rls(identity, after)
        except Rejected as error:
            require(str(error) == "restore_app_role", "ci_negative_wrong_role_reason")
            checks["app_bypass_rls"] = {
                "rejected": True,
                "code": str(error),
                "actual_sql": True,
            }
        else:
            raise AssertionError("bypass app role accepted")
    finally:
        await backup._sql(identity, b"ALTER ROLE clothing_app NOBYPASSRLS;")
    await backup._sql(
        identity,
        b"INSERT INTO role_permission(id,role_id,permission_id) SELECT 'd0000000-0000-0000-0000-000000000001',r.id,p.id FROM role r,permission p WHERE r.code='pr' AND p.scope='promotion_retro:write';",
    )
    try:
        rows = await refresh("role_access")
        rejected(
            "unexpected_role",
            "acceptance_role_delta",
            lambda: acceptance._roles(before["rows"], rows),
        )
    finally:
        await backup._sql(
            identity,
            b"DELETE FROM role_permission WHERE id='d0000000-0000-0000-0000-000000000001';",
        )
    await backup._sql(
        identity,
        b"UPDATE promotion SET id='40000000-0000-0000-0000-999999999999' WHERE id='40000000-0000-0000-0000-000000004999';",
    )
    try:
        rows = await refresh("promotions")
        rejected(
            "same_count_changed_id",
            "acceptance_stable_promotions",
            lambda: acceptance._after_projection(
                before, rows, migrated, counts, window, {"gate_closed": True}
            ),
        )
    finally:
        await backup._sql(
            identity,
            b"UPDATE promotion SET id='40000000-0000-0000-0000-000000004999' WHERE id='40000000-0000-0000-0000-999999999999';",
        )
    await backup._sql(
        identity,
        b"ALTER TABLE promotion DROP CONSTRAINT ck_promotion_ck_promotion_retro_status; ALTER TABLE promotion ADD CONSTRAINT ck_promotion_ck_promotion_retro_status CHECK (true);",
    )
    try:
        invalid = await acceptance._migration_rows(
            spec, identity, before, window, acceptance._budget()
        )
        projected = acceptance._after_projection(
            before, after, invalid, counts, window, {"gate_closed": True}
        )
        rejected(
            "weakened_constraint",
            "schema_or_gate",
            lambda: acceptance.verify_acceptance(before["projection"], projected),
        )
    finally:
        await backup._sql(
            identity,
            "ALTER TABLE promotion DROP CONSTRAINT ck_promotion_ck_promotion_retro_status; ALTER TABLE promotion ADD CONSTRAINT ck_promotion_ck_promotion_retro_status CHECK (retro_status IN ('未开始','待复盘','已完成'));".encode(),
        )
    return checks


async def exercise(policy, identity, image, output, temporary):
    await seed(policy, output)
    await backup._sql(identity, (HERE / "acceptance_fixture.sql").read_bytes())
    marker = json.loads(
        await backup._sql(
            identity,
            b"SELECT json_build_object('nonce',(SELECT nonce FROM flow78_disposable_ci),'count',(SELECT count(*) FROM promotion));",
        )
    )
    require(
        marker == {"nonce": policy.data["nonce"], "count": 5000},
        "ci_fixture_identity_and_size",
    )
    tools = tuple(
        (name, (HERE.parent / name).read_bytes()) for name in acceptance.SQL_PINS
    )
    # Only pure collectors need public SQL bytes, not a fabricated HostReleaseSpec.
    collector = SimpleNamespace(_tools=tools)
    started = time.monotonic()
    before_rows = await acceptance._base(collector, identity, acceptance._budget())
    before = {
        "rows": before_rows,
        "projection": acceptance._before_projection(before_rows),
    }
    require(
        before["projection"]["head"] == BEFORE
        and len(before_rows["promotions"]) == 5000,
        "ci_before_collection",
    )
    raw = canonical(before)
    require(1024**2 < len(raw) <= acceptance.MAX_BYTES, "ci_large_receipt_required")
    pin = write(output / "synthetic-schema-before.json", before)
    require(
        acceptance.parse_private_receipt(
            (output / "synthetic-schema-before.json").read_bytes()
        )
        == before,
        "ci_large_receipt_roundtrip",
    )
    before_seconds = time.monotonic() - started
    restored = await restore(identity, image, temporary, output)
    interval = {
        "db_before_utc": await database_time(identity),
        "started_monotonic_ns": time.monotonic_ns(),
    }
    journal = LinuxJournal(policy.data["nonce"])
    result = await run_guarded_migration(policy, LinuxCITransport(policy, journal))
    require(
        result["outcome"] == "verified"
        and result["host_lock_released"] is True
        and result["post_head"] == AFTER,
        "ci_original_067_migration",
    )
    interval.update(
        db_after_utc=await database_time(identity),
        completed_monotonic_ns=time.monotonic_ns(),
    )
    started = time.monotonic()
    after = await acceptance._base(collector, identity, acceptance._budget())
    rows = await acceptance._migration_rows(
        collector, identity, before, interval, acceptance._budget()
    )
    counts = {
        str(i): await acceptance._rows(identity, q, acceptance._budget())
        for i, q in enumerate(
            acceptance._statements(collector, "acceptance_queries.sql")[:4]
        )
    }
    projected = acceptance._after_projection(
        before, after, rows, counts, interval, {"gate_closed": True}
    )
    validated = acceptance.verify_acceptance(before["projection"], projected)
    roles, expected_roles = acceptance._roles(before_rows, after)
    require(roles == expected_roles, "ci_actual_role_delta")
    rls = await acceptance._app_rls(identity, after)
    require(
        rls["two_real_tenants"] is True and rls["populated_tenant_count"] == 2,
        "ci_two_fixture_tenants_rls",
    )
    financial = acceptance._financial(before_rows, after)
    require(financial["unpublished_count"] == 2000, "ci_unpublished_history_included")
    after_seconds = time.monotonic() - started
    write(
        output / "synthetic-schema-after.json",
        {
            "base": after,
            "migration_rows": rows,
            "projection": projected,
            "roles": roles,
            "rls": rls,
        },
    )
    negatives = await negative_cases(
        identity, collector, before, after, rows, counts, interval
    )
    return {
        "mode": "real_pg18_6_synthetic_dataset",
        "source_commit": policy.data["source_commit"],
        "source_tree": policy.data["source_tree"],
        "fixture_rows": 5000,
        "private_before": pin,
        "complete_before_seconds": before_seconds,
        "complete_after_seconds": after_seconds,
        "migration": {
            "outcome": result["outcome"],
            "policy_sha256": policy.sha256,
            "time_window": interval,
        },
        "validation": validated,
        "financial": financial,
        "rls": rls,
        "restore": restored,
        "negative_cases": negatives,
        "real_sql": True,
        "real_original_migration": True,
        "real_independent_app_socket": True,
        "host_approval_journal_freeze_executed": False,
        "host_freeze_value_for_pure_schema_validator": "explicit_fixture_true_no_host_proof",
        "http_tls_ttl_executed": False,
        "original_api_backup_r2_executed": False,
        "production_access": False,
        "production_29_actions_verified": False,
        "live_verified": False,
    }


def main():
    require_linux()
    require(
        os.geteuid() == 0 and os.environ.get("GITHUB_ACTIONS") == "true",
        "ci_disposable_root_runner_only",
    )
    parser = argparse.ArgumentParser()
    for name in ("repository", "commit", "tree", "pg-container", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{64}", args.pg_container), "ci_service_full_id")
    source = verify_ci_source(Path(args.repository), args.commit, args.tree)
    service = json.loads(
        call(
            "inspect",
            "--format",
            '{"id":{{json .Id}},"image":{{json .Image}},"reference":{{json .Config.Image}},"running":{{json .State.Running}}}',
            args.pg_container,
        )
    )
    require(
        service["id"] == args.pg_container
        and service["reference"] == "postgres:18.6"
        and service["running"] is True,
        "ci_pg_service_identity",
    )
    output = Path(args.output).resolve()
    require(
        output.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()),
        "ci_output_scope",
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    nonce = secrets.token_hex(16)
    policy = stage(source, nonce)
    asyncio.run(bootstrap(nonce))
    # Fixture bootstrap only, before application seed: normalize this database,
    # its nonce marker and the just-created user extension. Never REASSIGN the
    # bootstrap superuser's system objects. DROP has no CASCADE, so unexpected
    # dependencies fail. Recreate pg_trgm as root to match real restore owners.
    call(
        "exec",
        "-i",
        args.pg_container,
        "psql",
        "-X",
        "-q",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        DATABASE,
        stdin=(
            ROOT_ROLE
            + "ALTER DATABASE "
            + DATABASE
            + " OWNER TO root; ALTER TABLE public.flow78_disposable_ci OWNER TO root; "
            + "DROP EXTENSION pg_trgm; SET ROLE root; CREATE EXTENSION pg_trgm; RESET ROLE; "
            + "GRANT CREATE ON SCHEMA public TO clothing_bypass;"
        ).encode(),
    )
    original_docker = backup.DOCKER
    backup.DOCKER = DOCKER
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(
            prefix="flow78-acceptance-pg-ci-", dir="/root"
        ) as temporary:
            result = asyncio.run(
                exercise(
                    policy, args.pg_container, service["image"], output, Path(temporary)
                )
            )
        result["elapsed_seconds"] = time.monotonic() - started
        write(output / "result.json", result)
        print(json.dumps(result, sort_keys=True))
    finally:
        backup.DOCKER = original_docker


if __name__ == "__main__":
    main()
