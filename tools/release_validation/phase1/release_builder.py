"""Phase-one public release candidate packer. No builds, network or deployment."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import stat
import tarfile
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ORIGIN = "https://47.110.76.9"
BEFORE = "066_platform_metric_setting"
AFTER = "067_retro_completion"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 10000
MAINTENANCE = {
    "backup-database-daily": "app.tasks.backup_tasks.backup_database",
    "cleanup-expired-backups-daily": "app.tasks.backup_tasks.cleanup_expired_backups",
    "archive-audit-logs-monthly": "app.tasks.cleanup_tasks.archive_audit_logs",
}
CONTROL = ("runtime_guard.py", "schedule_pickle_guard.py", "start_067.py", "migrate_067.py")
TEMPLATES = ("Dockerfile.backend.offline.in", "Dockerfile.frontend.offline.in")
BASE_IDS = {
    "python": "sha256:2b223ad02f09c7a4c8ea661e9f5e039502427091e1981908bafb3a4db22e6e7e",
    "nginx": "sha256:bd3cc628e59c8865f88a3bbe99c1029c12a9983b2cb88a9b03913f43d4f5aa79",
}


class Rejected(ValueError):
    """Only static error codes may leave the command boundary."""


def require(ok, code):
    if not ok:
        raise Rejected(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def json_bytes(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "duplicate_json_key")
            out[key] = value
        return out

    return json.loads(raw, object_pairs_hook=pairs)


def keys(value, expected):
    require(type(value) is dict and set(value) == set(expected), "unexpected_fields")


# Phase2 integration delta (2026-10-23): exact public source paths only. This is
# not a directory exception; content, Git-mode and canonical-path guards remain.
CREDENTIAL_CODE_PATHS = frozenset({
    "backend/app/modules/credential/__init__.py",
    "backend/app/modules/credential/api.py",
    "backend/app/modules/credential/config.py",
    "backend/app/modules/credential/deps.py",
    "backend/app/modules/credential/enums.py",
    "backend/app/modules/credential/exceptions.py",
    "backend/app/modules/credential/models.py",
    "backend/app/modules/credential/permissions.py",
    "backend/app/modules/credential/repository.py",
    "backend/app/modules/credential/schemas.py",
    "backend/app/modules/credential/service.py",
})


def safe_name(name):
    require(
        type(name) is str and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", name)),
        "unsafe_path",
    )
    parts = PurePosixPath(name).parts
    require(
        parts and all(p not in (".", "..") and not p.startswith(".") for p in parts), "unsafe_path"
    )
    require(name == "/".join(parts), "noncanonical_path")
    lower = name.lower()
    require(
        not any(
            p.lower() in ("node_modules", "__pycache__", "secrets", "credentials") for p in parts
        ),
        "private_path",
    )
    require(
        not any(
            t in lower for t in (".local.", "deploy-secrets", "runtime-config", "github-config")
        ),
        "private_path",
    )
    require(
        name in CREDENTIAL_CODE_PATHS or not any(
            re.fullmatch(
                r"(?:secrets?|credentials?|cloud[-_](?:access|sync|secrets)|github[-_]config)(?:[._-].*)?",
                p.lower(),
            )
            for p in parts
        ),
        "private_path",
    )
    require(
        PurePosixPath(lower).suffix
        not in (".pem", ".key", ".dump", ".sqlite", ".db", ".map", ".env"),
        "private_path",
    )
    return name


def public_bytes(data):
    require(len(data) <= MAX_FILE, "file_too_large")
    # References to configuration variable names in application code are allowed.
    # Actual private keys / common credential literals are not public artifacts.
    require(
        not re.search(
            rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\bAKIA[A-Z0-9]{16}\b",
            data,
        ),
        "credential_literal",
    )


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    )
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        env=env,
        timeout=30,
    )
    require(result.returncode == 0, "git_command_failed")
    return result.stdout


def source_allowed(name):
    return (
        name in ("backend/alembic.ini", "backend/requirements.txt")
        or name.startswith("backend/app/")
        and PurePosixPath(name).suffix in (".py", ".json", ".sql")
        or name.startswith("backend/alembic/")
        and PurePosixPath(name).suffix in (".py", ".mako")
    )


def source_files(repo, commit, tree):
    require(bool(HEX40.fullmatch(commit)) and bool(HEX40.fullmatch(tree)), "unfrozen_git_identity")
    require(git(repo, "cat-file", "-t", commit).strip() == b"commit", "not_commit")
    require(git(repo, "rev-parse", commit + "^{tree}").decode().strip() == tree, "tree_mismatch")
    files, total = {}, 0
    for row in git(repo, "ls-tree", "-r", "-z", "--long", commit).split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        name = path.decode("utf-8")
        if not source_allowed(name) and name != "frontend/package-lock.json":
            continue
        safe_name(name)
        mode, kind, oid, size = header.split()
        require(mode in (b"100644", b"100755") and kind == b"blob", "git_link_or_nonfile")
        require(int(size) <= MAX_FILE, "file_too_large")
        total += int(size)
        require(total <= MAX_TOTAL and len(files) < MAX_FILES, "source_limit")
        data = git(repo, "cat-file", "blob", oid.decode("ascii"))
        require(len(data) == int(size), "git_size_mismatch")
        public_bytes(data)
        files[name] = data
    for name in (
        "backend/app/main.py",
        "backend/app/core/celery_app.py",
        "backend/alembic/env.py",
        "backend/alembic.ini",
        "backend/requirements.txt",
        "frontend/package-lock.json",
    ):
        require(name in files, "required_source_missing")
    return files


def disk_files(root, expected):
    root = Path(root)
    no_link_chain(root)
    require(root.is_dir() and not root.is_symlink() and not root.is_junction(), "unsafe_root")
    base = root.resolve()
    found = {}

    def visit(folder):
        for path in sorted(folder.iterdir()):
            require(not path.is_symlink() and not path.is_junction(), "disk_link")
            name = safe_name(path.relative_to(root).as_posix())
            require(path.resolve().is_relative_to(base), "outside_root")
            if path.is_dir():
                visit(path)
            else:
                require(path.is_file() and name in expected, "unexpected_disk_file")
                raw = read_local(path)
                pin = expected[name]
                require(
                    len(raw) == pin["bytes"] and digest(raw) == pin["sha256"], "disk_pin_mismatch"
                )
                public_bytes(raw)
                found[name] = raw

    visit(root)
    require(set(found) == set(expected), "missing_disk_file")
    return found


def no_link_chain(path):
    for current in (path, *path.parents):
        require(not current.is_symlink() and not current.is_junction(), "path_ancestor_link")


def read_local(path):
    no_link_chain(path)
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= MAX_FILE,
        "local_file",
    )
    with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as handle:
        after = os.fstat(handle.fileno())
        require(
            (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
            and stat.S_ISREG(after.st_mode)
            and after.st_nlink == 1
            and after.st_size <= MAX_FILE,
            "local_file_changed",
        )
        raw = handle.read(MAX_FILE + 1)
    require(len(raw) <= MAX_FILE, "file_too_large")
    return raw


def validate_file_pins(value):
    require(type(value) is dict and 0 < len(value) <= MAX_FILES, "missing_file_pins")
    require(len({n.casefold() for n in value}) == len(value), "case_collision")
    for name, pin in value.items():
        safe_name(name)
        keys(pin, ("bytes", "sha256"))
        require(type(pin["bytes"]) is int and 0 < pin["bytes"] <= MAX_FILE, "file_size_pin")
        require(
            type(pin["sha256"]) is str and bool(HEX64.fullmatch(pin["sha256"])), "file_hash_pin"
        )
    require(sum(p["bytes"] for p in value.values()) <= MAX_TOTAL, "pin_size_limit")


def validate_pins(pins):
    keys(
        pins,
        (
            "schema_version",
            "source_commit",
            "source_tree",
            "from_revision",
            "target_revision",
            "origin",
            "base_images",
            "backend_requirements_sha256",
            "migration_sha256",
            "dependencies",
            "frontend",
            "controls",
        ),
    )
    require(pins["schema_version"] == 1 and type(pins["schema_version"]) is int, "schema_version")
    require(
        all(
            type(pins[k]) is str and bool(HEX40.fullmatch(pins[k]))
            for k in ("source_commit", "source_tree")
        ),
        "unfrozen_git_identity",
    )
    require(
        (pins["from_revision"], pins["target_revision"], pins["origin"]) == (BEFORE, AFTER, ORIGIN),
        "release_target",
    )
    for name in ("backend_requirements_sha256", "migration_sha256"):
        require(type(pins[name]) is str and bool(HEX64.fullmatch(pins[name])), "unfrozen_hash")
    keys(pins["base_images"], ("python", "nginx"))
    for name in ("python", "nginx"):
        value = pins["base_images"][name]
        keys(value, ("local_tag", "image_id"))
        require(value["local_tag"] == "clothinkai-trial-" + name + "-base:20261018", "base_tag")
        require(value["image_id"] == BASE_IDS[name], "base_image_pin")
    dep = pins["dependencies"]
    keys(dep, ("source_requirements_sha256", "closure_reviewed", "files"))
    require(
        dep["source_requirements_sha256"] == pins["backend_requirements_sha256"]
        and dep["closure_reviewed"] is True,
        "dependency_review_missing",
    )
    validate_file_pins(dep["files"])
    require(
        "requirements-linux.lock" in dep["files"]
        and all(
            n == "requirements-linux.lock" or n.startswith("wheelhouse/") and n.endswith(".whl")
            for n in dep["files"]
        ),
        "dependency_allowlist",
    )
    front = pins["frontend"]
    keys(
        front,
        (
            "source_commit",
            "package_lock_sha256",
            "node_version",
            "npm_version",
            "api_origin",
            "files",
        ),
    )
    require(
        front["source_commit"] == pins["source_commit"] and front["api_origin"] == ORIGIN,
        "dist_source_mismatch",
    )
    require(
        type(front["package_lock_sha256"]) is str
        and bool(HEX64.fullmatch(front["package_lock_sha256"])),
        "frontend_lock_pin",
    )
    require(
        all(
            type(front[k]) is str and bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", front[k]))
            for k in ("node_version", "npm_version")
        ),
        "frontend_tool_versions",
    )
    validate_file_pins(front["files"])
    require(
        "index.html" in front["files"]
        and all(
            PurePosixPath(n).suffix
            in (".html", ".js", ".css", ".svg", ".png", ".ico", ".jpg", ".webp", ".woff", ".woff2")
            for n in front["files"]
        ),
        "dist_allowlist",
    )
    validate_file_pins(pins["controls"])
    require(set(pins["controls"]) == set(CONTROL + TEMPLATES), "control_allowlist")


def migrations(files):
    graph, names = {}, {}
    for name, raw in files.items():
        if (
            not name.startswith("backend/alembic/versions/")
            or not name.endswith(".py")
            or name.endswith("/__init__.py")
        ):
            continue
        values = {}
        for node in ast.parse(raw).body:
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            for target in targets:
                if isinstance(target, ast.Name) and target.id in ("revision", "down_revision"):
                    values[target.id] = ast.literal_eval(node.value)
        require(set(values) == {"revision", "down_revision"}, "migration_metadata")
        rev, down = values["revision"], values["down_revision"]
        require(
            type(rev) is str and (down is None or type(down) is str) and rev not in graph,
            "migration_graph",
        )
        graph[rev], names[rev] = down, name
    require(AFTER in graph and graph[AFTER] == BEFORE, "migration_target_parent")
    require(
        all(down is None or down in graph for down in graph.values()), "migration_parent_missing"
    )
    require(
        set(graph) - {down for down in graph.values() if down is not None} == {AFTER},
        "migration_heads",
    )
    seen, cursor = set(), AFTER
    while cursor is not None:
        require(cursor not in seen, "migration_cycle")
        seen.add(cursor)
        cursor = graph[cursor]
    require(seen == set(graph), "disconnected_migrations")
    return names[AFTER]


def beat_tasks(raw):
    result = None
    for node in ast.walk(ast.parse(raw)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Attribute) and t.attr == "beat_schedule" for t in node.targets
        ):
            require(result is None and isinstance(node.value, ast.Dict), "schedule_shape")
            result = {}
            for key, value in zip(node.value.keys, node.value.values):
                name = ast.literal_eval(key)
                require(
                    type(name) is str and isinstance(value, ast.Dict) and name not in result,
                    "schedule_entry",
                )
                tasks = [
                    ast.literal_eval(v)
                    for k, v in zip(value.keys, value.values)
                    if isinstance(k, ast.Constant) and k.value == "task"
                ]
                require(len(tasks) == 1 and type(tasks[0]) is str, "schedule_task")
                result[name] = tasks[0]
    require(
        result is not None and all(result.get(k) == v for k, v in MAINTENANCE.items()),
        "maintenance_schedule_changed",
    )
    result = {k: v for k, v in result.items() if k not in MAINTENANCE}
    require(len(result) == 7, "business_schedule_changed")
    return result


def validate_html_assets(files):
    class References(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if (tag in ("script", "img") and name == "src") or (
                    tag == "link" and name == "href"
                ):
                    require(value is not None, "html_asset_reference")
                    url = urlsplit(value)
                    require(
                        not url.scheme and not url.netloc and not url.query and not url.fragment,
                        "external_html_asset",
                    )
                    target = safe_name(url.path.lstrip("/"))
                    require(target in files, "missing_html_asset")

    References().feed(files["index.html"].decode("utf-8"))


def build(repo, commit, pins, dist, dependencies, output):
    validate_pins(pins)
    require(commit == pins["source_commit"], "commit_argument_mismatch")
    files = source_files(repo, commit, pins["source_tree"])
    require(
        digest(files["backend/requirements.txt"]) == pins["backend_requirements_sha256"],
        "requirements_mismatch",
    )
    require(
        digest(files.pop("frontend/package-lock.json")) == pins["frontend"]["package_lock_sha256"],
        "frontend_lock_mismatch",
    )
    migration_path = migrations(files)
    require(digest(files[migration_path]) == pins["migration_sha256"], "migration_hash_mismatch")
    schedule = beat_tasks(files["backend/app/core/celery_app.py"])
    front, dep = (
        disk_files(dist, pins["frontend"]["files"]),
        disk_files(dependencies, pins["dependencies"]["files"]),
    )
    validate_html_assets(front)
    lock = dep["requirements-linux.lock"].decode("utf-8")
    lines = [
        line.strip()
        for line in lock.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    require(
        lines
        and all(
            re.fullmatch(
                r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+(?: --hash=sha256:[0-9a-f]{64})+", line
            )
            for line in lines
        ),
        "offline_lock_syntax",
    )
    require(
        set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock))
        == {digest(raw) for name, raw in dep.items() if name.endswith(".whl")},
        "wheel_hash_closure",
    )
    text_assets = b"\n".join(
        raw for name, raw in front.items() if PurePosixPath(name).suffix in (".html", ".js", ".css")
    )
    require(
        ORIGIN.encode() in text_assets
        and not re.search(rb"localhost|127\.0\.0\.1|trial-web|zeabur\.app", text_assets, re.I),
        "frontend_origin_mismatch",
    )
    for name, raw in front.items():
        files["frontend-dist/" + name] = raw
    files.update(dep)
    checked_control = {}
    for name in CONTROL + TEMPLATES:
        raw = read_local(HERE / name)
        pin = pins["controls"][name]
        require(len(raw) == pin["bytes"] and digest(raw) == pin["sha256"], "control_pin_mismatch")
        checked_control[name] = raw
        if name in CONTROL:
            files["control/" + name] = raw
    for kind in ("backend", "frontend"):
        raw = checked_control["Dockerfile." + kind + ".offline.in"]
        require(raw.count(b"@SOURCE_COMMIT@") == 1, "docker_template")
        files["control/Dockerfile." + kind + ".offline"] = raw.replace(
            b"@SOURCE_COMMIT@", commit.encode()
        )
    files["control/beat-expected.json"] = canonical(schedule)
    files["release.json"] = canonical(
        {
            "source_commit": commit,
            "source_tree": pins["source_tree"],
            "schema": AFTER,
            "origin": ORIGIN,
            "phase": "candidate-only",
        }
    )
    require(len(files) <= MAX_FILES, "bundle_file_limit")
    require(sum(map(len, files.values())) <= MAX_TOTAL, "bundle_size_limit")
    manifest = {
        "schema_version": 1,
        "artifact_kind": "flow78-phase1-candidate",
        "deployable": False,
        "missing_deployment_gate": "independently-reviewed-updater-and-user-approval",
        "pins": pins,
        "compatibility": {
            "legacy_root": "/srv/clothinkai/production-20261018",
            "preserve_containers": [
                "clothinkai-prod-" + n + "-20261018"
                for n in ("db", "redis", "api", "web", "worker", "beat")
            ],
            "preserve_certbot_metadata": True,
            "preserve_backup_renew": True,
            "implicit_migration": False,
        },
        "files": {
            name: {"bytes": len(raw), "sha256": digest(raw)} for name, raw in sorted(files.items())
        },
    }
    raw_manifest = canonical(manifest)
    output = Path(output)
    no_link_chain(output.parent)
    require(
        output.parent.is_dir()
        and not output.parent.is_symlink()
        and not output.parent.is_junction(),
        "unsafe_output_parent",
    )
    require(not output.exists() and not output.is_symlink(), "output_exists")
    # Exclusive create preserves a partial artifact on interruption; it is never a release marker.
    with output.open("xb") as handle, gzip.GzipFile(
        filename="", mode="wb", fileobj=handle, mtime=0
    ) as gz, tarfile.open(fileobj=gz, mode="w|") as archive:
        for name, raw in [("release-manifest.json", raw_manifest), *sorted(files.items())]:
            safe_name(name)
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            archive.addfile(info, io.BytesIO(raw))
    with tarfile.open(output, "r:gz") as archive:
        require(archive.getnames() == ["release-manifest.json", *sorted(files)], "archive_members")
        for name, raw in [("release-manifest.json", raw_manifest), *files.items()]:
            require(archive.extractfile(name).read() == raw, "archive_readback")
    return {
        "status": "PHASE1_CANDIDATE_VERIFIED",
        "deployable": False,
        "source_commit": commit,
        "bytes": output.stat().st_size,
        "sha256": digest(output.read_bytes()),
        "manifest_sha256": digest(raw_manifest),
        "files": len(files),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repo", "commit", "pins", "dist", "dependencies", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        pins = json_bytes(Path(args.pins).read_bytes())
        print(
            json.dumps(
                build(args.repo, args.commit, pins, args.dist, args.dependencies, args.output)
            )
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "CANDIDATE_REJECTED",
                    "code": str(exc) if isinstance(exc, Rejected) else type(exc).__name__,
                }
            )
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
