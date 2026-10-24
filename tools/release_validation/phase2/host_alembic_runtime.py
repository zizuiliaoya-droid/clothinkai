"""Fixed container child for the original product Alembic env; no CLI paths/SQL."""

import os
import stat
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, "/opt/update")

from contracts import require
from contracts import sha as require_sha
from fixed_host_transport import ROOT
from linux_transport import PrivateDirectory, require_linux
from sql_policy import (
    HostMigrationSpec,
    canonical,
    parse,
    require_host_authority,
    safe_failure,
    sha,
    validate_spec,
)


class OutputDirectory(PrivateDirectory):
    def __init__(self, commit):
        require_sha(commit, 40)
        require_linux()
        self.root = (
            Path(ROOT) / "releases" / ("flow78-" + commit[:12]) / "migration-output"
        )
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in self.root.parts[1:]:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                require(
                    info.st_uid == 0 and not info.st_mode & 0o022,
                    "migration_output_owner",
                )
            require(
                stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "migration_output_private"
            )
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise


def original_source(spec):
    env = Path("/app/alembic/env.py")
    require(
        not env.is_symlink() and env.read_bytes() == spec.artifact("env_py"),
        "migration_env_bytes",
    )
    expected = {
        node["sha256"] for node in parse(spec.artifact("migration_tree"))["nodes"]
    }
    actual = set()
    for path in Path("/app/alembic/versions").glob("*.py"):
        require(not path.is_symlink(), "migration_version_link")
        if path.name != "__init__.py":
            actual.add(sha(path.read_bytes()))
    require(actual == expected, "migration_version_bytes")


def run_original(spec, output):
    from sql_migration_driver import SqlMigrationDriver, candidate_events

    require_host_authority(spec)
    original_source(spec)
    require(Path.cwd() == Path("/app"), "host_alembic_cwd")
    require(
        not any(n == "app" or n.startswith("app.") for n in sys.modules),
        "product_preimported",
    )
    os.environ.clear()
    os.environ.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            **parse(spec.artifact("config")),
        }
    )
    sys.path.insert(0, "/app")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    driver = SqlMigrationDriver(
        spec,
        lambda proof: output.create("connected.json", canonical(proof)),
        lambda diagnostic: output.create("advisory-busy.json", canonical(diagnostic)),
    )
    failure = None
    try:
        with candidate_events(driver, event, Engine):
            config = Config("/app/alembic.ini")
            config.set_main_option("script_location", "/app/alembic")
            command.upgrade(config, spec.data["target_revision"])
        proof = driver.result()
    except BaseException as exc:
        failure, proof = safe_failure(exc), driver.proof
    output.create(
        "result.json",
        canonical(
            {
                "policy_sha256": spec.sha256,
                "identity": proof["identity"],
                "proof": proof,
                "post_head": proof["post_head"],
                "failure": failure,
            }
        ),
    )
    return 0 if failure is None else 1


def main():
    from host_sequence import HostDirectory

    require(len(sys.argv) == 2, "host_runtime_arguments")
    commit = sys.argv[1]
    require_sha(commit, 40)
    state = HostDirectory(commit)
    try:
        packed = parse(state.read("migration-policy.json"))
    finally:
        state.close()
    require(
        set(packed) == {"data", "binding", "authority", "artifacts"},
        "host_runtime_policy_fields",
    )
    spec = validate_spec(
        HostMigrationSpec(
            canonical(packed["data"]),
            tuple(
                sorted((n, b.encode("utf-8")) for n, b in packed["artifacts"].items())
            ),
            canonical(packed["binding"]),
            canonical(packed["authority"]),
        )
    )
    require(spec.data["source_commit"] == commit, "host_runtime_source")
    output = OutputDirectory(commit)
    try:
        return run_original(spec, output)
    finally:
        output.close()


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        code = 1
    raise SystemExit(code)
