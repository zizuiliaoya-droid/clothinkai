"""Local regression checks for the successor validation-only snapshot."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "verify_bundle", HERE / "verify_bundle.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class HostBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        transfer = json.loads((HERE / "chain-bundle-manifest-1024.json").read_bytes())
        names = list(transfer["files"]) + [
            "chain-ci-transfer-pins-1024.json",
            "chain-bundle-manifest-1024.json",
            "original-public-pins-1024.json",
            "fixture-input-pins-1024.json",
            "checkpoint-ci-transfer-pins-1024.json",
            "checkpoint-bundle-manifest-1024.json",
            "bundle-manifest.json",
            "host-ci-transfer-pins-1023.json",
        ]
        for name in names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((HERE / name).read_bytes())
        context = patch.object(verifier, "HERE", self.root)
        context.start()
        self.addCleanup(context.stop)

    def test_exact_154_capture_plus_finite_ci_additions(self):
        manifest, _ = verifier.verify()
        self.assertEqual(
            len(manifest["files"]), 154 + len(manifest["ci_additions"]) + 3 + 2
        )
        self.assertTrue(manifest["fixture_only"])

    def test_frozen_member_tamper_rejected(self):
        member = self.root / "phase2/sql_migration_driver.py"
        member.write_bytes(member.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "sql_migration_driver"):
            verifier.verify()

    def test_original_public_dependency_tamper_rejected(self):
        member = self.root / "production-infra-1018/backup.py"
        member.write_bytes(member.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "backup.py"):
            verifier.verify()

    def test_changed_original_dependency_manifest_rejected(self):
        member = self.root / "original-public-pins-1024.json"
        member.write_bytes(member.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "dependency pins drift"):
            verifier.verify()

    def test_changed_fixture_input_pins_rejected(self):
        member = self.root / "fixture-input-pins-1024.json"
        member.write_bytes(member.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "fixture input pins drift"):
            verifier.verify()

    def test_changed_runtime_lock_rejected(self):
        member = self.root / "phase2/ci_tests/requirements-linux.lock"
        member.write_bytes(
            member.read_bytes().replace(b"botocore==1.35.99", b"botocore==1.35.27")
        )
        with self.assertRaisesRegex(ValueError, "requirements-linux.lock"):
            verifier.verify()

    def test_nonoriginal_healthcheck_rejected(self):
        member = self.root / "phase2/ci_tests/producer-fixture.Dockerfile"
        member.write_bytes(member.read_bytes().replace(b"timeout=3", b"timeout=4"))
        with self.assertRaisesRegex(ValueError, "original CMD-SHELL"):
            verifier.verify()

    def test_unlisted_member_rejected(self):
        (self.root / "phase2/extra.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            verifier.verify()

    def test_unfrozen_manifest_rejected(self):
        path = self.root / "chain-bundle-manifest-1024.json"
        manifest = json.loads(path.read_bytes())
        manifest["frozen"] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "frozen"):
            verifier.verify()

    def test_changed_successor_transfer_rejected(self):
        path = self.root / "chain-ci-transfer-pins-1024.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "transfer pins drift"):
            verifier.verify()

    def test_changed_historical_transfer_rejected(self):
        path = self.root / "checkpoint-ci-transfer-pins-1024.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "historical transfer"):
            verifier.verify()

    def test_fixture_label_cannot_be_removed(self):
        path = self.root / "chain-bundle-manifest-1024.json"
        manifest = json.loads(path.read_bytes())
        manifest["fixture_only"] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "fixture"):
            verifier.verify()


if __name__ == "__main__":
    unittest.main()
