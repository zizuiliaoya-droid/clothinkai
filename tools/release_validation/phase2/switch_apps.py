"""Synthetic app replacement constraints; no Docker execution or permit issuer."""

from contracts import AFTER, exact, require, sha


def validate_start(binding, role, commit):
    # This is a simulator intent binding, NOT phase1's executable start permit.
    exact(
        binding,
        (
            "mode",
            "role",
            "operation",
            "head",
            "commit",
            "schema_receipt_sha256",
            "config_sha256",
            "guard_directory",
            "read_only",
            "live_enabled",
            "gate_closed",
        ),
    )
    require(role in {"api", "worker", "beat"}, "start_role")
    sha(commit, 40)
    sha(binding["schema_receipt_sha256"])
    sha(binding["config_sha256"])
    require(
        binding["mode"] == "synthetic"
        and binding["live_enabled"] is False
        and binding["role"] == role
        and binding["operation"] == "start"
        and binding["head"] == AFTER
        and binding["commit"] == commit
        and binding["guard_directory"] == "guards/" + role
        and binding["read_only"] is True
        and binding["gate_closed"] is True,
        "role_binding",
    )
    return True


def verify_replaced(before, after, role, image):
    require(
        role in {"api", "web", "worker", "beat"}
        and set(before) == set(after)
        and {"db", "redis", role} <= set(before),
        "replace_scope",
    )
    require(type(image) is str and image.startswith("sha256:"), "image_id")
    sha(image[7:])
    require(
        all(before[k] == after[k] for k in before if k != role),
        "unrelated_service_changed",
    )
    require(
        after[role]["image"] == image and after[role]["id"] != before[role]["id"],
        "replace_identity",
    )
    sha(after[role]["id"])
    return True
