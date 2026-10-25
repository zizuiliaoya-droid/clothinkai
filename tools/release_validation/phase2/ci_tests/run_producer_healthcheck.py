"""Actual original healthcheck pair, frozen business threads and unknown-exec rejection."""

import json
import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import Rejected
from host_producer_control import _health_identity, same_group
from run_host_producer_docker import APP, call, process_group, wait

PROBE = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3).close()"
SHELL_PROBE = 'python -c "' + PROBE + '"'
HEALTH = {
    "Test": ["CMD-SHELL", SHELL_PROBE],
    "Interval": 10_000_000_000,
    "Timeout": 4_000_000_000,
    "StartPeriod": 30_000_000_000,
    "Retries": 3,
}


def process_diagnostic(before):
    cgroup = Path("/sys/fs/cgroup") / before["cgroup"].lstrip("/")
    members = (cgroup / "cgroup.procs").read_text().split()
    rows = []
    for member in members[:8]:
        try:
            rows.append(_health_identity(int(member)))
        except (FileNotFoundError, ProcessLookupError, Rejected):
            rows.append({"pid": int(member), "sample": "exited_or_changed"})
    return {"processes": rows, "truncated": len(members) > 8}


def observe_complete_pair(pid, before, health, holds):
    """Bounded read-only fixture observation; never relax the production policy."""
    deadline = time.monotonic() + 25

    def hold(reason):
        holds["count"] += 1
        if len(holds["samples"]) < 32:
            holds["samples"].append(
                {
                    "release_gate": "HOLD_" + reason,
                    "read_only": True,
                    "observation": process_diagnostic(before),
                }
            )

    while time.monotonic() < deadline:
        try:
            process_group(pid)
        except Rejected as strict_error:
            assert str(strict_error) == "producer_uncovered_process"
            try:
                compatible = process_group(pid, healthcheck=health)
                same_group(before, compatible, frozen=True)
                pair = compatible["healthcheck_processes"]
                if len(pair) != 2:
                    hold("ci_health_pair_exited_before_observation")
                    time.sleep(0.05)
                    continue
                time.sleep(0.1)
                again = process_group(pid, healthcheck=health)
                same_group(before, again, frozen=True)
                if [
                    (r["pid"], r["start_ticks"]) for r in again["healthcheck_processes"]
                ] != [(r["pid"], r["start_ticks"]) for r in pair]:
                    hold("ci_health_pair_exited_or_changed_before_confirmation")
                    time.sleep(0.05)
                    continue
            except Rejected as rejected:
                if str(rejected) not in {
                    "producer_healthcheck_incomplete_pair",
                    "producer_healthcheck_sample_changed",
                }:
                    raise
                hold(str(rejected))
                time.sleep(0.05)
                continue
            return {
                "strict_policy_error": str(strict_error),
                "process_count": 3,
                "validated_probe_processes": pair,
                "stable_pair_reobserved": True,
            }
        # A sample with only the main process is never overlap success.
        time.sleep(0.05)
    raise Rejected("ci_health_complete_pair_not_observed")


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    image = os.environ["FLOW78_CI_PRODUCER_IMAGE"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image)
    health = json.loads(
        call("image", "inspect", "--format", "{{json .Config.Healthcheck}}", image)
    )
    assert health == HEALTH
    with tempfile.TemporaryDirectory(prefix="flow78-health-overlap-ci-") as temporary:
        root = Path(temporary)
        (root / "app").mkdir()
        (root / "app/__init__.py").write_text("")
        (root / "app/main.py").write_text(APP)
        (root / "heartbeat").write_text("0")
        (root / "synthetic.dump").write_bytes(b"synthetic full read; not a PG dump")
        owned = None
        fd = None
        try:
            run_args = (
                "run",
                "-d",
                "--pull=never",
                "--restart=no",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges=true",
                "--memory=256m",
                "--cpus=1",
                "--pids-limit=64",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--mount",
                "type=bind,src=" + str(root) + ",dst=/fixture",
                "--workdir",
                "/fixture",
                "--entrypoint",
                "python",
                image,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
                "--proxy-headers",
                "--forwarded-allow-ips",
                "*",
                "--no-access-log",
            )
            owned = call(*run_args).decode()
            assert re.fullmatch(r"[0-9a-f]{64}", owned)
            wait(
                lambda: (
                    call("inspect", "--format", "{{.State.Health.Status}}", owned)
                    == b"healthy"
                ),
                seconds=45,
            )
            pid = int(call("inspect", "--format", "{{.State.Pid}}", owned))
            before = process_group(pid)
            fd = os.pidfd_open(pid)
            signal.pidfd_send_signal(fd, signal.SIGSTOP)
            wait(lambda: all(r["state"] == "T" for r in process_group(pid)["threads"]))
            heartbeat = (root / "heartbeat").read_bytes()
            assert (
                json.loads(
                    call("inspect", "--format", "{{json .Config.Healthcheck}}", owned)
                )
                == health
            )
            holds = {"count": 0, "samples": []}
            try:
                observed = observe_complete_pair(pid, before, health, holds)
            except Rejected as rejected:
                print(
                    json.dumps(
                        {
                            "release_gate": "HOLD_" + str(rejected),
                            "original_healthcheck_compatible": False,
                            "read_only_sampling_holds": holds,
                            "synthetic_observed_processes": process_diagnostic(before),
                        }
                    ),
                    flush=True,
                )
                raise
            proof = call(
                "exec",
                owned,
                "python",
                "-I",
                "-B",
                "-c",
                "import boto3;import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/fixture/synthetic.dump').read_bytes()).hexdigest())",
            )
            assert len(proof) == 64 and (root / "heartbeat").read_bytes() == heartbeat
            unknown_code = "import time;time.sleep(300)"
            call("exec", "-d", owned, "python", "-c", unknown_code)
            cgroup = Path("/sys/fs/cgroup") / before["cgroup"].lstrip("/")
            unknown_argv = [b"python", b"-c", unknown_code.encode()]

            def unknown_present():
                for member in (cgroup / "cgroup.procs").read_text().split():
                    try:
                        args = (
                            (Path("/proc") / member / "cmdline")
                            .read_bytes()
                            .rstrip(b"\0")
                            .split(b"\0")
                        )
                        if args == unknown_argv:
                            return True
                    except FileNotFoundError:
                        pass
                return False

            wait(unknown_present)
            try:
                process_group(pid, healthcheck=health)
            except Rejected as error:
                unknown_rejection = str(error)
            else:
                raise AssertionError(
                    "unknown exec incorrectly accepted by health policy"
                )
            assert (root / "heartbeat").read_bytes() == heartbeat
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            assert call("wait", owned) == b"137"
            stopped_container = owned
            call("rm", "-f", owned)
            owned = None
            os.close(fd)
            fd = None
            old_heartbeat = int((root / "heartbeat").read_text())
            owned = call(
                *(run_args[:2] + ("--env", "FLOW78_FIXTURE_CHILD=1") + run_args[2:])
            ).decode()
            assert re.fullmatch(r"[0-9a-f]{64}", owned)
            wait(
                lambda: int((root / "heartbeat").read_text() or "0") > old_heartbeat + 2
            )
            child_parent = int(call("inspect", "--format", "{{.State.Pid}}", owned))
            assert (
                json.loads(
                    call("inspect", "--format", "{{json .Config.Healthcheck}}", owned)
                )
                == health
            )
            children = (
                (
                    Path("/proc")
                    / str(child_parent)
                    / "task"
                    / str(child_parent)
                    / "children"
                )
                .read_text()
                .split()
            )
            assert len(children) == 1
            actual_child = Path("/proc") / children[0]
            assert actual_child.joinpath("cmdline").read_bytes().rstrip(b"\0").split(
                b"\0"
            )[1:] == [b"-c", unknown_code.encode()]
            assert (
                int(
                    actual_child.joinpath("stat")
                    .read_text()
                    .rsplit(")", 1)[1]
                    .split()[1]
                )
                == child_parent
            )
            try:
                process_group(child_parent, healthcheck=health)
            except Rejected as error:
                business_child_rejection = str(error)
            else:
                raise AssertionError(
                    "business child incorrectly accepted by health policy"
                )
            result = {
                "mode": "real_linux_original_healthcheck_pair_compatibility",
                "image_id": image,
                "healthcheck": health,
                "overlap": observed,
                "read_only_sampling_holds": holds,
                "business_heartbeat_stopped": True,
                "separate_read_exec_succeeded": True,
                "sigcont_used": False,
                "original_healthcheck_compatible": True,
                "unknown_exec_rejected": unknown_rejection,
                "unknown_business_child_rejected": business_child_rejection,
                "stopped_container_id": stopped_container,
                "business_child_container_id": owned,
                "release_gate": "HOLD_remaining_release_validation",
                "production_access": False,
                "production_29_actions_verified": False,
            }
            print(json.dumps(result, sort_keys=True))
        finally:
            if fd is not None:
                os.close(fd)
            if owned is not None:
                call("rm", "-f", owned)


if __name__ == "__main__":
    main()
