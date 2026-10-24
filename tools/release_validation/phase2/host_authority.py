"""Read-only verifier for a separately provisioned, fixed private approval artifact.

No issuer, writer, environment override, CLI switch, or approval generation exists.
Only a privileged operator can place this file after the concrete release review.
"""

import os
from datetime import datetime, timezone

from contracts import STEPS, TARGET
from sql_policy import canonical, fields, need, parse, sha


def validate_approval(spec, raw, boot_id, now):
    from host_release_input import HostReleaseSpec, validate_host_input

    validate_host_input(spec)
    binding = parse(spec._binding)
    policy_encoded = spec.encoded
    approval_ref = (
        spec.data["approval_ref"]
        if type(spec) is HostReleaseSpec
        else parse(spec.artifact("permit"))["approval_ref"]
    )
    if getattr(spec, "_authority", b""):
        origin = parse(spec._authority)
        policy_encoded, binding = canonical(origin["data"]), origin["binding"]
    need(sha(raw) == binding["approval_sha256"], "host_approval_hash")
    value = parse(raw)
    fields(
        value,
        {
            "schema_version",
            "purpose",
            "target",
            "policy_sha256",
            "host_binding_sha256",
            "actions",
            "boot_id",
            "approval_ref",
            "issued_at",
            "expires_at",
        },
        "host_approval_fields",
    )
    need(
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and value["purpose"] == "reviewed-flow78-release-execution"
        and value["target"] == TARGET
        and value["actions"] == list(STEPS),
        "host_approval_scope",
    )
    need(
        value["policy_sha256"] == sha(policy_encoded)
        and value["host_binding_sha256"]
        == sha(canonical({k: v for k, v in binding.items() if k != "approval_sha256"})),
        "host_approval_binding",
    )
    need(value["boot_id"] == binding["boot_id"] == boot_id, "host_approval_boot")
    need(
        value["approval_ref"] == approval_ref
        and not value["approval_ref"].startswith("fixture-"),
        "host_approval_reference",
    )
    try:
        issued, expires = (
            datetime.fromisoformat(value[k]) for k in ("issued_at", "expires_at")
        )
        need(
            issued.utcoffset().total_seconds()
            == expires.utcoffset().total_seconds()
            == 0,
            "host_approval_utc",
        )
        need(
            0 < (expires - issued).total_seconds() <= 21600 and issued <= now < expires,
            "host_approval_expiry",
        )
    except (ValueError, TypeError, AttributeError):
        need(False, "host_approval_time")
    return {"approval_sha256": sha(raw), "authority_verified": True}


def verify_authority(spec):
    from fixed_host_transport import _private_fixed_file
    from host_sequence import HostDirectory
    from linux_transport import require_linux

    require_linux()
    need(os.geteuid() == 0, "host_authority_root")
    need(
        _private_fixed_file("/var/lib/cloud/data/instance-id", 128).decode().strip()
        == TARGET["instance"],
        "host_authority_instance",
    )
    directory = HostDirectory(spec.data["source_commit"])
    try:
        raw = directory.read("approval.json", 16384)
    finally:
        directory.close()
    fd = os.open("/proc/sys/kernel/random/boot_id", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        boot_id = os.read(fd, 128).decode("ascii").strip()
    finally:
        os.close(fd)
    return validate_approval(spec, raw, boot_id, datetime.now(timezone.utc))
