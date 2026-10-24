"""Immutable release input before build/backup; SQL policy is derived at MIGRATE.

No predicted Docker IDs or future backup receipts are accepted as observations.
"""

import re
from dataclasses import dataclass, field

from contracts import AFTER, BEFORE, TARGET, canonical, digest, exact, require, sha
from phase1_package_binding import BoundPackage
from sql_policy import parse


@dataclass(frozen=True)
class HostReleaseSpec:
    encoded: bytes = field(repr=False)
    _binding: bytes = field(repr=False)
    package: BoundPackage = field(repr=False)
    _tools: tuple = field(repr=False)

    @property
    def data(self):
        return parse(self.encoded)

    @property
    def sha256(self):
        from sql_policy import sha as hash_bytes

        return hash_bytes(self.encoded + b"\0" + self._binding)

    def artifact(self, name):
        require(name == "release", "release_artifact_unavailable_before_migration")
        return self.package.member("release.json")


def prepare_release(package, binding, approval_ref, config_sha256, tools):
    from sql_policy import sha as hash_bytes

    data = {
        "mode": "host_release_candidate",
        "live_enabled": False,
        "deployable": False,
        "source_commit": package.get("source_commit"),
        "source_tree": package.get("source_tree"),
        "release_id": "flow78-" + package.get("source_commit")[:12],
        "from_revision": BEFORE,
        "target_revision": AFTER,
        "approval_ref": approval_ref,
        "config_sha256": config_sha256,
        "members_sha256": digest(
            {n: {"bytes": len(b), "sha256": hash_bytes(b)} for n, b in package._members}
        ),
    }
    return validate_release(
        HostReleaseSpec(
            canonical(data), canonical(binding), package, tuple(sorted(tools.items()))
        )
    )


def validate_release(spec):
    from sql_policy import sha as hash_bytes

    require(type(spec) is HostReleaseSpec, "host_release_type")
    data, binding, package = spec.data, parse(spec._binding), spec.package
    exact(
        data,
        (
            "mode",
            "live_enabled",
            "deployable",
            "source_commit",
            "source_tree",
            "release_id",
            "from_revision",
            "target_revision",
            "approval_ref",
            "config_sha256",
            "members_sha256",
        ),
    )
    require(
        data["mode"] == "host_release_candidate"
        and data["live_enabled"] is False
        and data["deployable"] is False
        and data["from_revision"] == BEFORE
        and data["target_revision"] == AFTER,
        "host_release_mode",
    )
    require(
        type(package) is BoundPackage
        and package.get("semantic_verified") is True
        and package.get("mode") == "candidate_review",
        "host_full_package_required",
    )
    for key in ("source_commit", "source_tree"):
        sha(data[key], 40)
        require(data[key] == package.get(key), "host_release_source")
    require(
        data["release_id"] == "flow78-" + data["source_commit"][:12], "host_release_id"
    )
    require(
        type(data["approval_ref"]) is str
        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", data["approval_ref"])
        and not data["approval_ref"].startswith("fixture-"),
        "host_approval_reference",
    )
    sha(data["config_sha256"])
    exact(
        binding,
        (
            "target",
            "package_sha256",
            "package_evidence_sha256",
            "baseline_sha256",
            "approval_sha256",
            "manual_freeze_sha256",
            "boot_id",
            "tools_sha256",
            "network_id",
        ),
    )
    require(
        binding["target"] == TARGET
        and binding["package_sha256"] == package.get("archive_sha256")
        and binding["package_evidence_sha256"] == package.sha256,
        "host_release_package",
    )
    for key in binding:
        if key.endswith("sha256") or key == "network_id":
            sha(binding[key])
    require(
        type(binding["boot_id"]) is str
        and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", binding["boot_id"]
        ),
        "host_release_boot",
    )
    tools = dict(spec._tools)
    require(len(tools) == len(spec._tools) and 1 <= len(tools) <= 256, "host_tools_set")
    require(
        binding["tools_sha256"]
        == digest(
            {n: {"bytes": len(b), "sha256": hash_bytes(b)} for n, b in tools.items()}
        ),
        "host_tools_binding",
    )
    # BoundPackage is produced by the semantic verifier; recheck every retained byte
    # against its source pins where applicable before writing any build context.
    members = dict(package._members)
    require(len(members) == len(package._members), "host_duplicate_member")
    require(
        data["members_sha256"]
        == digest(
            {n: {"bytes": len(b), "sha256": hash_bytes(b)} for n, b in members.items()}
        ),
        "host_all_member_bytes",
    )
    for name, pin in package.get("source_members").items():
        raw = members[name]
        require(
            pin == {"bytes": len(raw), "sha256": hash_bytes(raw)}, "host_source_bytes"
        )
    for name, raw in (*package._members, *spec._tools):
        require(
            type(name) is str
            and re.fullmatch(r"[A-Za-z0-9_./-]+", name)
            and all(p not in ("", ".", "..") for p in name.split("/")),
            "host_member_path",
        )
        require(type(raw) is bytes and len(raw) <= 16 * 1024**2, "host_member_size")
    return spec


def validate_host_input(spec):
    if type(spec) is HostReleaseSpec:
        return validate_release(spec)
    from sql_policy import validate_host_policy

    return validate_host_policy(spec)


def require_step(spec, journal, step):
    from host_sequence import HostJournal, replay
    from sql_policy import require_host_authority

    require_host_authority(spec)
    require(
        type(journal) is HostJournal and journal.lock is not None, "host_action_lock"
    )
    rows = journal.read()
    state = replay(spec, rows)
    require(
        state.pending is not None
        and state.hold is None
        and rows[-1]["kind"] == "intent"
        and rows[-1]["payload"]["name"] == step,
        "host_action_intent",
    )
