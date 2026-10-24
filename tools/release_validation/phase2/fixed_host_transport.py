"""Finite host operations. Only guarded read-only collection has an entry point.

Write command compilation is reviewable; it does not confer production authority.
There is no argv, URL, SQL, environment override or live boolean in the public API.
"""

import asyncio
import hashlib
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

from contracts import STEPS, TARGET, Rejected, require

ROOT = "/srv/clothinkai/production-20261018"
ROLES = ("api", "web", "worker", "beat", "db", "redis")
TIMERS = ("backup", "renew")
API_OBSERVER_SHA256 = "cd1a2a03513428e0eb04624f7a2bd807ce89120ec20e330e7ce259a735b5964b"
DOCKER = (
    "/usr/bin/docker",
    "--host",
    "unix:///var/run/docker.sock",
    "--config",
    ROOT + "/docker-client",
)
# Docker performs this projection before stdout exists. Config.Env and command
# arguments (which may contain credentials) never leave the daemon.
INSPECT = (
    '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
    '"running":{{json .State.Running}},"paused":{{json .State.Paused}},'
    '"restarting":{{json .State.Restarting}},"oom":{{json .State.OOMKilled}},'
    '"exit_code":{{json .State.ExitCode}},"restarts":{{json .RestartCount}},'
    '"restart_policy":{{json .HostConfig.RestartPolicy.Name}},"pid":{{json .State.Pid}},'
    '"networks":{ {{$first := true}}{{range $k,$v := .NetworkSettings.Networks}}'
    "{{if not $first}},{{end}}{{json $k}}:{{json $v.NetworkID}}{{$first = false}}{{end}} }}"
)


def command(operation, subject):
    require(type(operation) is str and type(subject) is str, "operation_type")
    if operation == "inspect" and subject in ROLES:
        return DOCKER + (
            "inspect",
            "--type",
            "container",
            "--format",
            INSPECT,
            "clothinkai-prod-" + subject + "-20261018",
        )
    if operation == "stop" and subject in ("api", "worker", "beat"):
        return DOCKER + (
            "stop",
            "--time",
            "120",
            "clothinkai-prod-" + subject + "-20261018",
        )
    if operation == "disable_restart" and subject in ("api", "web", "worker", "beat"):
        return DOCKER + (
            "update",
            "--restart=no",
            "clothinkai-prod-" + subject + "-20261018",
        )
    if (
        operation in ("timer_show", "service_show", "timer_stop", "timer_disable")
        and subject in TIMERS
    ):
        suffix = ".service" if operation == "service_show" else ".timer"
        unit = "clothinkai-production-" + subject + suffix
        if operation.endswith("show"):
            props = "Id,LoadState,ActiveState,SubState,UnitFileState,Job"
            if operation == "service_show":
                props += ",MainPID,ControlPID,ControlGroup"
            return (
                "/usr/bin/systemctl",
                "show",
                "--no-pager",
                "--property=" + props,
                unit,
            )
        return (
            "/usr/bin/systemctl",
            "stop" if operation == "timer_stop" else "disable",
            unit,
        )
    if operation in ("nginx_test", "nginx_reload") and subject == "web":
        tail = ("-t",) if operation == "nginx_test" else ("-s", "reload")
        return DOCKER + (
            "exec",
            "clothinkai-prod-web-20261018",
            "nginx",
            *tail,
            "-c",
            "/opt/production/nginx-active.conf",
        )
    if operation == "backup" and subject == "api":
        return ("/usr/bin/python3", ROOT + "/build/control/backup.py")
    if operation == "api_observe" and subject == "api":
        return DOCKER + (
            "exec",
            "-i",
            "clothinkai-prod-api-20261018",
            "python",
            "-I",
            "-B",
            "-",
        )
    raise Rejected("fixed_operation")


def validate_readonly(operation, subject):
    command(operation, subject)
    require(
        operation in {"inspect", "timer_show", "service_show", "api_observe"},
        "production_authority_unbound",
    )


def _private_fixed_file(path, limit, uid=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == uid
            and info.st_nlink == 1
            and not info.st_mode & 0o022
            and 0 < info.st_size <= limit,
            "fixed_file_metadata",
        )
        data = os.read(fd, limit + 1)
        require(len(data) == info.st_size, "fixed_file_changed")
        return data
    finally:
        os.close(fd)


async def _capture(argv, stdin=None, timeout=15):
    """Private bounded subprocess kernel; callers compile the closed operations.

    A killed docker-exec client is NOT proof its container process has exited.
    Errors therefore never return an observation or authorize releasing a lock.
    """
    from linux_transport import require_linux

    require_linux()
    # Popen has no asyncio child watcher: until wait() is started below, an exited
    # child remains ours (a zombie). Its PID cannot be recycled before pidfd_open.
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": "/root"},
        cwd="/",
        start_new_session=True,
    )
    pidfd = None
    tasks = []
    pipe_transport = None
    reader = asyncio.StreamReader(limit=65537)

    async def read():
        output = bytearray()
        while chunk := await reader.read(4096):
            output.extend(chunk)
            require(len(output) <= 65536, "host_projection_size")
        return bytes(output)

    async def write():
        if stdin is not None:

            def send():
                try:
                    proc.stdin.write(stdin)
                    proc.stdin.flush()
                finally:
                    proc.stdin.close()

            await asyncio.to_thread(send)

    try:
        pidfd = os.pidfd_open(proc.pid)
        pipe_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), proc.stdout
        )
        tasks = [
            asyncio.create_task(read()),
            asyncio.create_task(write()),
            asyncio.create_task(asyncio.to_thread(proc.wait)),
        ]
        observed, _, status = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        require(status == 0, "host_operation_failed")
        return observed
    except BaseException as error:
        if proc.returncode is None and pidfd is not None:
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif pidfd is None and not tasks:
            # No waiter has started, so Popen still owns this PID; its own poll
            # prevents a signal after it reaps an already-exited child.
            proc.kill()
        try:
            await asyncio.wait_for(asyncio.shield(asyncio.to_thread(proc.wait)), 5)
        except BaseException:
            pass
        if isinstance(error, asyncio.CancelledError):
            raise
        raise Rejected("host_observation_unknown") from None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if pidfd is not None:
            os.close(pidfd)
        if pipe_transport is not None:
            pipe_transport.close()
        else:
            proc.stdout.close()


async def collect(operation, subject):
    """Future approved host read-only entry; not called by Windows or Linux CI.

    Verify the fixed host and data mount before Docker/systemd.
    """
    from host_collectors import (
        container_projection,
        queue_projection,
        service_projection,
        timer_projection,
    )
    from linux_transport import require_linux
    from sql_policy import parse

    validate_readonly(operation, subject)
    require_linux()
    require(os.geteuid() == 0, "host_root_required")
    require(
        _private_fixed_file("/var/lib/cloud/data/instance-id", 128).decode().strip()
        == TARGET["instance"],
        "host_instance",
    )
    mount = await _capture(
        ("/usr/bin/findmnt", "--json", "--target", ROOT, "--output", "UUID")
    )
    require(
        parse(mount) == {"filesystems": [{"uuid": TARGET["disk_uuid"]}]}, "host_disk"
    )
    started = time.monotonic_ns()
    payload = None
    if operation == "api_observe":
        payload = _private_fixed_file(
            Path(__file__).with_name("original_api_observer.py"), 32768
        )
        require(
            hashlib.sha256(payload).hexdigest() == API_OBSERVER_SHA256,
            "api_observer_hash",
        )
    raw = await _capture(command(operation, subject), payload, 30 if payload else 15)
    if operation == "inspect":
        facts = container_projection(raw, subject)
    elif operation == "timer_show":
        facts = timer_projection(raw, subject)
    elif operation == "service_show":
        facts = service_projection(raw, subject)
    else:
        facts = queue_projection(parse(raw))
    return {
        "execution_scope": "linux_host_readonly",
        "operation": operation,
        "subject": subject,
        "started_tick": started,
        "finished_tick": time.monotonic_ns(),
        "facts": facts,
        "deployable": False,
        "live_enabled": False,
    }


def step_operations():
    """All 29 original actions have explicit dependencies; no pending is success."""
    bindings = {
        "PREPARE": ((), "final_archive_and_target_private_journal"),
        "BUILD_BACKEND": ((), "frozen_wheels_base_images_and_build_receipt"),
        "BUILD_FRONTEND": ((), "frozen_assets_and_build_receipt"),
        "PREFLIGHT": (
            tuple(("inspect", r) for r in ROLES),
            "identity_roles_network_mounts_tls_and_baseline",
        ),
        "FREEZE_TIMERS": (
            tuple(
                (op, t)
                for t in TIMERS
                for op in ("timer_stop", "timer_disable", "timer_show", "service_show")
            ),
            "original_timer_state_unit_hash_cgroup_children_and_manual_freeze",
        ),
        "DISABLE_RESTART": (
            tuple(("disable_restart", r) for r in ROLES[:4]),
            "original_restart_policy_and_readback",
        ),
        "CLOSE_GATE": (
            (("nginx_test", "web"), ("nginx_reload", "web")),
            "atomic_config_switch_all_probes_and_old_request_drain",
        ),
        "STOP_BEAT": (
            (("stop", "beat"), ("inspect", "beat")),
            "graceful_exit_unique_process_and_schedule_snapshot",
        ),
        "FREEZE_PRODUCERS": ((), "callbacks_manual_external_producers_frozen"),
        "DRAIN": (
            (("api_observe", "api"),),
            "two_windows_celery_active_reserved_scheduled_retry_and_http_sessions",
        ),
        "STOP_WORKER": (
            (("stop", "worker"), ("inspect", "worker")),
            "graceful_exit_children_and_business_sessions",
        ),
        "FREEZE_VERIFY": (
            (("api_observe", "api"),),
            "all_producer_and_consumer_absence",
        ),
        "BACKUP": (
            (("backup", "api"),),
            "fresh_pair_original_api_window_local_and_r2_full_hash",
        ),
        "RESTORE": ((), "isolated_drill_roles_acl_rls_schema_and_data_comparison"),
        "STOP_API": (
            (("stop", "api"), ("inspect", "api")),
            "api_process_and_exact_backend_absence",
        ),
        "MIGRATE": ((), "production_sql_spec_owned_container_and_lock_not_ci_spec"),
        "VERIFY_SCHEMA": (
            (),
            "original_067_stable_sets_acl_and_new_business_write_exclusion",
        ),
        "REPLACE_API": ((), "frozen_compose_api_only_and_original_start_permit"),
        "REPLACE_WEB": ((), "frozen_compose_web_only_and_preserved_old_web_image"),
        "ACCEPT_INTERNAL": ((), "ttl_roles_k7_k8_strict_ready_tls_gate_closed"),
        "START_WORKER": ((), "original_worker_start_permit_and_frozen_compose"),
        "VERIFY_WORKER": ((), "exact_worker_queues_no_unknown_consumers"),
        "START_BEAT": ((), "original_beat_start_permit_and_exact_dbm"),
        "VERIFY_JOBS": ((), "original_seven_jobs_unique_beat_process_and_dbm"),
        "OPEN_GATE": (
            (("nginx_test", "web"), ("nginx_reload", "web")),
            "atomic_config_restore_and_real_external_open_observation",
        ),
        "VERIFY_OPEN": ((), "external_tls_http_ttl_and_business_acceptance"),
        "RESTORE_RESTART": ((), "exact_original_restart_policy_only_after_acceptance"),
        "RESTORE_TIMERS": (
            (),
            "exact_original_enabled_active_masked_state_and_idle_children",
        ),
        "VERIFY_COMPLETE": (
            tuple(("inspect", r) for r in ROLES),
            "complete_signed_action_receipts_and_post_open_observations",
        ),
    }
    require(tuple(bindings) == STEPS, "host_action_coverage")
    return {
        step: {"commands": operations, "requires": evidence, "live_enabled": False}
        for step, (operations, evidence) in bindings.items()
    }
