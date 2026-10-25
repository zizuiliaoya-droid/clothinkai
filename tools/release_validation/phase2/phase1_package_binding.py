"""Original phase1 contract plus one pinned schedule recognizer correction; no build/install."""

import ast
import hashlib
import io
import json
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

PHASE1_PINS = "0cc34f4847dce14a3ffc7868f20b37a32500144a5895563228b9d84a66bbc0c0"
BUILDER_SHA = "d728bdd2ce2ce93559ded9a9a13fe60bf94748935c7119cf02694e6a0cd9783f"
PACKAGE_PINS_FILE = "implementation-pins-origin-1024.json"
SCHEDULE_GUARD_BEFORE = {
    "bytes": 11040,
    "sha256": "e7c9692981ee8a64afc0bfce22273f96a406f36b3c8bfa13c0fc16eee0930dbc",
}
SCHEDULE_GUARD_AFTER = {
    "bytes": 12557,
    "sha256": "c7c5a9d619e8d32dd114004d79d197af47ee029e63d6404365027a81cb612ad9",
}


class PackageRejected(ValueError):
    pass


def need(value, code):
    if not value:
        raise PackageRejected(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def schedule_guard_pin(name, original):
    """Require the historical origin, then bind only the corrected guard bytes."""
    if name != "schedule_pickle_guard.py":
        return original
    need(original == SCHEDULE_GUARD_BEFORE, "schedule_guard_origin")
    return dict(SCHEDULE_GUARD_AFTER)


def tool_bundle():
    """Include every phase2 root module and template; prove local import closure.

    Returned bytes are public source only. This creates no executable archive and
    is not an independent release freeze. Callers must pin the resulting manifest.
    """
    root = Path(__file__).resolve().parent
    paths = sorted(root.glob("*.py")) + sorted((root / "templates").glob("*"))
    paths += sorted(root.glob("*.sql"))
    result = {}
    modules = {p.stem for p in root.glob("*.py")}
    for path in paths:
        need(
            path.is_file()
            and not path.is_symlink()
            and not path.is_junction()
            and path.stat().st_nlink == 1,
            "tool_file",
        )
        raw = path.read_bytes()
        name = path.relative_to(root).as_posix()
        if path.suffix == ".py":
            for node in ast.walk(ast.parse(raw)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    local = node.module.split(".")[0]
                    if local in modules:
                        need((root / (local + ".py")).is_file(), "tool_import_missing")
        result[name] = raw
    builder = frozen_contract()
    raw_pins = (builder.HERE / PACKAGE_PINS_FILE).read_bytes()
    for name, pin in builder.json_bytes(raw_pins)["files"].items():
        path = builder.HERE / name
        need(
            not path.is_symlink()
            and not path.is_junction()
            and path.stat().st_nlink == 1,
            "frozen_file_link",
        )
        raw = path.read_bytes()
        need(
            schedule_guard_pin(name, pin) == {"bytes": len(raw), "sha256": digest(raw)},
            "frozen_file_drift",
        )
        result["frozen_phase1/" + name] = raw
    result["frozen_phase1/" + PACKAGE_PINS_FILE] = raw_pins
    # Retain the historical SQL-guard pinfile; only the schedule pin has an exact delta.
    result["frozen_phase1/implementation-pins.json"] = (
        builder.HERE / "implementation-pins.json"
    ).read_bytes()
    return result


def tool_manifest():
    return {
        name: {"bytes": len(raw), "sha256": digest(raw)}
        for name, raw in tool_bundle().items()
    }


def frozen_contract():
    candidates = (
        Path(__file__).resolve().parent / "frozen_phase1",
        Path(__file__).resolve().parent.parent / "release-flow78-1021",
        Path(__file__).resolve().parent.parent / "phase1",
    )
    root = next((p for p in candidates if p.is_dir()), None)
    need(root is not None, "phase1_contract_missing")
    for path in (root, *root.parents):
        need(not path.is_symlink() and not path.is_junction(), "phase1_link")
    raw_pins = (root / PACKAGE_PINS_FILE).read_bytes()
    need(digest(raw_pins) == PHASE1_PINS, "phase1_pins")
    raw = (root / "release_builder.py").read_bytes()
    need(digest(raw) == BUILDER_SHA, "phase1_builder")
    module = ModuleType("frozen_phase1_release_contract")
    module.__file__ = str(root / "release_builder.py")
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    pins = module.json_bytes(raw_pins)
    for name in module.CONTROL + module.TEMPLATES:
        path = root / name
        need(
            not path.is_symlink()
            and not path.is_junction()
            and path.stat().st_nlink == 1,
            "phase1_control_link",
        )
        content = path.read_bytes()
        need(
            schedule_guard_pin(name, pins["files"][name])
            == {"bytes": len(content), "sha256": digest(content)},
            "phase1_control_drift",
        )
    return module


@dataclass(frozen=True)
class BoundPackage:
    encoded: bytes = field(repr=False)
    _members: tuple = field(repr=False)

    def get(self, key, default=None):
        return json.loads(self.encoded).get(key, default)

    def member(self, name):
        return dict(self._members)[name]

    @property
    def sha256(self):
        return digest(self.encoded)


@dataclass(frozen=True)
class BoundCISource(BoundPackage):
    """Git-proven original source for CI; explicitly NOT a verified release package."""


def verify_ci_source(repository, commit, tree):
    builder = frozen_contract()
    source = builder.source_files(repository, commit, tree)
    builder.migrations(source)
    need(
        {"backend/alembic/env.py", "backend/alembic.ini", "backend/app/core/db.py"}
        <= source.keys(),
        "ci_original_source",
    )
    evidence = {
        "mode": "linux_ci_source_only",
        "semantic_verified": False,
        "source_verified": True,
        "deployable": False,
        "live_enabled": False,
        "source_commit": commit,
        "source_tree": tree,
        "archive_sha256": None,
        "source_members": {
            n: {"bytes": len(b), "sha256": digest(b)} for n, b in source.items()
        },
        "phase1_pins_sha256": PHASE1_PINS,
    }
    return BoundCISource(builder.canonical(evidence), tuple(sorted(source.items())))


def verify_package(archive_bytes, repository, freeze):
    """Use independently frozen identities and actual Git objects, never worktree bytes."""
    builder = frozen_contract()
    try:
        builder.keys(
            freeze,
            ("mode", "source_commit", "source_tree", "pins_sha256", "archive_sha256"),
        )
        need(freeze["mode"] in {"synthetic_fixture", "candidate_review"}, "freeze_mode")
        need(
            all(
                type(freeze[k]) is str and re.fullmatch("[0-9a-f]{40}", freeze[k])
                for k in ("source_commit", "source_tree")
            ),
            "source_not_frozen",
        )
        need(
            all(
                type(freeze[k]) is str and re.fullmatch("[0-9a-f]{64}", freeze[k])
                for k in ("pins_sha256", "archive_sha256")
            ),
            "artifact_not_frozen",
        )
        need(
            type(archive_bytes) is bytes
            and len(archive_bytes) <= 256 * 1024**2
            and digest(archive_bytes) == freeze["archive_sha256"],
            "archive_binding",
        )
        files, total = {}, 0
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            for member in archive:
                builder.safe_name(member.name)
                need(
                    member.isfile()
                    and member.name not in files
                    and member.size <= builder.MAX_FILE,
                    "archive_member",
                )
                total += member.size
                need(
                    total <= builder.MAX_TOTAL and len(files) < builder.MAX_FILES,
                    "archive_limits",
                )
                stream = archive.extractfile(member)
                need(stream is not None, "archive_stream")
                raw = stream.read(member.size + 1)
                need(len(raw) == member.size, "archive_truncated")
                builder.public_bytes(raw)
                files[member.name] = raw
        manifest = builder.json_bytes(files.pop("release-manifest.json"))
        builder.keys(
            manifest,
            (
                "schema_version",
                "artifact_kind",
                "deployable",
                "missing_deployment_gate",
                "pins",
                "compatibility",
                "files",
            ),
        )
        need(
            manifest["schema_version"] == 1
            and manifest["artifact_kind"] == "flow78-phase1-candidate"
            and manifest["deployable"] is False
            and manifest["missing_deployment_gate"]
            == "independently-reviewed-updater-and-user-approval",
            "manifest_contract",
        )
        pins = manifest["pins"]
        builder.validate_pins(pins)
        need(
            digest(builder.canonical(pins)) == freeze["pins_sha256"]
            and all(pins[k] == freeze[k] for k in ("source_commit", "source_tree")),
            "independent_pins_binding",
        )
        need(
            manifest["files"]
            == {n: {"bytes": len(b), "sha256": digest(b)} for n, b in files.items()},
            "member_pins",
        )
        compatibility = {
            "legacy_root": "/srv/clothinkai/production-20261018",
            "preserve_containers": [
                "clothinkai-prod-" + n + "-20261018"
                for n in ("db", "redis", "api", "web", "worker", "beat")
            ],
            "preserve_certbot_metadata": True,
            "preserve_backup_renew": True,
            "implicit_migration": False,
        }
        need(manifest["compatibility"] == compatibility, "compatibility_contract")
        source = builder.source_files(
            repository, freeze["source_commit"], freeze["source_tree"]
        )
        verified_source_lock = source.pop("frontend/package-lock.json")
        need(
            digest(verified_source_lock) == pins["frontend"]["package_lock_sha256"],
            "frontend_git_lock",
        )
        need(
            digest(source["backend/requirements.txt"])
            == pins["backend_requirements_sha256"],
            "backend_git_requirements",
        )
        migration = builder.migrations(source)
        need(
            digest(source[migration]) == pins["migration_sha256"],
            "migration_git_source",
        )
        expected = dict(source)
        for prefix, section in (("frontend-dist/", "frontend"), ("", "dependencies")):
            subset = {}
            for name, pin in pins[section]["files"].items():
                raw = files[prefix + name]
                need(
                    pin == {"bytes": len(raw), "sha256": digest(raw)},
                    "asset_or_dependency_pin",
                )
                subset[name] = raw
                expected[prefix + name] = raw
            if section == "frontend":
                builder.validate_html_assets(subset)
                builder.validate_frontend_origin(subset, verified_source_lock)
            else:
                lines = [
                    v.strip()
                    for v in subset["requirements-linux.lock"].decode().splitlines()
                    if v.strip() and not v.strip().startswith("#")
                ]
                need(
                    lines
                    and all(
                        re.fullmatch(
                            r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?: --hash=sha256:[0-9a-f]{64})+",
                            v,
                        )
                        for v in lines
                    ),
                    "offline_lock_syntax",
                )
                need(
                    set(re.findall(r"--hash=sha256:([0-9a-f]{64})", "\n".join(lines)))
                    == {digest(b) for n, b in subset.items() if n.endswith(".whl")},
                    "wheel_hash_closure",
                )
        for name in builder.CONTROL + builder.TEMPLATES:
            raw = (builder.HERE / name).read_bytes()
            need(
                pins["controls"][name] == {"bytes": len(raw), "sha256": digest(raw)},
                "frozen_control_pin",
            )
            if name in builder.CONTROL:
                expected["control/" + name] = raw
            else:
                need(raw.count(b"@SOURCE_COMMIT@") == 1, "docker_template")
                expected["control/" + name[:-3]] = raw.replace(
                    b"@SOURCE_COMMIT@", freeze["source_commit"].encode()
                )
        expected["control/beat-expected.json"] = builder.canonical(
            builder.beat_tasks(source["backend/app/core/celery_app.py"])
        )
        expected["release.json"] = builder.canonical(
            {
                "source_commit": freeze["source_commit"],
                "source_tree": freeze["source_tree"],
                "schema": builder.AFTER,
                "origin": builder.ORIGIN,
                "phase": "candidate-only",
            }
        )
        need(files == expected, "archive_differs_from_source_contract")
        evidence = {
            "semantic_verified": True,
            "mode": freeze["mode"],
            "deployable": False,
            "live_enabled": False,
            "source_commit": freeze["source_commit"],
            "source_tree": freeze["source_tree"],
            "archive_sha256": freeze["archive_sha256"],
            "pins_sha256": freeze["pins_sha256"],
            "source_members": {
                n: {"bytes": len(b), "sha256": digest(b)} for n, b in source.items()
            },
            "base_images": pins["base_images"],
            "phase1_pins_sha256": PHASE1_PINS,
        }
        return BoundPackage(
            builder.canonical(evidence), tuple(sorted(expected.items()))
        )
    except PackageRejected:
        raise
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError, UnicodeError):
        raise PackageRejected("package_contract_rejected") from None
