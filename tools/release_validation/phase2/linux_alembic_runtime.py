"""Isolated Linux child executing the sealed original Alembic env, exactly 066→067."""

import os
import sys
from pathlib import Path

if __name__ == "__main__":
    # -I removes ambient Python paths. This directory was pinned by the parent.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sql_policy import (
    AFTER,
    LinuxCISpec,
    canonical,
    ci_config,
    need,
    parse,
    safe_failure,
    sha,
    validate_spec,
)


def verify_staged_files(spec, directory):
    validate_spec(spec)
    for prefix, section in (("app/", "source_members"), ("tools/", "tools")):
        expected = spec.data[section]
        actual = set()
        for path in (directory.root / prefix).rglob("*"):
            need(not path.is_symlink(), "staged_symlink")
            if path.is_file():
                actual.add(path.relative_to(directory.root / prefix).as_posix())
        need(actual == set(expected), "staged_file_set")
        for name, pin in expected.items():
            raw = directory.read(prefix + name)
            need({"bytes": len(raw), "sha256": sha(raw)} == pin, "staged_source_drift")
    need(
        directory.read("app/backend/alembic/env.py") == spec.artifact("env_py"),
        "original_env_binding",
    )


def run_original(spec, directory):
    from linux_transport import verify_ci_network
    from sql_migration_driver import SqlMigrationDriver, candidate_events

    verify_ci_network()
    verify_staged_files(spec, directory)
    need(Path.cwd() == directory.root / "app/backend", "original_env_cwd")
    need(
        not any(name == "app" or name.startswith("app.") for name in sys.modules),
        "product_preimported",
    )
    # The actual product env reads this bounded fixture config. No real config is read.
    os.environ.clear()
    os.environ.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            **ci_config(),
        }
    )
    sys.path.insert(0, str(directory.root / "app/backend"))
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    driver = SqlMigrationDriver(
        spec, lambda proof: directory.create("connected.json", canonical(proof))
    )
    failure = None
    try:
        with candidate_events(driver, event, Engine):
            config = Config(str(directory.root / "app/backend/alembic.ini"))
            config.set_main_option(
                "script_location", str(directory.root / "app/backend/alembic")
            )
            command.upgrade(config, AFTER)
        proof = driver.result()
    except BaseException as exc:
        failure = safe_failure(exc)
        proof = driver.proof
    # A failure cannot establish post-rollback head from a local callback. The
    # parent independently inspects it; absent result/identity remains HOLD.
    result = {
        "policy_sha256": spec.sha256,
        "identity": proof["identity"],
        "proof": proof,
        "post_head": proof["post_head"],
        "failure": failure,
    }
    directory.create("result.json", canonical(result))
    return 0 if failure is None else 1


def main():
    from linux_transport import PrivateDirectory

    need(len(sys.argv) == 2, "closed_runtime_arguments")
    directory = PrivateDirectory(sys.argv[1])
    try:
        packed = parse(directory.read("policy.json", 1024**2))
        need(set(packed) == {"data", "artifacts"}, "staged_policy_fields")
        spec = validate_spec(
            LinuxCISpec(
                canonical(packed["data"]),
                tuple(sorted((k, v.encode()) for k, v in packed["artifacts"].items())),
            )
        )
        need(spec.data["nonce"] == sys.argv[1], "runtime_nonce")
        return run_original(spec, directory)
    finally:
        directory.close()


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:
        code = 1
    raise SystemExit(code)
