"""Actual original registry, Redis worker and live GNU/dumb Beat; synthetic isolated data."""

import base64
import hashlib
import importlib
import json
import os
import pickle
import re
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1])]
from beat_health_candidate import _fixed_read, _frozen_start
from host_jobs_control import WORKER_PROBE

SCRIPT = "/validation/phase2/ci_tests/run_celery_docker.py"
PROJECTION = (
    '{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},'
    '"running":{{json .State.Running}},"oom":{{json .State.OOMKilled}},'
    '"pid":{{json .State.Pid}},"restarts":{{json .RestartCount}},'
    '"started":{{json .State.StartedAt}},"ports":{{json .HostConfig.PortBindings}},'
    '"readonly":{{json .HostConfig.ReadonlyRootfs}},"privileged":{{json .HostConfig.Privileged}},'
    '"cap_add":{{json .HostConfig.CapAdd}},"cap_drop":{{json .HostConfig.CapDrop}},'
    '"networks":{{json .NetworkSettings.Networks}},'
    '"fixture":{{json (index .Config.Labels "flow78.fixture")}}}'
)


def process():
    raw = Path("/proc/1/stat").read_text()
    fields = raw[raw.rfind(")") + 2 :].split()
    assert fields[0] not in {"Z", "X"}
    return {
        "pid": 1,
        "state": fields[0],
        "start_ticks": int(fields[19]),
        "argv": Path("/proc/1/cmdline").read_bytes().rstrip(b"\0").decode().split("\0"),
    }


def app():
    from original_api_observer import _config, validate_config

    config = _config()
    validate_config(config)
    _frozen_start().load.__globals__["validate_config"](config)
    os.environ.update(config)
    os.chdir("/app")
    sys.path.insert(0, "/app")
    from app.core.celery_app import celery_app

    celery_app.loader.import_default_modules()
    return celery_app


def inside(mode):
    from run_dbm_docker import make_store, source_schedule

    if mode == "seed":
        from importlib.metadata import version

        from celery.beat import ScheduleEntry

        expected, schedule = source_schedule(
            Path("/product-source/backend/app/core/celery_app.py")
        )
        for name in ("gnu", "dumb"):
            base = make_store(Path("/state") / name, "dbm." + name, schedule)
            # Fresh owned synthetic data only: one harmless cleanup task is due.
            # The real Beat can publish it only to broker DB3 with no consumer.
            entries = {
                key: ScheduleEntry(
                    name=key,
                    last_run_at=datetime.now(timezone.utc)
                    - timedelta(
                        days=2 if key == "cleanup-expired-refresh-tokens-daily" else 0
                    ),
                    total_run_count=0,
                    args=(),
                    kwargs={},
                    **value,
                )
                for key, value in schedule.items()
            }
            with importlib.import_module("dbm." + name).open(str(base), "w") as store:
                store[b"entries"] = pickle.dumps(entries, protocol=4)
            _frozen_start().validate_existing_schedule(base, "dbm." + name, expected)
            # Match the frozen dispatch proof byte-for-byte, including spacing.
            (Path("/state") / name / "production-schedule.json").write_bytes(
                json.dumps(expected, sort_keys=True).encode()
            )
        (Path("/state") / "beat-expected.json").write_text(
            json.dumps(expected), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "expected": expected,
                    "formats": ["dbm.gnu", "dbm.dumb"],
                    "versions": {
                        name: version(name) for name in ("celery", "kombu", "redis")
                    },
                }
            )
        )
        return
    if mode == "store":
        format_name = sys.argv[2]
        pid = int(Path("/tmp/celerybeat.pid").read_text())
        assert pid == 1
        before = process()
        assert json.loads(Path("/tmp/beat-broker.json").read_bytes()) == {
            "broker_db": 3
        }
        parent = Path("/var/lib/celery")
        expected = json.loads(Path("/opt/release/beat-expected.json").read_bytes())
        names = {p.name for p in parent.glob("celerybeat-schedule*")}
        frozen = _frozen_start()
        required = {
            "celerybeat-schedule" + suffix for suffix in frozen.DBM_LAYOUTS[format_name]
        }
        allowed = required | (
            {"celerybeat-schedule.bak"} if format_name == "dbm.dumb" else set()
        )
        assert required <= names <= allowed
        stats = {n: frozen.schedule_stat(parent / n) for n in names}
        values = {n: _fixed_read(parent / n, 64 * 1024**2) for n in names}
        with tempfile.TemporaryDirectory(prefix="flow78-live-store-") as temporary:
            for n, value in values.items():
                target = Path(temporary) / n
                target.write_bytes(value)
                target.chmod(0o600)
            frozen.validate_existing_schedule(
                Path(temporary) / "celerybeat-schedule", format_name, expected
            )
        assert stats == {n: frozen.schedule_stat(parent / n) for n in names}
        assert names == {p.name for p in parent.glob("celerybeat-schedule*")}
        after = process()
        assert before["start_ticks"] == after["start_ticks"]
        print(
            json.dumps(
                {
                    "format": format_name,
                    "task_count": len(expected),
                    "process": after,
                    "files_sha256": {
                        n: hashlib.sha256(v).hexdigest() for n, v in values.items()
                    },
                    "frozen_schedule_validator_passed": True,
                }
            )
        )
        return
    celery = app()
    if mode == "worker":
        _frozen_start().dispatch(
            "worker"
        )  # Exact frozen worker arguments; no guard claim.
    elif mode == "beat":
        # Deliberate CI isolation: original seven cron entries may become due,
        # but broker DB 3 has no consumer. No original business task can run.
        celery.conf.broker_url = "redis://prod-redis:6379/3"
        assert celery.connection_for_write().as_uri() == "redis://prod-redis:6379/3"
        Path("/tmp/beat-broker.json").write_text(
            json.dumps({"broker_db": 3}), encoding="utf-8"
        )
        _frozen_start().dispatch("beat", sys.argv[2])
    elif mode == "ping":
        print(json.dumps(celery.control.inspect(timeout=2).ping()))
    elif mode == "worker-window":
        prefix = (
            (HERE.parents[1] / "original_api_observer.py")
            .read_bytes()
            .split(b'if __name__ == "__main__":')[0]
        )
        sys.argv = ["fixture", "celery@flow78worker"]
        exec(  # noqa: S102 - exact manifest-bound original probe, no external code
            compile(prefix + b"\n" + WORKER_PROBE, "original-worker-probe", "exec"), {}
        )
    elif mode == "builtin-task":
        from importlib.metadata import version

        from celery.app import builtins

        # Verified against the exact requirements lock wheel, without installing
        # it locally. No index => args tuple, serialized by the original JSON backend.
        source_sha = hashlib.sha256(Path(builtins.__file__).read_bytes()).hexdigest()
        assert version("celery") == "5.4.0"
        assert (
            source_sha
            == "8273b2134eccf338316ad4e66f40f4bcacedc75b1065a462fe13bf77e14b354b"
        )
        assert celery.conf.result_serializer == "json"
        # One write. Waiting for this result never republishes the task.
        task = celery.send_task(
            "celery.accumulate", args=["flow78-no-business"], queue="default"
        )
        value = task.get(timeout=20)
        receipt = {
            "task": "celery.accumulate",
            "id": task.id,
            "result": value,
            "result_type": type(value).__name__,
            "result_serializer": celery.conf.result_serializer,
            "builtin_source_sha256": source_sha,
            "original_business_handler_executed": False,
        }
        print(
            json.dumps({"builtin_task_observed": receipt}), file=sys.stderr, flush=True
        )
        assert type(value) is list and value == ["flow78-no-business"]
        print(json.dumps(receipt))
    elif mode == "quarantine":
        import redis

        client = redis.Redis(
            host="prod-redis",
            port=6379,
            db=3,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        expected, _ = source_schedule(
            Path("/product-source/backend/app/core/celery_app.py")
        )
        messages = []
        for queue in ("default", "backup", "crawler", "report"):
            assert client.llen(queue) <= 32
            for raw in client.lrange(queue, 0, 31):
                headers = json.loads(raw)["headers"]
                assert headers["task"] in expected.values()
                messages.append(
                    {"queue": queue, "task": headers["task"], "id": headers["id"]}
                )
        clients = [
            {k: row[k] for k in ("addr", "db", "cmd")} for row in client.client_list()
        ]
        own = redis.Redis(
            host="prod-redis",
            port=6379,
            db=1,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
        assert all(
            own.llen(queue) == 0 for queue in ("default", "backup", "crawler", "report")
        )
        print(
            json.dumps(
                {
                    "broker_db": 3,
                    "messages": messages,
                    "clients": clients,
                    "worker_business_queues_empty": True,
                }
            )
        )
        client.close()
        own.close()
    else:
        raise AssertionError("unknown finite container mode")


def docker(*args, timeout=45, combined=False):
    try:
        return subprocess.check_output(
            ("docker", *args),
            stderr=subprocess.STDOUT if combined else subprocess.PIPE,
            timeout=timeout,
        ).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print((exc.stderr or b"")[-8192:].decode("utf-8", "replace"), file=sys.stderr)
        raise


def main():
    control, redis = (
        os.environ[k] for k in ("FLOW78_CI_CONTROL_IMAGE", "FLOW78_CI_REDIS_IMAGE")
    )
    for image in (control, redis):
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image)
        assert (
            docker("image", "inspect", "--format", "{{.Id}}", image).decode() == image
        )
    validation = HERE.parents[2]
    product = Path(os.environ["FLOW78_CI_PRODUCT_SOURCE"]).resolve()
    assert (
        subprocess.check_output(
            (
                "git",
                "-c",
                "safe.directory=" + str(product),
                "-C",
                str(product),
                "rev-parse",
                "HEAD",
            )
        ).strip()
        == b"55477c44f09bb1a25169bfb44ff6c90bc4f9cedb"
    )
    output = Path(os.environ["FLOW78_CI_OUTPUT"])
    assert (
        output.is_absolute() and output.name == "release-validation" and output.is_dir()
    )
    output = output / "celery"
    output.mkdir(mode=0o755)
    project = "flow78-celery-" + secrets.token_hex(6)
    network, owned = None, []
    with tempfile.TemporaryDirectory(prefix="flow78-celery-", dir="/root") as temporary:
        root = Path(temporary)
        state, config = root / "state", root / "config"
        state.mkdir(mode=0o700)
        config.mkdir(mode=0o700)
        settings = {
            "ENVIRONMENT": "production",
            "LOG_LEVEL": "WARNING",
            "DB_ECHO": "false",
            "DB_POOL_SIZE": "1",
            "DB_MAX_OVERFLOW": "0",
            "CORS_ALLOWED_ORIGINS": "https://47.110.76.9",
            "CREDENTIAL_MASTER_KEY": base64.b64encode(b"c" * 32).decode(),
            "JWT_SECRET": "f" * 64,
            "R2_ENDPOINT_URL": "https://r2.invalid",
            "R2_ACCESS_KEY_ID": "synthetic",
            "R2_SECRET_ACCESS_KEY": "synthetic",
            "R2_BUCKET_PRIVATE": "synthetic",
        }
        for key, role, driver in (
            ("APP", "app", "asyncpg"),
            ("BYPASS", "bypass", "asyncpg"),
            ("SYNC", "bypass", "psycopg2"),
            ("ARCHIVER", "archiver", "asyncpg"),
        ):
            settings["DATABASE_URL_" + key] = (
                f"postgresql+{driver}://clothing_{role}:synthetic@prod-db:5432/clothing_erp_prod_20261018"
            )
        for key, number in (("CACHE", 0), ("CELERY_BROKER", 1), ("CELERY_BACKEND", 2)):
            settings["REDIS_URL_" + key] = f"redis://prod-redis:6379/{number}"
        path = config / "runtime-config.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        path.chmod(0o600)
        common_mounts = (
            "--mount",
            f"type=bind,src={validation},dst=/validation,readonly",
            "--mount",
            f"type=bind,src={product},dst=/product-source,readonly",
            "--mount",
            f"type=bind,src={product / 'backend'},dst=/app,readonly",
            "--mount",
            f"type=bind,src={config},dst=/run/production,readonly",
        )

        def create(role, image, arguments, extra=(), connected=True):
            identity = docker(
                "create",
                "--pull=never",
                "--name",
                project + "-" + role,
                "--label",
                "flow78.fixture=" + project,
                "--hostname",
                "flow78worker" if role == "worker" else role,
                "--network",
                network if connected else "none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges=true",
                "--memory=768m",
                "--pids-limit=256",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "--env",
                "GITHUB_ACTIONS=true",
                *extra,
                image,
                *arguments,
            ).decode()
            assert re.fullmatch(r"[0-9a-f]{64}", identity)
            owned.append(identity)
            docker("start", identity)
            return identity

        def observe(identity, image):
            row = json.loads(docker("inspect", "--format", PROJECTION, identity))
            assert (
                row["id"] == identity
                and row["image"] == image
                and row["fixture"] == project
            )
            assert (
                row["running"]
                and row["oom"] is False
                and row["restarts"] == 0
                and row["pid"] > 0
            )
            assert row["ports"] in ({}, None)
            assert row["readonly"] is True and row["privileged"] is False
            assert row["cap_add"] in (None, []) and row["cap_drop"] == ["ALL"]
            assert (
                len(row["networks"]) == 1
                and next(iter(row["networks"].values()))["NetworkID"] == network
            )
            return row

        def probe(identity, mode, *args):
            return json.loads(
                docker("exec", identity, "python", "-I", "-B", SCRIPT, mode, *args)
            )

        try:
            network = docker("network", "create", "--internal", project).decode()
            assert re.fullmatch(r"[0-9a-f]{64}", network)
            assert (
                docker("network", "inspect", "--format", "{{.Internal}}", network)
                == b"true"
            )
            seed = create(
                "seed",
                control,
                ("-I", "-B", SCRIPT, "seed"),
                (
                    *common_mounts,
                    "--mount",
                    f"type=bind,src={state},dst=/state",
                    "--entrypoint",
                    "python",
                ),
                connected=False,
            )
            assert docker("wait", seed) == b"0"
            seeded = json.loads(docker("logs", seed))
            redis_id = create(
                "redis",
                redis,
                (
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    "/tmp",
                    "--protected-mode",
                    "no",
                ),
                ("--network-alias", "prod-redis", "--entrypoint", "redis-server"),
            )
            for _ in range(30):
                try:
                    if (
                        docker("exec", redis_id, "redis-cli", "ping", timeout=5)
                        == b"PONG"
                    ):
                        break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(0.5)
            else:
                raise AssertionError("redis_not_ready")
            redis_version = docker(
                "exec", redis_id, "redis-server", "--version"
            ).decode()
            worker = create(
                "worker",
                control,
                ("-I", "-B", SCRIPT, "worker"),
                (*common_mounts, "--entrypoint", "python"),
            )
            for _ in range(8):
                answer = probe(worker, "ping")
                if answer is not None:
                    assert answer == {"celery@flow78worker": {"ok": "pong"}}
                    break
                time.sleep(1)
            else:
                raise AssertionError("worker_not_ready")
            (output / "worker-ready.json").write_text(
                json.dumps(
                    {
                        "worker": observe(worker, control),
                        "redis": observe(redis_id, redis),
                    }
                ),
                encoding="utf-8",
            )
            builtin = probe(worker, "builtin-task")
            (output / "builtin-task.json").write_text(
                json.dumps(builtin), encoding="utf-8"
            )
            beats = {}
            for name in ("gnu", "dumb"):
                beats[name] = create(
                    "beat-" + name,
                    control,
                    ("-I", "-B", SCRIPT, "beat", "dbm." + name),
                    (
                        *common_mounts,
                        "--mount",
                        f"type=bind,src={state / name},dst=/var/lib/celery",
                        "--mount",
                        f"type=bind,src={state / 'beat-expected.json'},dst=/opt/release/beat-expected.json,readonly",
                        "--entrypoint",
                        "python",
                    ),
                )
                for _ in range(30):
                    ready = docker(
                        "exec",
                        beats[name],
                        "python",
                        "-c",
                        "from pathlib import Path;print(Path('/tmp/celerybeat.pid').is_file())",
                    )
                    if ready == b"True":
                        break
                    time.sleep(0.5)
                else:
                    raise AssertionError("beat_not_ready")
            windows = []
            for index in range(2):
                if index:
                    time.sleep(30)
                windows.append(
                    {
                        "worker": {
                            "container": observe(worker, control),
                            "registration": probe(worker, "worker-window"),
                        },
                        "beat": {
                            name: {
                                "container": observe(identity, control),
                                "schedule": probe(identity, "store", "dbm." + name),
                            }
                            for name, identity in beats.items()
                        },
                        "redis": observe(redis_id, redis),
                        "monotonic_ns": time.monotonic_ns(),
                    }
                )
            (output / "stable-windows.json").write_text(
                json.dumps(windows), encoding="utf-8"
            )
            assert (
                windows[1]["monotonic_ns"] - windows[0]["monotonic_ns"]
                >= 30_000_000_000
            )
            assert windows[0]["worker"] == windows[1]["worker"]
            assert windows[0]["redis"] == windows[1]["redis"]
            for name in beats:
                first, second = (window["beat"][name] for window in windows)
                assert first["container"] == second["container"]
                assert (
                    first["schedule"]["process"]["start_ticks"]
                    == second["schedule"]["process"]["start_ticks"]
                )
                assert (
                    first["schedule"]["task_count"]
                    == second["schedule"]["task_count"]
                    == 7
                )
            connected = json.loads(
                docker(
                    "network", "inspect", "--format", "{{json .Containers}}", network
                )
            )
            assert set(connected) == {worker, redis_id, *beats.values()}
            quarantined = probe(beats["gnu"], "quarantine")
            cleanup = "app.tasks.cleanup_tasks.cleanup_expired_refresh_tokens"
            assert sum(row["task"] == cleanup for row in quarantined["messages"]) >= 2
            worker_ip = next(
                iter(windows[-1]["worker"]["container"]["networks"].values())
            )["IPAddress"]
            assert all(
                not (row["addr"].rsplit(":", 1)[0] == worker_ip and int(row["db"]) == 3)
                for row in quarantined["clients"]
            )
            for name in beats:
                beat_ip = next(
                    iter(windows[-1]["beat"][name]["container"]["networks"].values())
                )["IPAddress"]
                assert any(
                    row["addr"].rsplit(":", 1)[0] == beat_ip
                    and int(row["db"]) == 3
                    and row["cmd"] != "client|list"
                    for row in quarantined["clients"]
                )
            print(
                json.dumps(
                    {
                        "mode": "actual_original_celery_registry_and_isolated_redis",
                        "source_commit": "55477c44f09bb1a25169bfb44ff6c90bc4f9cedb",
                        "network_id": network,
                        "dependency_image": control,
                        "redis_image": redis,
                        "redis_version": redis_version,
                        "seeded": seeded,
                        "builtin_task": builtin,
                        "quarantined_due_messages": quarantined,
                        "windows": windows,
                        "worker_broker_db": 1,
                        "beat_broker_db": 3,
                        "beat_broker_has_consumer": False,
                        "original_start_guard_executed": False,
                        "production_same_broker_consumption_verified": False,
                        "production_schedule_file_verified": False,
                        "production_29_actions_verified": False,
                        "release_gate": "HOLD_remaining_release_validation",
                    },
                    sort_keys=True,
                )
            )
        except BaseException:
            for identity in owned:
                try:
                    print(
                        json.dumps(
                            {
                                "owned_container": identity,
                                "log_tail": docker(
                                    "logs", "--tail", "35", identity, combined=True
                                )[-16384:].decode("utf-8", "replace"),
                            }
                        ),
                        file=sys.stderr,
                    )
                except (OSError, subprocess.SubprocessError, ValueError) as error:
                    print(
                        json.dumps(
                            {
                                "owned_container": identity,
                                "diagnostic_error": type(error).__name__,
                            }
                        ),
                        file=sys.stderr,
                    )
            raise
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                found = (
                    docker(
                        "ps",
                        "--all",
                        "--quiet",
                        "--no-trunc",
                        "--filter",
                        "label=flow78.fixture=" + project,
                    )
                    .decode()
                    .split()
                )
                for identity in found:
                    row = json.loads(
                        docker("inspect", "--format", PROJECTION, identity)
                    )
                    assert (
                        re.fullmatch(r"[0-9a-f]{64}", identity)
                        and row["id"] == identity
                        and row["fixture"] == project
                    )
                    docker("rm", "--force", identity)
                if network:
                    docker("network", "rm", network)
            except Exception:
                if not failed:
                    raise
                print(
                    "synthetic owned cleanup failed after original failure",
                    file=sys.stderr,
                )
            finally:
                try:
                    captured = {}
                    for format_name in ("gnu", "dumb"):
                        source = state / format_name
                        if not source.is_dir():
                            continue
                        destination = output / format_name
                        destination.mkdir(mode=0o755)
                        names = {
                            "production-schedule.json",
                            "celerybeat-schedule",
                            "celerybeat-schedule.dat",
                            "celerybeat-schedule.dir",
                            "celerybeat-schedule.bak",
                        }
                        for path in source.iterdir():
                            assert path.name in names
                            raw = _fixed_read(path, 64 * 1024**2)
                            (destination / path.name).write_bytes(raw)
                            captured[format_name + "/" + path.name] = {
                                "bytes": len(raw),
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                    (output / "synthetic-store-pins.json").write_text(
                        json.dumps(
                            {
                                "synthetic_only": True,
                                "production_format_verified": False,
                                "files": captured,
                            },
                            sort_keys=True,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    if not failed:
                        raise
                    print(
                        "synthetic store evidence capture failed after original failure",
                        file=sys.stderr,
                    )


if __name__ == "__main__":
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    if len(sys.argv) > 1:
        inside(sys.argv[1])
    else:
        main()
