"""Disposable Linux dirfd kernel test; no Docker, network or production paths."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import Rejected
from host_web_control import _atomic_replace
from linux_transport import require_linux


def main():
    require_linux()
    result = {
        "mode": "linux_disposable_web_control_kernel",
        "production_access": False,
        "checks": {},
    }
    with tempfile.TemporaryDirectory(prefix="flow78-web-kernel-") as temporary:
        root = Path(temporary)
        active = root / "nginx-active.conf"
        active.write_bytes(b"synthetic-old")
        active.chmod(0o644)
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _atomic_replace(parent, b"synthetic-old", b"synthetic-new")
            assert (
                active.read_bytes() == b"synthetic-new"
                and active.stat().st_mode & 0o777 == 0o644
            )
            result["checks"]["atomic_replace_readback_fsync"] = True
            try:
                _atomic_replace(parent, b"synthetic-old", b"unexpected")
            except Rejected:
                result["checks"]["stale_original_rejected"] = True
            else:
                raise AssertionError("stale original accepted")
            assert active.read_bytes() == b"synthetic-new"
            pending = root / "nginx-active.conf.pending"
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"keep")
            pending.symlink_to(sentinel)
            try:
                _atomic_replace(parent, b"synthetic-new", b"unexpected")
            except FileExistsError:
                result["checks"]["pending_symlink_rejected"] = True
            else:
                raise AssertionError("pending symlink accepted")
            assert (
                sentinel.read_bytes() == b"keep"
                and active.read_bytes() == b"synthetic-new"
            )
            pending.unlink()
            active.unlink()
            active.symlink_to(sentinel)
            try:
                _atomic_replace(parent, b"keep", b"unexpected")
            except OSError:
                result["checks"]["active_symlink_rejected"] = True
            else:
                raise AssertionError("active symlink accepted")
            active.unlink()
            os.link(sentinel, active)
            try:
                _atomic_replace(parent, b"keep", b"unexpected")
            except Rejected:
                result["checks"]["active_hardlink_rejected"] = True
            else:
                raise AssertionError("active hardlink accepted")
            assert sentinel.read_bytes() == b"keep"
        finally:
            os.close(parent)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
