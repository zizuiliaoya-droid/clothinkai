"""Actual kernel checks independent of the blocked product package. No DB/Docker."""

import asyncio
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "ci_tests"))

from run_linux import filesystem_checks  # noqa: E402

from linux_transport import OwnedChild, ci_root, require_linux  # noqa: E402
from sql_policy import need  # noqa: E402


async def run():
    require_linux()
    nonce = secrets.token_hex(16)
    root = ci_root(nonce)
    root.mkdir(mode=0o700)
    filesystem_checks(nonce)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        str(HERE / "ci_tests/os_child.py"),
        nonce,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    child = OwnedChild(process, nonce)
    try:
        deadline = time.monotonic() + 10
        while not (root / "os-child-ready.json").exists():
            need(
                time.monotonic() < deadline and child.returncode is None,
                "os_child_not_ready",
            )
            await asyncio.sleep(0.02)
        child.terminate()
        try:
            await asyncio.wait_for(child.wait(), 0.2)
        except TimeoutError:
            pass
        else:
            raise AssertionError("test child must ignore SIGTERM")
        child.kill()
        need(
            await asyncio.wait_for(child.wait(), 5) == -signal.SIGKILL,
            "pidfd_kill_failed",
        )
        need(child.returncode == -signal.SIGKILL, "precise_child_not_reaped")
        return {
            "mode": "linux_ci",
            "data_kind": "synthetic",
            "linux_executed": True,
            "checks": [
                "flock_exclusion",
                "file_and_directory_fsync",
                "nofollow",
                "hardlink_rejection",
                "pidfd_sigterm",
                "pidfd_sigkill",
                "precise_child_reaped",
            ],
            "product_package_verified": False,
            "pg_verified": False,
            "deployable": False,
            "live_enabled": False,
        }
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        os.close(child.pidfd)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), sort_keys=True))
