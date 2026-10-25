"""Actual inherited healthcheck overlap diagnostic; a HOLD is not compatibility."""

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
            owned = call(
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
            ).decode()
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
            deadline = time.monotonic() + 25
            observed = None
            while time.monotonic() < deadline:
                try:
                    process_group(pid)
                except Rejected as error:
                    assert str(error) == "producer_uncovered_process"
                    cgroup = Path("/sys/fs/cgroup") / before["cgroup"].lstrip("/")
                    pids = [
                        int(p) for p in (cgroup / "cgroup.procs").read_text().split()
                    ]
                    commands = []
                    try:
                        for member in pids:
                            if member != pid:
                                commands.append(
                                    (Path("/proc") / str(member) / "cmdline")
                                    .read_bytes()
                                    .rstrip(b"\0")
                                    .split(b"\0")
                                )
                    except FileNotFoundError:
                        continue
                    python_probe = [b"python", b"-c", PROBE.encode()]
                    shell_probe = [b"/bin/sh", b"-c", SHELL_PROBE.encode()]
                    # Record whether the actual base-image shell retained a
                    # parent process or exec-replaced itself; no other exec
                    # process can satisfy this narrow diagnostic.
                    if python_probe in commands and (
                        commands == [python_probe]
                        or len(commands) == 2
                        and shell_probe in commands
                    ):
                        observed = {
                            "error": str(error),
                            "process_count": len(pids),
                            "exact_original_probe_argv": True,
                            "shell_parent_observed": shell_probe in commands,
                            "probe_process_argv": [
                                [arg.decode() for arg in command]
                                for command in commands
                            ],
                        }
                        break
                time.sleep(0.05)
            assert observed is not None, "healthcheck overlap not actually observed"
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
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            assert call("wait", owned) == b"137"
            result = {
                "mode": "real_linux_original_healthcheck_overlap_diagnostic",
                "image_id": image,
                "healthcheck": health,
                "overlap": observed,
                "business_heartbeat_stopped": True,
                "separate_read_exec_succeeded": True,
                "sigcont_used": False,
                "original_healthcheck_compatible": False,
                "release_gate": "HOLD_healthcheck_uncovered_process",
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
