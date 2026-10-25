import hashlib
import io
import json
import tarfile
import unittest

from contracts import Rejected
from preflight import verify_candidate


def archive_fixture(name="backend/app/main.py", symlink=False, tamper=False):
    payload = b"# synthetic source\n"
    manifest = {
        "deployable": False,
        "pins": {"source_commit": "a" * 40, "source_tree": "b" * 40},
        "files": {
            name: {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        },
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as output:
        for path, raw in [
            ("release-manifest.json", json.dumps(manifest).encode()),
            (name, payload + (b"!" if tamper else b"")),
        ]:
            item = tarfile.TarInfo(path)
            item.size = len(raw)
            if symlink and path == name:
                item.type = tarfile.SYMTYPE
                item.linkname = "../../outside"
            output.addfile(item, io.BytesIO(raw))
    return buffer.getvalue()


class PrepareTests(unittest.TestCase):
    def test_path_links_and_hash_corruption_rejected_without_extracting(self):
        for raw in (
            archive_fixture("../outside"),
            archive_fixture(symlink=True),
            archive_fixture(tamper=True),
        ):
            with self.assertRaises(Rejected):
                verify_candidate(raw, "a" * 40, "b" * 40)

    def test_verified_public_members_are_returned_without_writing_files(self):
        result = verify_candidate(archive_fixture(), "a" * 40, "b" * 40)
        self.assertEqual(result["members"], ("backend/app/main.py",))
        self.assertFalse(result["deployable"])
