"""Synthetic package and fixed Docker boundary; no daemon or production bytes."""

import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from contracts import AFTER, TARGET, canonical, digest
from host_build_control import build
from host_release_input import prepare_release, validate_release
from phase1_package_binding import BoundPackage
from sql_policy import parse, sha
from tests.test_host_finite_operations import FixtureDirectory
from tests.test_migration_process import NoSocketAsyncCase


def release_fixture():
    files = {
        "backend/app/__init__.py": b"",
        "backend/alembic.ini": b"[alembic]",
        "frontend-dist/index.html": b"https://47.110.76.9",
        "release.json": b"{}",
    }
    pins = {
        n: {"bytes": len(b), "sha256": sha(b)}
        for n, b in files.items()
        if n.startswith("backend/")
    }
    base = {
        k: {
            "local_tag": "clothinkai-trial-" + k + "-base:20261018",
            "image_id": "sha256:" + "c" * 64,
        }
        for k in ("python", "nginx")
    }
    package = BoundPackage(
        canonical(
            {
                "semantic_verified": True,
                "mode": "candidate_review",
                "source_commit": "a" * 40,
                "source_tree": "b" * 40,
                "archive_sha256": "d" * 64,
                "source_members": pins,
                "base_images": base,
            }
        ),
        tuple(sorted(files.items())),
    )
    tools = {"synthetic-test.py": b"pass\n"}
    binding = {
        "target": TARGET,
        "package_sha256": "d" * 64,
        "package_evidence_sha256": package.sha256,
        "baseline_sha256": "e" * 64,
        "approval_sha256": "f" * 64,
        "manual_freeze_sha256": "1" * 64,
        "boot_id": "12345678-1234-1234-1234-123456789abc",
        "network_id": "2" * 64,
        "tools_sha256": digest(
            {n: {"bytes": len(b), "sha256": sha(b)} for n, b in tools.items()}
        ),
    }
    return prepare_release(
        package, binding, "unit-test-no-live-authority", "3" * 64, tools
    )


class BuildTests(NoSocketAsyncCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-")
        self.spec = release_fixture()
        from types import SimpleNamespace

        self.journal = SimpleNamespace(directory=FixtureDirectory(Path(self.temp.name)))

    async def asyncTearDown(self):
        self.temp.cleanup()

    def image(self, *, base=False):
        return {
            "id": "sha256:" + ("c" if base else "4") * 64,
            "os": "linux",
            "architecture": "amd64",
            "revision": None if base else "a" * 40,
            "schema": AFTER,
            "origin": TARGET["origin"],
        }

    async def test_release_inputs_require_no_future_images_or_backup(self):
        self.assertEqual(validate_release(self.spec), self.spec)
        self.assertNotIn("backend_image", parse(self.spec._binding))
        with self.assertRaises(Exception):
            self.spec.artifact("backup_receipt")
        wrong = replace(
            self.spec.package,
            _members=tuple(
                (n, b + b"changed" if n.startswith("backend") else b)
                for n, b in self.spec.package._members
            ),
        )
        with self.assertRaises(Exception):
            validate_release(replace(self.spec, package=wrong))

    async def test_backend_intent_before_build_and_content_probe_before_receipt(self):
        calls = []

        async def capture(argv, **kwargs):
            calls.append(argv)
            if "build" in argv:
                self.assertTrue(
                    (Path(self.temp.name) / "build-backend-intent.json").exists()
                )
                self.assertIn("--network=none", argv)
                self.assertIn("--pull=false", argv)
            if "-c" in argv:
                return canonical(
                    {
                        "files": {
                            n.removeprefix("backend/"): p
                            for n, p in self.spec.package.get("source_members").items()
                        },
                        "heads": [AFTER],
                        "imports": True,
                    }
                )
            return b""

        with (
            patch("host_build_control.require_step"),
            patch("host_build_control.verify_build_tree"),
            patch(
                "host_build_control._image",
                side_effect=[self.image(base=True), self.image()],
            ),
            patch("host_build_control._capture", side_effect=capture),
        ):
            result = await build(self.spec, self.journal, "backend")
        self.assertEqual(result["mode"], "host_observed")
        self.assertTrue((Path(self.temp.name) / "build-backend-observed.json").exists())
        self.assertEqual(sum("build" in call for call in calls), 1)

    async def test_base_drift_prevents_build_and_intent(self):
        execute = AsyncMock()
        with (
            patch("host_build_control.require_step"),
            patch("host_build_control.verify_build_tree"),
            patch("host_build_control._image", return_value=self.image()),
            patch("host_build_control._capture", execute),
        ):
            with self.assertRaises(Exception):
                await build(self.spec, self.journal, "backend")
        self.assertEqual(execute.await_count, 0)
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    async def test_frontend_overlay_must_match_and_preserve_observed_base_assets(self):
        assets = {"index.html": sha(b"old base"), "50x.html": sha(b"base error page")}
        result = {**assets, "index.html": sha(b"https://47.110.76.9")}
        with (
            patch("host_build_control.require_step"),
            patch("host_build_control.verify_build_tree"),
            patch(
                "host_build_control._image",
                side_effect=[self.image(base=True), self.image()],
            ),
            patch("host_build_control._capture", return_value=b""),
            patch("host_build_control._frontend_files", side_effect=[assets, result]),
        ):
            await build(self.spec, self.journal, "frontend")
        self.assertTrue(
            (Path(self.temp.name) / "build-frontend-observed.json").exists()
        )

    async def test_failed_content_readback_retains_intent_without_success(self):
        with (
            patch("host_build_control.require_step"),
            patch("host_build_control.verify_build_tree"),
            patch(
                "host_build_control._image",
                side_effect=[self.image(base=True), self.image()],
            ),
            patch("host_build_control._capture", return_value=b""),
            patch(
                "host_build_control._frontend_files",
                side_effect=[{}, {"foreign.js": "e" * 64}],
            ),
        ):
            with self.assertRaises(Exception):
                await build(self.spec, self.journal, "frontend")
        self.assertTrue((Path(self.temp.name) / "build-frontend-intent.json").exists())
        self.assertFalse(
            (Path(self.temp.name) / "build-frontend-observed.json").exists()
        )
