"""Local real Git over synthetic temporary source; no Docker/network/product tree."""

import hashlib
import importlib.util
import io
import json
import socket
import sys
import tarfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PHASE1 = HERE.parent / "release-flow78-1021"
if not PHASE1.is_dir():
    PHASE1 = HERE.parent / "phase1"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PHASE1))
spec = importlib.util.spec_from_file_location(
    "frozen_package_fixture", PHASE1 / "test_release_tools.py"
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
import phase1_package_binding as binding  # noqa: E402 - pinned fixture imports precede this


def deny_network(*args, **kwargs):
    raise AssertionError("network forbidden in source test")


socket.socket = deny_network
socket.create_connection = deny_network


def rearchive(raw, change):
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as source:
        content = {m.name: source.extractfile(m).read() for m in source}
    change(content)
    manifest = json.loads(content["release-manifest.json"])
    manifest["files"] = {
        n: {"bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
        for n, b in content.items()
        if n != "release-manifest.json"
    }
    content["release-manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as out:
        for name, value in content.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            out.addfile(member, io.BytesIO(value))
    return buffer.getvalue()


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BuilderTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        out = f.root / "candidate.tar.gz"
        fixtures.builder.build(f.repo, f.head, f.pins, f.dist, f.dep, out)
        self.raw = out.read_bytes()
        self.freeze = {
            "mode": "synthetic_fixture",
            "source_commit": f.head,
            "source_tree": f.tree,
            "pins_sha256": hashlib.sha256(
                fixtures.builder.canonical(f.pins)
            ).hexdigest(),
            "archive_sha256": hashlib.sha256(self.raw).hexdigest(),
        }

    def test_complete_real_git_source_and_phase1_contract(self):
        result = binding.verify_package(self.raw, self.fixture.repo, self.freeze)
        self.assertTrue(result.get("semantic_verified"))
        self.assertEqual(result.get("source_tree"), self.fixture.tree)
        self.assertFalse(result.get("deployable", True))

    def test_linux_policy_is_distinct_and_cannot_be_enabled_or_retargeted(self):
        from sql_policy import LinuxCISpec, canonical, prepare_linux_ci, validate_spec

        package = binding.verify_package(self.raw, self.fixture.repo, self.freeze)
        phase1 = binding.frozen_contract().HERE
        artifacts = {
            "phase1_pins": (phase1 / "implementation-pins.json").read_bytes(),
            "phase1_guard": (phase1 / "runtime_guard.py").read_bytes(),
        }
        policy = prepare_linux_ci(package, "a" * 32, artifacts, binding.tool_manifest())
        self.assertIs(type(policy), LinuxCISpec)
        self.assertEqual(policy.data["mode"], "linux_ci")
        self.assertNotIn("permit", dict(policy._artifacts))
        for change in (
            {"live_enabled": True},
            {"mode": "live"},
            {"operation": "downgrade"},
            {"target_revision": "head"},
            {"nonce": "../production"},
        ):
            with self.assertRaises(ValueError):
                validate_spec(
                    LinuxCISpec(canonical({**policy.data, **change}), policy._artifacts)
                )

    def test_self_consistent_manifest_cannot_replace_committed_source(self):
        altered = rearchive(
            self.raw,
            lambda c: c.__setitem__("backend/app/main.py", b"UNCOMMITTED = True\n"),
        )
        freeze = {**self.freeze, "archive_sha256": hashlib.sha256(altered).hexdigest()}
        with self.assertRaises(ValueError):
            binding.verify_package(altered, self.fixture.repo, freeze)

    def test_unknown_final_tree_or_independent_pin_hash_rejected(self):
        for values in (
            {"source_commit": None},
            {"source_tree": "0" * 40},
            {"pins_sha256": "0" * 64},
        ):
            with self.assertRaises(ValueError):
                binding.verify_package(
                    self.raw, self.fixture.repo, {**self.freeze, **values}
                )

    def test_control_and_wheel_mutation_with_reforged_tar_manifest_rejected(self):
        for name in ("control/start_067.py", "wheelhouse/demo-1.0-py3-none-any.whl"):
            altered = rearchive(
                self.raw, lambda c: c.__setitem__(name, b"changed fixture")
            )
            with self.assertRaises(ValueError):
                binding.verify_package(
                    altered,
                    self.fixture.repo,
                    {
                        **self.freeze,
                        "archive_sha256": hashlib.sha256(altered).hexdigest(),
                    },
                )
