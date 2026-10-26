"""Root/Linux temporary-filesystem ACME directory test; no production access."""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import host_gate_control as control
from linux_transport import require_linux


def main():
    require_linux()
    assert os.geteuid() == 0, "root_owned_fixture_required"
    spec = SimpleNamespace(data={"source_commit": "a" * 40}, sha256="b" * 64)
    with tempfile.TemporaryDirectory(prefix="flow78-acme-paths-") as temporary:
        root = Path(temporary)

        def fixture_directory(path):
            assert path == control.ROOT + "/acme"
            return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        with patch.object(control, "_directory", side_effect=fixture_directory):
            url, payload = control.acme_file(spec)
            proof = root / url.removeprefix("/")
            assert proof.read_bytes() == payload
            for directory in (root / ".well-known", proof.parent):
                info = directory.lstat()
                assert stat.S_ISDIR(info.st_mode)
                assert info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o755
            before = proof.stat()
            try:
                control.acme_file(spec)
            except FileExistsError:
                pass
            else:
                raise AssertionError("existing_acme_proof_overwritten")
            after = proof.stat()
            assert (before.st_ino, before.st_mtime_ns) == (
                after.st_ino,
                after.st_mtime_ns,
            )
            assert proof.read_bytes() == payload
    print(
        json.dumps(
            {
                "mode": "linux_temporary_acme_directories",
                "production_access": False,
                "missing_directories_created": True,
                "root_owned_0755": True,
                "existing_proof_not_overwritten": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
