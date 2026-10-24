"""Future disposable Linux CI check of the new bounded process kernel only.

Does NOT invoke host collect(), Docker, systemctl, network, or product code.
Not covered by the earlier Linux run 39978060802.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from contracts import Rejected, require  # noqa: E402
from fixed_host_transport import _capture  # noqa: E402
from linux_transport import require_linux  # noqa: E402


def no_children():
    # This process launches only the serial fixed helper below.
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return
    raise AssertionError("owned_child_not_reaped")


async def run():
    require_linux()
    require(os.environ.get("GITHUB_ACTIONS") == "true", "disposable_ci_required")
    base = (sys.executable, "-I", "-B", str(HERE / "ci_tests/host_capture_child.py"))
    require(
        await _capture(base + ("small",)) == b'{"fixture":true}\n', "capture_readback"
    )
    no_children()
    for kind, timeout in (("oversize", 5), ("sleep", 0.2)):
        try:
            await _capture(base + (kind,), timeout=timeout)
        except Rejected:
            pass
        else:
            raise AssertionError("capture_must_fail_closed")
        no_children()
    task = asyncio.create_task(_capture(base + ("sleep",)))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("cancellation_not_propagated")
    no_children()
    return {
        "checks": [
            "bounded_stdout",
            "oversize_reaped",
            "timeout_reaped",
            "cancel_reaped",
        ],
        "execution_scope": "linux_kernel_only",
        "data_kind": "synthetic",
        "deployable": False,
        "live_enabled": False,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), sort_keys=True))
