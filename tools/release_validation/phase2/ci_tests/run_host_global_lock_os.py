"""Real cross-process flock/claim kernel, confined to a disposable root CI folder."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_sequence
from contracts import Rejected
from linux_transport import PrivateDirectory


def journal(root, identity):
    assert root.parent == Path("/root") and root.name.startswith("flow78-host-lock-ci-")
    host_sequence.ROOT = str(root)
    state = root / identity
    state.mkdir(mode=0o700, exist_ok=True)
    directory = object.__new__(PrivateDirectory)
    directory.root = state
    directory.fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    value = object.__new__(host_sequence.HostJournal)
    value.directory, value.root, value.lock, value.global_lock = (
        directory,
        state,
        None,
        None,
    )
    value.claim = {
        "release_id": "flow78-" + identity * 12,
        "source_commit": identity * 40,
        "input_sha256": identity * 64,
    }
    return value


def child(root, identity, abandon=False):
    value = journal(root, identity)
    try:
        value.acquire()
    except BlockingIOError:
        return 20
    except Rejected:
        return 21
    if abandon:
        value.append("hold", {"reason": "synthetic_process_exit"})
        os._exit(0)
    rows = value.read()
    value.release()
    value.directory.close()
    return 22 if rows and rows[-1]["kind"] == "hold" else 0


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    if len(sys.argv) > 1:
        assert (
            len(sys.argv) == 4
            and sys.argv[1] in ("--child", "--abandon")
            and sys.argv[3] in ("a", "b")
        )
        raise SystemExit(
            child(Path(sys.argv[2]), sys.argv[3], sys.argv[1] == "--abandon")
        )
    checks = {}
    with tempfile.TemporaryDirectory(
        prefix="flow78-host-lock-ci-", dir="/root"
    ) as temporary:
        root = Path(temporary)
        value = journal(root, "a")
        value.acquire()
        raw = (root / "flow78-active.json").read_bytes()

        def run(identity):
            return subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--child",
                    str(root),
                    identity,
                ],
                close_fds=True,
                timeout=15,
                capture_output=True,
            ).returncode

        assert run("a") == 20 and run("b") == 20
        checks["two_candidates_blocked_by_real_host_flock"] = True
        value.append("hold", {"reason": "synthetic_unknown_result"})
        value.release()
        value.directory.close()
        assert run("b") == 21 and (root / "flow78-active.json").read_bytes() == raw
        checks["released_os_lock_does_not_remove_durable_other_candidate_block"] = True
        assert run("a") == 22
        checks["same_candidate_reopens_durable_hold_without_effect"] = True
        assert (root / "flow78-active.json").stat().st_mode & 0o777 == 0o600
    with tempfile.TemporaryDirectory(
        prefix="flow78-host-lock-ci-", dir="/root"
    ) as temporary:
        command = [sys.executable, str(Path(__file__).resolve())]
        assert (
            subprocess.run(
                command + ["--abandon", temporary, "a"], timeout=15
            ).returncode
            == 0
        )
        assert (
            subprocess.run(command + ["--child", temporary, "b"], timeout=15).returncode
            == 21
        )
        assert (
            subprocess.run(command + ["--child", temporary, "a"], timeout=15).returncode
            == 22
        )
        checks["actual_process_exit_retains_claim_and_unresolved_journal"] = True
    print(
        json.dumps(
            {
                "mode": "linux_host_global_lock_disposable",
                "production_access": False,
                "checks": checks,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
