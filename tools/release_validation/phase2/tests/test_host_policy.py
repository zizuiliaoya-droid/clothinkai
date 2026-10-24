"""Hand-built non-secret policy/approval bytes; no files, network or execution."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from contracts import STEPS, TARGET
from host_authority import validate_approval
from sql_migration_driver import SqlMigrationDriver
from sql_policy import (
    HostMigrationSpec,
    SqlRejected,
    _prepare_policy,
    canonical,
    parse,
    sha,
    validate_spec,
)
from tests.test_sql_migration_driver import fixture_policy, params


def policy_fixture():
    data, artifacts = fixture_policy()
    data["mode"] = "host_candidate"
    config = parse(artifacts["config"])
    for key in (
        "CREDENTIAL_MASTER_KEY",
        "JWT_SECRET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    ):
        config[key] = "local-unit-test-value-not-a-real-secret"
    config["R2_ENDPOINT_URL"] = "https://local-unit-test.invalid"
    for key in (
        "DATABASE_URL_APP",
        "DATABASE_URL_BYPASS",
        "DATABASE_URL_SYNC",
        "DATABASE_URL_ARCHIVER",
    ):
        config[key] = config[key].replace(
            "fixture-password", "local-unit-test-password"
        )
    artifacts["config"] = canonical(config)
    permit = parse(artifacts["permit"])
    permit.update(
        approval_ref="local-unit-test-reference-no-production-approval",
        config_sha256=sha(artifacts["config"]),
    )
    artifacts["permit"] = canonical(permit)
    for name in ("backup_receipt", "restore_receipt"):
        receipt = parse(artifacts[name])
        receipt.update(mode="host_observed", config_sha256=sha(artifacts["config"]))
        artifacts[name] = canonical(receipt)
    data["hashes"] = {k: sha(v) for k, v in artifacts.items()}
    spec = _prepare_policy(data, artifacts, HostMigrationSpec)
    binding = {
        "target": TARGET,
        "source_commit": data["source_commit"],
        "source_tree": data["source_tree"],
        "package_sha256": "a" * 64,
        "package_evidence_sha256": "b" * 64,
        "backend_image": "sha256:" + "c" * 64,
        "container_id": "d" * 64,
        "network_id": "e" * 64,
        "baseline_sha256": "f" * 64,
        "manual_freeze_sha256": "9" * 64,
        "frontend_image": "sha256:" + "8" * 64,
        "boot_id": "12345678-1234-1234-1234-123456789abc",
    }
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    approval = {
        "schema_version": 1,
        "purpose": "reviewed-flow78-release-execution",
        "target": TARGET,
        "policy_sha256": sha(spec.encoded),
        "host_binding_sha256": sha(canonical(binding)),
        "actions": list(STEPS),
        "boot_id": binding["boot_id"],
        "approval_ref": permit["approval_ref"],
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
    }
    raw = canonical(approval)
    binding["approval_sha256"] = sha(raw)
    return replace(spec, _binding=canonical(binding)), raw, now


class HostPolicyTests(unittest.TestCase):
    def test_host_policy_does_not_borrow_ci_spec_or_enable_live_flag(self):
        spec, raw, now = policy_fixture()
        self.assertIs(type(validate_spec(spec)), HostMigrationSpec)
        self.assertFalse(spec.data["live_enabled"])
        self.assertTrue(
            validate_approval(spec, raw, parse(spec._binding)["boot_id"], now)[
                "authority_verified"
            ]
        )
        data = spec.data
        data["live_enabled"] = True
        with self.assertRaises(SqlRejected):
            validate_spec(replace(spec, encoded=canonical(data)))

    def test_expiry_changed_boot_and_mutated_approval_rejected(self):
        spec, raw, now = policy_fixture()
        for document, boot, tick in (
            (raw + b" ", parse(spec._binding)["boot_id"], now),
            (raw, "different-boot", now),
            (raw, parse(spec._binding)["boot_id"], now + timedelta(hours=3)),
            (raw, parse(spec._binding)["boot_id"], now - timedelta(seconds=1)),
        ):
            with self.assertRaises(SqlRejected):
                validate_approval(spec, document, boot, tick)

    def test_sql_connection_requires_separate_authority_before_attempt(self):
        spec, _, _ = policy_fixture()
        driver = SqlMigrationDriver(spec)
        with patch(
            "host_authority.verify_authority",
            side_effect=SqlRejected("missing-operator-file"),
        ):
            with self.assertRaisesRegex(SqlRejected, "missing-operator-file"):
                driver.prepare_connect(
                    SimpleNamespace(name="postgresql", driver="asyncpg"), params()
                )
        self.assertEqual(driver.attempts, 0)

    def test_synthetic_receipts_or_fixture_permits_cannot_enter_host_policy(self):
        data, artifacts = fixture_policy()
        data["mode"] = "host_candidate"
        with self.assertRaises(SqlRejected):
            _prepare_policy(data, artifacts, HostMigrationSpec)

    def test_derived_migration_uses_release_approval_and_rejects_config_substitution(
        self,
    ):
        spec, raw, now = policy_fixture()
        binding = parse(spec._binding)
        original_binding = {
            k: v
            for k, v in binding.items()
            if k
            not in (
                "backend_image",
                "frontend_image",
                "container_id",
                "source_commit",
                "source_tree",
            )
        }
        original_binding["tools_sha256"] = "7" * 64
        origin = {
            **{
                k: spec.data[k]
                for k in (
                    "source_commit",
                    "source_tree",
                    "release_id",
                    "from_revision",
                    "target_revision",
                )
            },
            "mode": "host_release_candidate",
            "deployable": False,
            "live_enabled": False,
            "config_sha256": spec.data["hashes"]["config"],
            "approval_ref": parse(spec.artifact("permit"))["approval_ref"],
        }
        approval = parse(raw)
        approval.update(
            policy_sha256=sha(canonical(origin)),
            host_binding_sha256=sha(
                canonical(
                    {
                        k: v
                        for k, v in original_binding.items()
                        if k != "approval_sha256"
                    }
                )
            ),
        )
        raw = canonical(approval)
        original_binding["approval_sha256"] = binding["approval_sha256"] = sha(raw)
        spec = replace(
            spec,
            _binding=canonical(binding),
            _authority=canonical({"data": origin, "binding": original_binding}),
        )
        self.assertTrue(
            validate_approval(spec, raw, binding["boot_id"], now)["authority_verified"]
        )
        self.assertNotEqual(approval["policy_sha256"], sha(spec.encoded))
        origin["config_sha256"] = "0" * 64
        with self.assertRaises(SqlRejected):
            validate_spec(
                replace(
                    spec,
                    _authority=canonical({"data": origin, "binding": original_binding}),
                )
            )


if __name__ == "__main__":
    unittest.main()
