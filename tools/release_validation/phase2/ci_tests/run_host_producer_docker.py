"""GitHub-only Docker fixture: actual uvicorn SIGSTOP, separate exec, no SIGCONT.

Requires FLOW78_CI_PRODUCER_IMAGE=sha256:<image with Python/uvicorn/boto3> already
present. It neither pulls nor installs. No ERP image/config/network is touched.
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import Rejected
from host_producer_control import process_group, same_group

APP = """import asyncio,os,subprocess,sys
from pathlib import Path
task=None
async def tick():
 while True:
  p=Path('/fixture/heartbeat');q=p.with_name('heartbeat.tmp');q.write_text(str(int(p.read_text())+1));q.replace(p);await asyncio.sleep(.05)
async def app(scope,receive,send):
 global task
 if scope['type']=='lifespan':
  while True:
   message=await receive()
   if message['type']=='lifespan.startup':
    if os.environ.get('FLOW78_FIXTURE_CHILD')=='1':subprocess.Popen([sys.executable,'-c','import time;time.sleep(300)'])
    task=asyncio.create_task(tick());await send({'type':'lifespan.startup.complete'})
   elif message['type']=='lifespan.shutdown':task.cancel();await send({'type':'lifespan.shutdown.complete'});return
 else:
  await send({'type':'http.response.start','status':200,'headers':[]});await send({'type':'http.response.body','body':b'fixture'})
"""


def call(*args):
    return subprocess.check_output(
        ["docker", *args], timeout=30, stderr=subprocess.DEVNULL
    ).strip()


def wait(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("fixture deadline")


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    image = os.environ["FLOW78_CI_PRODUCER_IMAGE"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image)
    assert call("image", "inspect", "--format", "{{.Id}}", image).decode() == image
    checks = {}
    with tempfile.TemporaryDirectory(prefix="flow78-producer-docker-ci-") as temporary:
        root = Path(temporary)
        (root / "app").mkdir()
        (root / "app/__init__.py").write_text("")
        (root / "app/main.py").write_text(APP)
        (root / "heartbeat").write_text("0")
        (root / "synthetic.dump").write_bytes(
            b"synthetic backup bytes; not production\n"
        )
        for child in (False, True):
            name = "flow78-ci-producer-" + uuid.uuid4().hex
            identity = None
            try:
                identity = call(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--restart=no",
                    "--pull=never",
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
                    "--env",
                    "FLOW78_FIXTURE_CHILD=" + str(int(child)),
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
                assert re.fullmatch(r"[0-9a-f]{64}", identity)
                old = int((root / "heartbeat").read_text())
                wait(lambda: int((root / "heartbeat").read_text() or "0") > old + 2)
                pid = int(call("inspect", "--format", "{{.State.Pid}}", identity))
                if child:
                    try:
                        process_group(pid)
                    except Rejected:
                        checks["uncovered_business_child_rejected"] = True
                    else:
                        raise AssertionError("child accepted")
                    continue
                before = process_group(pid)
                fd = os.pidfd_open(pid)
                try:
                    same_group(before, process_group(pid), frozen=False)
                    signal.pidfd_send_signal(fd, signal.SIGSTOP)
                    wait(
                        lambda: all(
                            r["state"] == "T" for r in process_group(pid)["threads"]
                        )
                    )
                    frozen = process_group(pid)
                    same_group(before, frozen, frozen=True)
                    initial = (root / "heartbeat").read_bytes()
                    time.sleep(0.5)
                    assert (root / "heartbeat").read_bytes() == initial
                    checks["actual_uvicorn_all_threads_stopped_no_business_ticks"] = (
                        True
                    )
                    # Same interpreter/import/read primitives as the original
                    # uploader, but deliberately no R2 credentials or requests.
                    proof = call(
                        "exec",
                        identity,
                        "python",
                        "-I",
                        "-B",
                        "-c",
                        "import boto3;from botocore.config import Config;import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/fixture/synthetic.dump').read_bytes()).hexdigest())",
                    )
                    assert (
                        len(proof) == 64
                        and (root / "heartbeat").read_bytes() == initial
                    )
                    same_group(frozen, process_group(pid), frozen=True)
                    checks[
                        "independent_uploader_import_full_read_exec_succeeds_while_business_frozen"
                    ] = True
                    signal.pidfd_send_signal(fd, signal.SIGKILL)
                    assert call("wait", identity) == b"137"
                    assert (root / "heartbeat").read_bytes() == initial
                    checks["frozen_main_terminated_without_resume"] = True
                finally:
                    os.close(fd)
            finally:
                # This cleanup is CI-owned full ID only, never used by host code.
                if identity is not None:
                    call("rm", "-f", identity)
    print(
        json.dumps(
            {
                "mode": "linux_docker_producer_fixture",
                "production_access": False,
                "original_r2_upload_tested": False,
                "checks": checks,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
