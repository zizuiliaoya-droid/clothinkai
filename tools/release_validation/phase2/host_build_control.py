"""Fixed offline host build and readback; no source checkout, install or pull."""

import os
import stat
from pathlib import Path

from contracts import AFTER, canonical, digest, exact, require
from fixed_host_transport import DOCKER, ROOT, _capture
from host_release_input import HostReleaseSpec, require_step, validate_release
from sql_policy import parse, sha

IMAGE_FORMAT = (
    '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},'
    '"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
    '"schema":{{json (index .Config.Labels "clothinkai.release.schema")}},'
    '"origin":{{json (index .Config.Labels "clothinkai.production.origin")}}}'
)
BACKEND_PROBE = """import ast,hashlib,json,pathlib
import alembic,asyncpg,fastapi,sqlalchemy
root=pathlib.Path('/app'); files={}
for base in ('app','alembic'):
 for path in sorted((root/base).rglob('*')):
  if path.is_file() and '__pycache__' not in path.parts:
   raw=path.read_bytes();files[path.relative_to(root).as_posix()]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
raw=(root/'alembic.ini').read_bytes();files['alembic.ini']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
nodes={}
for path in sorted((root/'alembic/versions').glob('*.py')):
 values={}
 for node in ast.parse(path.read_bytes()).body:
  if isinstance(node,ast.Assign):
   for target in node.targets:
    if isinstance(target,ast.Name) and target.id in ('revision','down_revision'):values[target.id]=ast.literal_eval(node.value)
  elif isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id in ('revision','down_revision'):values[node.target.id]=ast.literal_eval(node.value)
 if 'revision' in values:nodes[values['revision']]=values['down_revision']
heads=sorted(set(nodes)-set(v for v in nodes.values() if v is not None))
print(json.dumps({'files':files,'heads':heads,'imports':True},sort_keys=True))
"""


def _release_fd(journal):
    fd = os.open(
        "..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=journal.directory.fd
    )
    info = os.fstat(fd)
    require(info.st_uid == 0 and not info.st_mode & 0o022, "release_directory_owner")
    return fd


def _write_tree(parent, name, files):
    """Exclusive new subtree; partial failure is retained and never retried."""
    os.mkdir(name, 0o700, dir_fd=parent)
    os.fsync(parent)
    root = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        for path, raw in sorted(files.items()):
            fd = os.dup(root)
            try:
                parts = path.split("/")
                require(
                    all(p not in ("", ".", "..") for p in parts), "build_relative_path"
                )
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, 0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                    child = os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                    )
                    info = os.fstat(child)
                    require(
                        info.st_uid == os.geteuid()
                        and stat.S_IMODE(info.st_mode) == 0o700,
                        "build_directory_owner",
                    )
                    os.close(fd)
                    fd = child
                output = os.open(
                    parts[-1],
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=fd,
                )
                with os.fdopen(output, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fsync(fd)
            finally:
                os.close(fd)
        os.fsync(root)
    finally:
        os.close(root)


async def prepare(spec, journal):
    require_step(spec, journal, "PREPARE")
    validate_release(spec)
    require(
        journal.root == Path(ROOT) / "releases" / spec.data["release_id"] / "state",
        "prepare_release_root",
    )
    parent = _release_fd(journal)
    try:
        _write_tree(parent, "build", dict(spec.package._members))
        _write_tree(parent, "tools", dict(spec._tools))
    finally:
        os.close(parent)
    facts = {
        "mode": "host_observed",
        "package_evidence_sha256": spec.package.sha256,
        "build_members_sha256": digest(
            {n: {"bytes": len(b), "sha256": sha(b)} for n, b in spec.package._members}
        ),
        "tools_sha256": parse(spec._binding)["tools_sha256"],
    }
    journal.directory.create("prepare-observed.json", canonical(facts))
    return facts


def verify_build_tree(spec, journal):
    from linux_transport import PrivateDirectory

    parent = _release_fd(journal)
    directory = object.__new__(PrivateDirectory)
    try:
        directory.fd = os.open(
            "build", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
    finally:
        os.close(parent)
    try:
        actual = set()
        root = journal.root.parent / "build"
        for path in root.rglob("*"):
            require(not path.is_symlink(), "build_context_link")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
        require(actual == set(dict(spec.package._members)), "build_context_set")
        for name, raw in spec.package._members:
            require(
                directory.read(name, max(len(raw), 1)) == raw, "build_context_bytes"
            )
    finally:
        directory.close()


async def _image(subject):
    row = parse(
        await _capture(DOCKER + ("image", "inspect", "--format", IMAGE_FORMAT, subject))
    )
    exact(row, ("id", "os", "architecture", "revision", "schema", "origin"))
    require(
        type(row["id"]) is str and row["id"].startswith("sha256:"), "build_image_id"
    )
    from contracts import sha as require_sha

    require_sha(row["id"][7:])
    require(row["os"] == "linux" and row["architecture"] == "amd64", "build_platform")
    return row


async def build(spec, journal, kind):
    require(kind in ("backend", "frontend"), "build_kind")
    require_step(spec, journal, "BUILD_" + kind.upper())
    require(type(spec) is HostReleaseSpec, "build_release_input")
    validate_release(spec)
    verify_build_tree(spec, journal)
    base = spec.package.get("base_images")["python" if kind == "backend" else "nginx"]
    require(
        (await _image(base["local_tag"]))["id"] == base["image_id"], "base_tag_drift"
    )
    tag = "clothinkai-flow78-" + kind + ":" + spec.data["source_commit"][:12]
    present = await _capture(
        DOCKER
        + ("image", "ls", "--no-trunc", "--quiet", "--filter", "reference=" + tag)
    )
    require(present.strip() == b"", "build_tag_exists")
    base_assets = (
        await _frontend_files(base["image_id"]) if kind == "frontend" else None
    )
    from build_adapter import plan

    recipe = plan(
        kind, spec.data["source_commit"], base["image_id"], base["image_id"], True
    )
    journal.directory.create(
        "build-" + kind + "-intent.json",
        canonical({"tag": tag, "base": base, "context": spec.package.sha256}),
    )
    await _capture(DOCKER + recipe["argv"][1:], timeout=recipe["timeout_seconds"])
    row = await _image(tag)
    require(row["revision"] == spec.data["source_commit"], "build_revision")
    require(
        row["schema"] == AFTER
        if kind == "backend"
        else row["origin"] == "https://47.110.76.9",
        "build_label",
    )
    # These isolated readbacks have no production config, mounts or network.
    common = DOCKER + (
        "run",
        "--rm",
        "--pull=never",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges=true",
        "--memory=512m",
        "--cpus=1",
        "--entrypoint",
        "python",
        row["id"],
    )
    if kind == "backend":
        await _capture(common + ("-m", "pip", "check"), timeout=60)
        probe = parse(await _capture(common + ("-B", "-c", BACKEND_PROBE), timeout=60))
        exact(probe, ("files", "heads", "imports"))
        expected = {
            n.removeprefix("backend/"): p
            for n, p in spec.package.get("source_members").items()
            if n.startswith(("backend/app/", "backend/alembic/"))
            or n == "backend/alembic.ini"
        }
        require(
            probe["files"] == expected
            and probe["heads"] == [AFTER]
            and probe["imports"] is True,
            "backend_probe",
        )
    else:
        # Preserve explicitly observed base assets such as nginx's 50x.html.
        probe = await _frontend_files(row["id"])
        expected = {
            **base_assets,
            **{
                n.removeprefix("frontend-dist/"): sha(b)
                for n, b in spec.package._members
                if n.startswith("frontend-dist/")
            },
        }
        require(probe == expected, "frontend_image_files")
    facts = {
        "mode": "host_observed",
        "image": row,
        "source_commit": spec.data["source_commit"],
        "probe_sha256": digest(probe),
    }
    journal.directory.create("build-" + kind + "-observed.json", canonical(facts))
    return facts


async def _frontend_files(image):
    # Fixed command prints only checksums; BusyBox is in the frozen nginx image.
    script = "test $(id -un) = nginx && cd /usr/share/nginx/html && find . -type f -exec sha256sum '{}' ';'"
    raw = await _capture(
        DOCKER
        + (
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            "--memory=128m",
            "--cpus=1",
            "--user",
            "nginx",
            "--entrypoint",
            "/bin/sh",
            image,
            "-c",
            script,
        ),
        timeout=60,
    )
    observed = {}
    for line in raw.decode("ascii").splitlines():
        hashed, name = line.split("  ./", 1)
        require(name not in observed, "frontend_duplicate")
        observed[name] = hashed
    return observed
