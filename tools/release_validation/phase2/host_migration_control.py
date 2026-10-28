"""One owned migration container, original env.py, durable identity and SQL proof."""

import ast
import asyncio
import os
import time
from datetime import datetime, timezone

from contracts import BEFORE, canonical, digest, exact, require
from contracts import sha as require_sha
from fixed_host_transport import DOCKER, INSPECT, ROOT, _capture, collect
from host_release_input import HostReleaseSpec, require_step, validate_release
from sql_migration_driver import validate_evidence, validate_identity
from sql_policy import LOCK_KEY, TIMEOUTS, parse, prepare_host_policy, sha


def migration_artifacts(spec, journal):
    """Consume only fixed private files, after backup/restore result journal rows."""
    validate_release(spec)
    config = journal.directory.read("runtime-config.json", 65536)
    require(sha(config) == spec.data["config_sha256"], "migration_config_pin")
    permit = journal.directory.read("migrate-permit.json", 65536)
    tools = dict(spec._tools)
    artifacts = {
        "config": config,
        "permit": permit,
        "release": spec.artifact("release"),
        "phase1_pins": tools["frozen_phase1/implementation-pins-069-1028.json"],
        "phase1_guard": tools["frozen_phase1/runtime_guard.py"],
        "env_py": spec.package.member("backend/alembic/env.py"),
    }
    receipts = [recorded_facts(spec, journal, name) for name in ("BACKUP", "RESTORE")]
    require(
        all(r["mode"] == "host_observed" for r in receipts)
        and receipts[0]["dump_sha256"] == receipts[1]["dump_sha256"],
        "migration_backup_restore_binding",
    )
    # The outer state machine requires both completed actions before MIGRATE;
    # retain the original receipt hashes alongside the immutable policy artifact.
    for name, row in zip(("backup", "restore"), receipts):
        artifacts[name + "_receipt"] = canonical(
            {
                "schema_version": 1,
                "mode": "host_observed",
                "purpose": name,
                "verified": True,
                "writes_paused": True,
                "revision": BEFORE,
                "backup_sha256": row["dump_sha256"],
                "config_sha256": sha(config),
            }
        )
    nodes = []
    for name, raw in spec.package._members:
        if (
            not name.startswith("backend/alembic/versions/")
            or not name.endswith(".py")
            or name.endswith("/__init__.py")
        ):
            continue
        values = {}
        for node in ast.parse(raw).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in (
                        "revision",
                        "down_revision",
                    ):
                        values[target.id] = ast.literal_eval(node.value)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in ("revision", "down_revision")
            ):
                values[node.target.id] = ast.literal_eval(node.value)
        exact(values, ("revision", "down_revision"))
        nodes.append({**values, "sha256": sha(raw)})
    ordered, previous = [], None
    while nodes:
        children = [n for n in nodes if n["down_revision"] == previous]
        require(len(children) == 1, "migration_source_linear")
        node = children[0]
        ordered.append(node)
        nodes.remove(node)
        previous = node["revision"]
    artifacts["migration_tree"] = canonical(
        {
            "source_commit": spec.data["source_commit"],
            "source_tree": spec.data["source_tree"],
            "env_sha256": sha(artifacts["env_py"]),
            "nodes": ordered,
        }
    )
    return artifacts


def recorded_facts(spec, journal, step):
    """A later action consumes the exact full observation bound by its result."""
    from contracts import STEPS
    from host_sequence import replay

    rows = journal.read()
    state = replay(spec, rows)
    require(step in state.completed, "migration_prerequisite_incomplete")
    action_id = spec.data["release_id"] + ":" + str(STEPS.index(step)) + ":" + step
    matches = [
        row["payload"]
        for row in rows
        if row["kind"] == "result" and row["payload"]["action_id"] == action_id
    ]
    require(len(matches) == 1, "migration_prerequisite_result")
    facts = parse(journal.directory.read("step-" + step.lower() + "-facts.json"))
    require(digest(facts) == matches[0]["facts_sha256"], "migration_prerequisite_hash")
    return facts


def derive_policy(spec, journal, backend, frontend, container_id):
    artifacts = migration_artifacts(spec, journal)
    binding = {k: v for k, v in parse(spec._binding).items() if k != "tools_sha256"}
    binding.update(
        source_commit=spec.data["source_commit"],
        source_tree=spec.data["source_tree"],
        backend_image=backend,
        frontend_image=frontend,
        container_id=container_id,
    )
    data = {
        "schema_version": 1,
        "mode": "host_candidate",
        "live_enabled": False,
        "deployable": False,
        **{
            k: spec.data[k]
            for k in (
                "source_commit",
                "source_tree",
                "release_id",
                "from_revision",
                "target_revision",
            )
        },
        "operation": "migrate",
        "lock_key": LOCK_KEY,
        "timeouts": TIMEOUTS,
        "hashes": {n: sha(b) for n, b in artifacts.items()},
    }
    return prepare_host_policy(
        data, artifacts, spec.package, binding, release_spec=spec
    )


async def _container(identity):
    require_sha(identity)
    row = parse(
        await _capture(
            DOCKER + ("inspect", "--type", "container", "--format", INSPECT, identity)
        )
    )
    require(row["id"] == identity, "migration_container_identity")
    return row


async def database_clock(database_id):
    from host_backup_control import _sql

    row = parse(
        await _sql(
            database_id,
            b"SELECT json_build_object('utc',to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'));",
        )
    )
    exact(row, ("utc",))
    stamp = datetime.fromisoformat(row["utc"].replace("Z", "+00:00"))
    require(stamp.tzinfo == timezone.utc, "migration_database_clock")
    return row["utc"]


async def migrate(spec, journal, baseline):
    require_step(spec, journal, "MIGRATE")
    require(type(spec) is HostReleaseSpec, "migration_release_input")
    # Validate all prerequisites before container creation; no fabricated future IDs.
    migration_artifacts(spec, journal)
    backend, frontend = (
        recorded_facts(spec, journal, "BUILD_" + kind.upper())["image"]["id"]
        for kind in ("backend", "frontend")
    )
    for role in ("api", "worker", "beat"):
        row = (await collect("inspect", role))["facts"]
        require(
            row["id"] == baseline[role]
            and row["running"] is False
            and row["pid"] == 0
            and row["restart_policy"] == "no",
            "migration_business_stopped",
        )
    database = (await collect("inspect", "db"))["facts"]
    require(database["id"] == baseline["db"], "migration_database_identity")
    from host_session_contract import observe_sessions

    sessions = await observe_sessions(baseline["db"], require_absent=True)
    journal.directory.create("migration-sessions-before.json", canonical(sessions))
    network = parse(spec._binding)["network_id"]
    require(network in database["networks"].values(), "migration_database_network")
    root = ROOT + "/releases/" + spec.data["release_id"]
    name = "clothinkai-flow78-migrate-" + spec.data["source_commit"][:12]
    existing = await _capture(
        DOCKER + ("ps", "-aq", "--no-trunc", "--filter", "name=^/" + name + "$")
    )
    require(existing.strip() == b"", "migration_container_exists")
    from host_build_control import _release_fd

    parent = _release_fd(journal)
    try:
        os.mkdir("migration-output", 0o700, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    mounts = (
        (root + "/state", root + "/state", True),
        (root + "/tools", "/opt/update", True),
        (root + "/migration-output", root + "/migration-output", False),
        ("/var/lib/cloud/data/instance-id", "/var/lib/cloud/data/instance-id", True),
    )
    argv = DOCKER + (
        "create",
        "--name",
        name,
        "--restart=no",
        "--pull=never",
        "--network",
        network,
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--memory=1536m",
        "--cpus=1",
        "--pids-limit=128",
        "--label",
        "clothinkai.flow78.input=" + spec.sha256,
        "--entrypoint",
        "python",
    )
    for source, destination, readonly in mounts:
        argv += (
            "--mount",
            "type=bind,src="
            + source
            + ",dst="
            + destination
            + (",readonly" if readonly else ""),
        )
    argv += (
        backend,
        "-I",
        "-B",
        "/opt/update/host_alembic_runtime.py",
        spec.data["source_commit"],
    )
    journal.directory.create(
        "migration-create-intent.json",
        canonical(
            {
                "name": name,
                "image": backend,
                "network": network,
                "input_sha256": spec.sha256,
            }
        ),
    )
    identity = (await _capture(argv, timeout=30)).decode("ascii").strip()
    require_sha(identity)
    journal.directory.create(
        "migration-container.json",
        canonical({"id": identity, "name": name, "image": backend}),
    )
    policy = derive_policy(spec, journal, backend, frontend, identity)
    packed = {
        "data": policy.data,
        "binding": parse(policy._binding),
        "authority": parse(policy._authority),
        "artifacts": {n: b.decode("utf-8") for n, b in policy._artifacts},
    }
    journal.directory.create("migration-policy.json", canonical(packed))
    before = await _container(identity)
    mode = (
        (
            await _capture(
                DOCKER
                + ("inspect", "--format", "{{.HostConfig.NetworkMode}}", identity)
            )
        )
        .decode("ascii")
        .strip()
    )
    require(
        before["image"] == backend
        and before["running"] is False
        and before["restart_policy"] == "no"
        and mode == network
        and len(before["networks"]) == 1
        and set(before["networks"].values()) <= {"", network},
        "migration_created_container",
    )
    try:
        async with asyncio.timeout(210):
            time_window = {
                "db_before_utc": await database_clock(baseline["db"]),
                "started_monotonic_ns": time.monotonic_ns(),
            }
            journal.directory.create(
                "migration-time-before.json", canonical(time_window)
            )
            await _capture(DOCKER + ("start", "--attach", identity), timeout=180)
            after = await _container(identity)
            require(
                after["image"] == backend
                and after["running"] is False
                and after["pid"] == 0
                and after["exit_code"] == 0
                and after["oom"] is False,
                "migration_exit",
            )
            from host_alembic_runtime import OutputDirectory

            directory = OutputDirectory(spec.data["source_commit"])
            try:
                result = parse(directory.read("result.json"))
            finally:
                directory.close()
            require(
                result["policy_sha256"] == policy.sha256 and result["failure"] is None,
                "migration_result",
            )
            validate_evidence(result["proof"], policy)
            identity_row = validate_identity(result["identity"], policy)
            await verify_backend_absence(policy, baseline["db"], identity_row)
            time_window.update(
                db_after_utc=await database_clock(baseline["db"]),
                completed_monotonic_ns=time.monotonic_ns(),
            )
            require(
                time_window["db_after_utc"] >= time_window["db_before_utc"],
                "migration_database_clock_regressed",
            )
    except BaseException:
        # Stop only the owned immutable container. Failure retains both identities
        # and the durable MIGRATE intent; no cleanup outcome is inferred here.
        try:
            await _capture(DOCKER + ("stop", "--time", "5", identity), timeout=8)
        except BaseException:
            pass
        raise
    facts = {
        "mode": "host_observed",
        "policy_sha256": policy.sha256,
        "container_id": identity,
        "proof": result["proof"],
        "backend_absent": True,
        "post_head": result["post_head"],
        "time_window": time_window,
    }
    journal.directory.create("migration-observed.json", canonical(facts))
    return facts


async def verify_backend_absence(policy, database_id, identity):
    validate_identity(identity, policy)
    # All interpolated tokens have closed numeric/hex grammars; no caller SQL.
    sql = (
        "SELECT json_build_object('present', EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid="
        + str(identity["pid"])
        + " AND extract(epoch from backend_start)::text='"
        + identity["backend_start"]
        + "' AND application_name='"
        + identity["application_name"]
        + "'), 'lock_present', EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid="
        + str(LOCK_KEY)
        + " AND objsubid=1), 'heads', (SELECT json_agg(version_num ORDER BY version_num) FROM alembic_version))"
    )
    row = parse(
        await _capture(
            DOCKER
            + (
                "exec",
                database_id,
                "psql",
                "-X",
                "-A",
                "-t",
                "-v",
                "ON_ERROR_STOP=1",
                "--host=/var/run/postgresql",
                "--username=root",
                "--dbname=clothing_erp_prod_20261018",
                "-c",
                sql,
            )
        )
    )
    from contracts import AFTER

    exact(row, ("present", "lock_present", "heads"))
    require(
        row["present"] is False
        and row["lock_present"] is False
        and row["heads"] == [AFTER],
        "migration_backend_or_head",
    )
