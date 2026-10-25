from copy import deepcopy
import unittest

from contracts import Rejected
import preflight
import build_adapter
from tests.fakes import baseline, spec_for


class PreflightTests(unittest.TestCase):
    def test_db_mount_network_identity_drift_rejected(self):
        base = baseline()
        spec = spec_for(base)
        for field in ("mounts", "networks", "id"):
            observed = deepcopy(base)
            observed["containers"]["prod-db"][field] = "changed"
            with self.assertRaises(Rejected):
                preflight.validate(observed, base, spec)

    def test_frontend_unknown_and_uploader_missing_rejected(self):
        base = baseline()
        spec = spec_for(base)
        for field, value in (
            ("old_frontend_image", None),
            ("uploader_mount", False),
            ("uploader_import", False),
        ):
            observed = deepcopy(base)
            observed["ops"][field] = value
            with self.assertRaises(Rejected):
                preflight.validate(observed, base, spec)

    def test_hook_unknown_and_app_elevated_rejected(self):
        base = baseline()
        spec = spec_for(base)
        observed = deepcopy(base)
        observed["external"]["github_zeabur_hooks_frozen"] = False
        with self.assertRaises(Rejected):
            preflight.validate(observed, base, spec)
        observed = deepcopy(base)
        observed["roles"]["app_bypassrls"] = True
        with self.assertRaises(Rejected):
            preflight.validate(observed, base, spec)

    def test_valid_projection_produces_no_live_authority(self):
        base = baseline()
        checked = preflight.validate(base, base, spec_for(base))
        self.assertEqual(
            checked["baseline_sha256"], spec_for(base).data["baseline_sha256"]
        )
        self.assertFalse(checked["live_verified"])

    def test_base_tag_drift_and_missing_wheel_block_build(self):
        for observed, wheel in (
            ("sha256:" + "c" * 64, True),
            ("sha256:" + "a" * 64, False),
        ):
            with self.assertRaises(Rejected):
                build_adapter.plan(
                    "backend", "a" * 40, "sha256:" + "a" * 64, observed, wheel
                )

    def test_interrupted_build_tag_is_not_image_receipt(self):
        with self.assertRaises(Rejected):
            build_adapter.accept({"tag": "exists"}, "a" * 40)
