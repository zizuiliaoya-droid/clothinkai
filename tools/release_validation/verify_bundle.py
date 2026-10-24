"""Verify an explicit public-source snapshot before importing any bundled code."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE_COMMIT = "55477c44f09bb1a25169bfb44ff6c90bc4f9cedb"
SOURCE_TREE = "28c0cac9a15cc48a5dfa5c7d3ed732cd6a59abdc"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def git(repository, *args):
    return subprocess.check_output(
        ["git", "-C", str(repository), *args], text=True, timeout=30
    ).strip()


def verify():
    raw = (HERE / "checkpoint-bundle-manifest-1024.json").read_bytes()
    manifest = json.loads(raw)
    require(manifest["schema_version"] == 1, "manifest schema")
    require(manifest["frozen"] is True, "root has not frozen the candidate bundle")
    require(manifest["source_commit"] == SOURCE_COMMIT, "source commit drift")
    require(manifest["source_tree"] == SOURCE_TREE, "source tree drift")
    transfer_raw = (HERE / "checkpoint-ci-transfer-pins-1024.json").read_bytes()
    require(
        sha256(transfer_raw) == manifest["pins"]["ci_transfer"], "transfer pins drift"
    )
    transfer = json.loads(transfer_raw)
    require(
        transfer["source_commit"] == SOURCE_COMMIT
        and transfer["source_tree"] == SOURCE_TREE,
        "transfer source binding",
    )
    require(manifest["fixture_only"] is True, "product source must remain fixture only")
    historical_raw = (HERE / "host-ci-transfer-pins-1023.json").read_bytes()
    require(
        sha256(historical_raw)
        == manifest["base_ci_transfer"]
        == transfer["base_transfer_pins_sha256"],
        "historical transfer pins drift",
    )
    files = manifest["files"]
    require(bool(files), "empty bundle")
    require(files == transfer["files"], "approved transfer allowlist drift")
    actual = set()
    for phase in ("phase1", "phase2"):
        root = HERE / phase
        require(
            root.is_dir() and not root.is_symlink() and not root.is_junction(),
            "missing or linked phase",
        )
        for path in root.rglob("*"):
            require(not path.is_symlink() and not path.is_junction(), "linked member")
            if path.is_file():
                require(path.stat().st_nlink == 1, "hardlinked member")
                actual.add(path.relative_to(HERE).as_posix())
    require(actual == set(files), "bundle file allowlist mismatch")
    for name, pin in files.items():
        require(
            re.fullmatch(r"phase[12]/[A-Za-z0-9_./-]+", name)
            and ".." not in name.split("/"),
            "unsafe bundle path",
        )
        content = (HERE / name).read_bytes()
        require(pin == {"bytes": len(content), "sha256": sha256(content)}, name)
    require(
        set(manifest["pins"]) == {"phase1_package", "phase1_sql_guard", "ci_transfer"},
        "exact pin identities required",
    )
    raw_pins = (HERE / "phase1/implementation-pins-origin-1024.json").read_bytes()
    require(
        sha256(raw_pins)
        == manifest["pins"]["phase1_package"]
        == transfer["phase1_package_pins_sha256"],
        "phase1 package pins drift",
    )
    guard_raw = (HERE / "phase1/implementation-pins.json").read_bytes()
    require(
        sha256(guard_raw)
        == manifest["pins"]["phase1_sql_guard"]
        == transfer["phase1_sql_guard_pins_sha256"],
        "phase1 SQL guard pins drift",
    )
    phase_files = json.loads(raw_pins)["files"]
    for name, pin in files.items():
        if name.startswith("phase1/"):
            relative = name.removeprefix("phase1/")
            if relative not in {
                "implementation-pins.json",
                "implementation-pins-origin-1024.json",
            }:
                require(
                    phase_files.get(relative) == pin, "unbound phase1 file: " + name
                )
    require(
        manifest["deployable"] is False
        and manifest["live_enabled"] is False
        and manifest["production_29_actions_verified"] is False
        and manifest["backup_module_included"] is False,
        "candidate boundary",
    )
    require("phase2/host_backup_control.py" not in files, "unfrozen backup module")
    return manifest, sha256(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-tool-commit", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(
        re.fullmatch(r"[0-9a-f]{40}", args.expected_tool_commit), "CI commit format"
    )
    manifest, manifest_hash = verify()
    tool_commit = git(HERE, "rev-parse", "HEAD")
    require(tool_commit == args.expected_tool_commit, "CI tool checkout drift")
    require(
        not git(HERE, "status", "--porcelain", "--untracked-files=all"),
        "dirty tool checkout",
    )
    dependencies = {}
    if args.source:
        require(git(args.source, "rev-parse", "HEAD") == SOURCE_COMMIT, "product HEAD")
        require(
            git(args.source, "rev-parse", "HEAD^{tree}") == SOURCE_TREE, "product tree"
        )
        require(
            not git(args.source, "status", "--porcelain", "--untracked-files=all"),
            "dirty source checkout",
        )
        for name in ("backend/requirements.txt", "backend/requirements-dev.txt"):
            dependencies[name] = sha256((args.source / name).read_bytes())
    result = {
        "schema_version": 1,
        "data_kind": "synthetic_validation_provenance",
        "source_commit": SOURCE_COMMIT,
        "source_tree": SOURCE_TREE,
        "source_checkout_verified": args.source is not None,
        "source_fixture_only": True,
        "source_kind": "unmerged_candidate_not_final_main",
        "historical_tool_commit": manifest["historical_tool_commit"],
        "historical_transfer_sha256": manifest["base_ci_transfer"],
        "tool_commit": tool_commit,
        "tool_pins": manifest["pins"],
        "bundle_manifest_sha256": manifest_hash,
        "bundle_file_count": len(manifest["files"]),
        "dependency_declaration_sha256": dependencies,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "package_semantics_verified": False,
        "production_29_actions_verified": False,
        "deployable": False,
        "live_enabled": False,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "provenance.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
