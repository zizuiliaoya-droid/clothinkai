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
    raw = (HERE / "chain-bundle-manifest-1024.json").read_bytes()
    manifest = json.loads(raw)
    require(manifest["schema_version"] == 1, "manifest schema")
    require(manifest["frozen"] is True, "root has not frozen the candidate bundle")
    require(manifest["source_commit"] == SOURCE_COMMIT, "source commit drift")
    require(manifest["source_tree"] == SOURCE_TREE, "source tree drift")
    transfer_raw = (HERE / "chain-ci-transfer-pins-1024.json").read_bytes()
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
    historical_raw = (HERE / "checkpoint-ci-transfer-pins-1024.json").read_bytes()
    require(
        sha256(historical_raw)
        == manifest["base_ci_transfer"]
        == transfer["base_transfer_pins_sha256"],
        "historical transfer pins drift",
    )
    files = manifest["files"]
    require(bool(files), "empty bundle")
    additions = manifest["ci_additions"]
    deltas = manifest["ci_test_deltas"]
    require(
        set(deltas)
        == {
            "phase2/ci_tests/test_dashboard_contract.py",
            "phase2/ci_tests/test_host_sequence.py",
            "phase2/tests/test_host_acceptance_control.py",
            "phase2/tests/test_host_freeze_control.py",
            "phase2/tests/test_host_backup_control.py",
        },
        "finite successor test deltas required",
    )
    for name, delta in deltas.items():
        require(delta["before"] == transfer["files"][name], "test delta origin drift")
    changed = {name: delta["after"] for name, delta in deltas.items()}
    runtime_deltas = manifest["host_runtime_deltas"]
    require(
        set(runtime_deltas)
        == {
            "phase2/host_gate_control.py",
            "phase2/host_web_control.py",
            "phase2/templates/nginx-maintenance.conf",
            "phase2/templates/nginx-release-closed.conf",
            "phase2/host_backup_control.py",
            "phase2/host_acceptance_control.py",
            "phase2/host_producer_control.py",
            "phase2/restore_drill.py",
            "phase1/schedule_pickle_guard.py",
            "phase1/Dockerfile.frontend.offline.in",
            "phase2/host_build_control.py",
            "phase2/phase1_package_binding.py",
            "phase2/beat_health_candidate.py",
        },
        "finite authorized host runtime delta required",
    )
    for name, delta in runtime_deltas.items():
        require(delta["before"] == transfer["files"][name], "host delta origin drift")
    changed.update({name: delta["after"] for name, delta in runtime_deltas.items()})
    public_raw = (HERE / "original-public-pins-1024.json").read_bytes()
    require(
        sha256(public_raw) == manifest["original_public_pins_sha256"],
        "original public dependency pins drift",
    )
    public = json.loads(public_raw)
    dependencies = manifest["public_dependencies"]
    require(
        public["runtime_execution_authorized"] is False
        and public["secrets_included"] is False
        and dependencies == public["files"]
        and set(dependencies)
        == {
            "production-infra-1018/backup.py",
            "production-infra-1018/upload_backup.py",
            "app-trial-infra-1018/bootstrap-roles.sql",
        },
        "exact public hash/import dependencies required",
    )
    fixture_raw = (HERE / "fixture-input-pins-1024.json").read_bytes()
    require(
        sha256(fixture_raw) == manifest["fixture_input_pins_sha256"],
        "original fixture input pins drift",
    )
    fixture = json.loads(fixture_raw)
    fixture_inputs = manifest["fixture_inputs"]
    require(
        fixture["secret_values_included"] is False
        and fixture["production_execution_authorized"] is False
        and set(fixture["files"])
        == {"original-backend.Dockerfile", "requirements-linux.lock"}
        and fixture_inputs
        == {"phase2/ci_tests/" + n: p for n, p in fixture["files"].items()},
        "exact original fixture inputs required",
    )
    original_health = [
        line
        for line in (HERE / "phase2/ci_tests/original-backend.Dockerfile")
        .read_bytes()
        .splitlines()
        if line.startswith(b"HEALTHCHECK ")
    ]
    synthetic_health = [
        line
        for line in (HERE / "phase2/ci_tests/producer-fixture.Dockerfile")
        .read_bytes()
        .splitlines()
        if line.startswith(b"HEALTHCHECK ")
    ]
    require(
        len(original_health) == 1 and synthetic_health == original_health,
        "exact original CMD-SHELL healthcheck required",
    )
    require(
        not set(additions) & set(transfer["files"])
        and all(n.startswith("phase2/ci_tests/") for n in additions),
        "CI additions must not overwrite frozen source",
    )
    require(
        files
        == {
            **transfer["files"],
            **changed,
            **additions,
            **dependencies,
            **fixture_inputs,
        },
        "approved transfer allowlist drift",
    )
    actual = set()
    for phase in ("phase1", "phase2", "production-infra-1018", "app-trial-infra-1018"):
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
            re.fullmatch(
                r"(?:phase[12]|production-infra-1018|app-trial-infra-1018)/[A-Za-z0-9_./-]+",
                name,
            )
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
                expected = phase_files.get(relative)
                if name in {
                    "phase1/schedule_pickle_guard.py",
                    "phase1/Dockerfile.frontend.offline.in",
                }:
                    delta = runtime_deltas[name]
                    require(delta["before"] == expected, "phase1 control origin drift")
                    expected = delta["after"]
                require(expected == pin, "unbound phase1 file: " + name)
    require(
        manifest["deployable"] is False
        and manifest["live_enabled"] is False
        and manifest["production_29_actions_verified"] is False
        and manifest["backup_module_included"] is True,
        "candidate boundary",
    )
    require(
        "phase2/host_backup_control.py" in transfer["files"], "unfrozen backup module"
    )
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
        "original_public_pins_sha256": manifest["original_public_pins_sha256"],
        "fixture_input_pins_sha256": manifest["fixture_input_pins_sha256"],
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
