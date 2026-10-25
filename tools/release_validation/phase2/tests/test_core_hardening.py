from copy import deepcopy
from pathlib import Path
import tempfile
import hashlib
import unittest

from contracts import Rejected
from tests.fakes import baseline
from tests.test_backup_adapter import backup_evidence
import compose_candidate
import backup_adapter
import build_adapter
import preflight
import maintenance
from journal import Journal


class CoreHardeningTests(unittest.TestCase):
    def test_unknown_health_commands_rejected(self):
        base = baseline()["compose"]
        for role in ("prod-api", "prod-beat"):
            candidate = compose_candidate.generate(
                base, "a" * 40, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "dbm.dumb"
            )
            candidate["services"][role]["healthcheck"] = {"test": ["CMD", "unreviewed"]}
            with self.assertRaises(Rejected):
                compose_candidate.validate(candidate, base, "a" * 40)

    def test_backup_metadata_requires_hex_digest_and_closed_fields(self):
        files, receipt = backup_evidence()
        badfiles = deepcopy(files)
        badreceipt = deepcopy(receipt)
        for item in badfiles.values():
            item["sha256"] = "not-a-hash"
        for key in ("local_sha256", "r2_get_sha256", "sidecar_sha256"):
            badreceipt[key] = "not-a-hash"
        with self.assertRaises(Rejected):
            backup_adapter.verify(
                {}, badfiles, badreceipt, 130, "fixture-backup-1", "c" * 64
            )
        next(iter(files.values()))["unreviewed"] = True
        with self.assertRaises(Rejected):
            backup_adapter.verify({}, files, receipt, 130, "fixture-backup-1", "c" * 64)

    def test_frontend_acceptance_does_not_require_python_database_runtime(self):
        receipt = {
            "mode": "synthetic",
            "image_id": "sha256:" + "b" * 64,
            "source_commit": "a" * 40,
            "os": "linux",
            "architecture": "amd64",
            "exit_code": 0,
            "assets_sha256": "c" * 64,
            "expected_assets_sha256": "c" * 64,
            "origin_matches": True,
            "syntax_ok": True,
        }
        self.assertTrue(hasattr(build_adapter, "accept_frontend"))
        self.assertEqual(
            build_adapter.accept_frontend(receipt, "a" * 40), receipt["image_id"]
        )

    def test_malformed_tar_has_controlled_failure(self):
        with self.assertRaises(Exception) as caught:
            preflight.verify_candidate(b"not-gzip", "a" * 40, "b" * 40)
        self.assertIsInstance(caught.exception, Rejected)

    def test_atomic_gate_snapshots_original_metadata(self):
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as tmp:
            root = Path(tmp)
            active = root / "nginx-active.conf"
            active.write_bytes(b"old-fixture")
            before = active.stat()
            maintenance.close_fixture(
                Journal(root),
                hashlib.sha256(b"old-fixture").hexdigest(),
                b"closed-fixture",
            )
            self.assertTrue((root / "old-active.metadata.json").exists())
            self.assertEqual(active.stat().st_mode & 0o777, before.st_mode & 0o777)
