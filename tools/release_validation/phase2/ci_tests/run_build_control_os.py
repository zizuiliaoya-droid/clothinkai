"""Disposable Linux staging kernel tests; no container daemon or production IO."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from host_build_control import _write_tree
from linux_transport import require_linux


def main():
    require_linux()
    checks = {}
    with tempfile.TemporaryDirectory(prefix="flow78-build-kernel-") as temporary:
        root = Path(temporary)
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            files = {
                "backend/app/a.py": b"synthetic-source",
                "control/Dockerfile": b"synthetic-template",
            }
            _write_tree(parent, "build", files)
            assert all((root / "build" / n).read_bytes() == b for n, b in files.items())
            assert all(
                (root / "build" / n).stat().st_mode & 0o777 == 0o600 for n in files
            )
            checks["nested_exclusive_bytes_modes_fsync"] = True
            try:
                _write_tree(parent, "build", files)
            except FileExistsError:
                checks["existing_context_never_overwritten"] = True
            else:
                raise AssertionError("existing context accepted")
            outside = root / "sentinel"
            outside.mkdir()
            (outside / "keep").write_bytes(b"unchanged")
            (root / "linked").symlink_to(outside, target_is_directory=True)
            try:
                _write_tree(parent, "linked", {"keep": b"bad"})
            except FileExistsError:
                checks["symlink_target_never_followed"] = True
            else:
                raise AssertionError("linked context accepted")
            assert (outside / "keep").read_bytes() == b"unchanged"
        finally:
            os.close(parent)
    print(
        json.dumps(
            {
                "mode": "linux_disposable_build_control_kernel",
                "production_access": False,
                "checks": checks,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
