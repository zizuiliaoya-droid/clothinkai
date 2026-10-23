"""Validate safe synthetic projections; no collectors or live environment access."""

import hashlib
import io
from pathlib import PurePosixPath
import tarfile
from contracts import BEFORE, TARGET, Rejected, digest, exact, parse_json, require, sha


def verify_candidate(raw, commit, tree):
    try:
        return _verify_candidate(raw, commit, tree)
    except (tarfile.TarError, OSError, EOFError, KeyError, TypeError, ValueError):
        raise Rejected("candidate_archive_invalid") from None


def _verify_candidate(raw, commit, tree):
    require(type(raw) is bytes and len(raw) <= 256 * 1024**2, "archive_size")
    sha(commit, 40)
    sha(tree, 40)
    members, total = {}, 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            name = member.name
            path = PurePosixPath(name)
            require(
                member.isfile()
                and name not in members
                and not path.is_absolute()
                and ".." not in path.parts
                and "\\" not in name
                and ":" not in name
                and str(path) == name
                and member.size <= 64 * 1024**2,
                "archive_member",
            )
            require(
                name == "release-manifest.json"
                or name.startswith(
                    (
                        "backend/app/",
                        "backend/alembic/",
                        "control/",
                        "frontend-dist/",
                        "wheelhouse/",
                    )
                )
                or name
                in {
                    "backend/alembic.ini",
                    "backend/requirements.txt",
                    "requirements-linux.lock",
                    "release.json",
                },
                "archive_allowlist",
            )
            require(
                not any(
                    part.startswith(".") or "secret" in part.lower()
                    for part in path.parts
                ),
                "archive_private_path",
            )
            total += member.size
            require(total <= 512 * 1024**2 and len(members) < 10000, "archive_budget")
            stream = archive.extractfile(member)
            require(stream is not None, "archive_stream")
            content = stream.read(member.size + 1)
            require(len(content) == member.size, "archive_truncated")
            members[name] = content
    require("release-manifest.json" in members, "manifest_missing")
    manifest = parse_json(members.pop("release-manifest.json"))
    require(
        manifest["deployable"] is False
        and manifest["pins"]["source_commit"] == commit
        and manifest["pins"]["source_tree"] == tree,
        "manifest_identity",
    )
    require(set(manifest["files"]) == set(members), "manifest_members")
    for name, payload in members.items():
        expected = manifest["files"][name]
        require(
            expected["bytes"] == len(payload)
            and expected["sha256"] == hashlib.sha256(payload).hexdigest(),
            "member_hash",
        )
    return {
        "members": tuple(sorted(members)),
        "deployable": False,
        "validation_scope": "structural_only",
        "semantic_phase1_verified": False,
        "production_build_allowed": False,
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "member_bytes": total,
    }


def validate(observed, baseline, spec):
    require(digest(baseline) == spec.data["baseline_sha256"], "baseline_identity")
    exact(observed, baseline)
    require(
        observed["identity"] == TARGET
        and observed["schema"] == BEFORE
        and observed["pg_version"] == "18.6",
        "server_database_identity",
    )
    for key in ("compose", "network_ids", "roles", "ops"):
        require(observed[key] == baseline[key], "baseline_drift_" + key)
    require(
        observed["roles"]
        == {
            "app_superuser": False,
            "app_bypassrls": False,
            "bypass_superuser": False,
            "bypass_bypassrls": True,
            "owner_expected": True,
        },
        "roles",
    )
    require(
        observed["ops"]["uploader_mount"] is True
        and observed["ops"]["uploader_import"] is True,
        "uploader",
    )
    frontend = observed["ops"]["old_frontend_image"]
    require(
        type(frontend) is str and frontend.startswith("sha256:"), "old_frontend_unknown"
    )
    sha(frontend[7:])
    require(
        frontend == observed["containers"]["prod-web"]["image"], "frontend_identity"
    )
    require(
        observed["external"]
        == {"old_jobs_stopped": True, "github_zeabur_hooks_frozen": True},
        "external_sources",
    )
    require(
        observed["certificate"]["ip_match"] is True
        and observed["certificate"]["remaining_seconds"] >= 172800,
        "certificate_margin",
    )
    require(
        observed["space"]["free_bytes"] >= observed["space"]["required_bytes"] > 0,
        "space",
    )
    require(
        set(observed["containers"])
        == {
            "prod-db",
            "prod-redis",
            "prod-api",
            "prod-web",
            "prod-worker",
            "prod-beat",
        },
        "six_containers",
    )
    for name, current in observed["containers"].items():
        require(current == baseline["containers"][name], "container_drift")
        sha(current["id"])
        require(
            current["running"] is True
            and current["oom"] is False
            and current["restart_count"] == 0,
            "container_state",
        )
    return {"baseline_sha256": digest(baseline), "live_verified": False}
