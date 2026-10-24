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
        transfer = json.loads(
            (HERE / "checkpoint-ci-transfer-pins-1024.json").read_bytes()
        )
        names = list(transfer["files"]) + [
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

    def test_exact_101_member_checkpoint(self):
        manifest, _ = verifier.verify()
        self.assertEqual(len(manifest["files"]), 101)
        self.assertTrue(manifest["fixture_only"])

    def test_frozen_member_tamper_rejected(self):
        member = self.root / "phase2/sql_migration_driver.py"
        member.write_bytes(member.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "sql_migration_driver"):
            verifier.verify()

    def test_unlisted_member_rejected(self):
        (self.root / "phase2/extra.py").write_text("pass\n")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            verifier.verify()

    def test_unfrozen_manifest_rejected(self):
        path = self.root / "checkpoint-bundle-manifest-1024.json"
        manifest = json.loads(path.read_bytes())
        manifest["frozen"] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "frozen"):
            verifier.verify()

    def test_changed_successor_transfer_rejected(self):
        path = self.root / "checkpoint-ci-transfer-pins-1024.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "transfer pins drift"):
            verifier.verify()

    def test_changed_historical_transfer_rejected(self):
        path = self.root / "host-ci-transfer-pins-1023.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "historical transfer"):
            verifier.verify()

    def test_fixture_label_cannot_be_removed(self):
        path = self.root / "checkpoint-bundle-manifest-1024.json"
        manifest = json.loads(path.read_bytes())
        manifest["fixture_only"] = False
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "fixture"):
            verifier.verify()


if __name__ == "__main__":
    unittest.main()
