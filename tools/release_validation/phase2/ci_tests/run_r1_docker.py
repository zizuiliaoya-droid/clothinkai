"""R1 only: real TTL exec and private staging -> exact frontend recipe -> nginx."""

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_adapter import plan
from host_acceptance_control import TTL_OBSERVER
from host_build_control import _frontend_files, _write_tree

COMMIT = "55477c44f09bb1a25169bfb44ff6c90bc4f9cedb"
TREE = "28c0cac9a15cc48a5dfa5c7d3ed732cd6a59abdc"
VALIDATION = Path(__file__).resolve().parents[2]
RELEASE = {
    "source_commit": COMMIT,
    "source_tree": TREE,
    "schema": "067_retro_completion",
    "origin": "https://47.110.76.9",
    "phase": "candidate-only",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def docker(*args, check=True):
    result = subprocess.run(
        ("docker", *args), capture_output=True, timeout=120, check=False
    )
    if check and result.returncode:
        print(result.stderr[-8192:].decode("utf-8", "replace"), file=sys.stderr)
        result.check_returncode()
    return result


def image(variable):
    value = os.environ[variable]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", value)
    assert (
        docker("image", "inspect", "--format", "{{.Id}}", value).stdout.decode().strip()
        == value
    )
    return value


def private_tree(root, name, files):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _write_tree(fd, name, files)
    finally:
        os.close(fd)
    path = root / name
    for member in (path, *path.rglob("*")):
        assert member.stat().st_uid == 0
        assert stat.S_IMODE(member.stat().st_mode) == (
            0o700 if member.is_dir() else 0o600
        )
    return path


def identity(container, expected_image):
    row = json.loads(
        docker(
            "inspect",
            "--format",
            '{"id":{{json .Id}},"image":{{json .Image}},"network":{{json .HostConfig.NetworkMode}},"ports":{{json .HostConfig.PortBindings}}}',
            container,
        ).stdout
    )
    assert row["id"] == container and row["image"] == expected_image
    assert row["network"] == "none" and row["ports"] in (None, {})
    return row


def ttl(root, product, dependency_image, output):
    config = {
        "ENVIRONMENT": "production",
        "LOG_LEVEL": "WARNING",
        "DB_ECHO": "false",
        "DB_POOL_SIZE": "1",
        "DB_MAX_OVERFLOW": "0",
        "CORS_ALLOWED_ORIGINS": RELEASE["origin"],
        "CREDENTIAL_MASTER_KEY": base64.b64encode(b"c" * 32).decode(),
        "JWT_SECRET": "f" * 64,
        "R2_ENDPOINT_URL": "https://r2.invalid",
        "R2_ACCESS_KEY_ID": "synthetic",
        "R2_SECRET_ACCESS_KEY": "synthetic",
        "R2_BUCKET_PRIVATE": "synthetic",
    }
    for key, role, driver in (
        ("APP", "app", "asyncpg"),
        ("BYPASS", "bypass", "asyncpg"),
        ("SYNC", "bypass", "psycopg2"),
        ("ARCHIVER", "archiver", "asyncpg"),
    ):
        config["DATABASE_URL_" + key] = (
            f"postgresql+{driver}://clothing_{role}:synthetic@prod-db:5432/clothing_erp_prod_20261018"
        )
    for key, db in (("CACHE", 0), ("CELERY_BROKER", 1), ("CELERY_BACKEND", 2)):
        config["REDIS_URL_" + key] = f"redis://prod-redis:6379/{db}"
    raw_config = json.dumps(config).encode()
    files = {
        "production/runtime-config.json": raw_config,
        "app/release.json": json.dumps(RELEASE).encode(),
        "control/runtime_guard.py": (
            VALIDATION / "phase1/runtime_guard.py"
        ).read_bytes(),
    }
    for name in ("app/__init__.py", "app/core/__init__.py", "app/core/config.py"):
        files["app/" + name] = (product / "backend" / name).read_bytes()
    assert (
        sha(files["app/app/core/config.py"])
        == "dcd07725c9ae86a80542534cd6623231e886d56f68561b07a54041208e796d2f"
    )
    context = private_tree(root, "ttl", files)
    mounts = []
    for name, target in (
        ("app", "/app"),
        ("control", "/opt/release"),
        ("production", "/run/production"),
    ):
        mounts += ["--mount", f"type=bind,src={context / name},dst={target},readonly"]
    container = (
        docker(
            "run",
            "-d",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges=true",
            *mounts,
            "--entrypoint",
            "/usr/local/bin/python",
            dependency_image,
            "-I",
            "-B",
            "-c",
            "import time; time.sleep(180)",
        )
        .stdout.decode()
        .strip()
    )
    assert re.fullmatch(r"[0-9a-f]{64}", container)
    try:
        bound = identity(container, dependency_image)
        results = {}
        for name, commit, config_sha in (
            ("matched", COMMIT, sha(raw_config)),
            ("wrong_digest", COMMIT, "0" * 64),
            ("wrong_source", "0" * 40, sha(raw_config)),
        ):
            # This is the exact production observer; env -i proves it needs no PID1 environment.
            result = docker(
                "exec",
                "--user",
                "0:0",
                container,
                "/usr/bin/env",
                "-i",
                "/usr/local/bin/python",
                "-I",
                "-B",
                "-c",
                TTL_OBSERVER,
                commit,
                TREE,
                config_sha,
                check=False,
            )
            if name == "matched":
                assert result.returncode == 0 and not result.stderr
                assert json.loads(result.stdout) == {
                    "permission_cache_ttl_seconds": 300
                }
            else:
                assert (
                    result.returncode == 2 and not result.stdout and not result.stderr
                )
            results[name] = {
                "exit_code": result.returncode,
                "stdout": result.stdout.decode(),
            }
        (output / "r1-ttl-result.json").write_text(
            json.dumps(
                {
                    "scope": "R1-01",
                    "container": bound,
                    "observer_sha256": sha(TTL_OBSERVER.encode()),
                    "config_sha256": sha(raw_config),
                    "private_config_mode": "0600",
                    "empty_application_environment": True,
                    "results": results,
                    "database_or_business_actions": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        docker("rm", "-f", container)


async def frontend(root, nginx_image, output):
    public = {
        "index.html": b'<html><script src="/assets/app.js"></script>R1 static</html>',
        "assets/app.js": b"/* synthetic static asset */",
        "assets/nested/style.css": b"body { color: black; }",
    }
    recipe = (
        (VALIDATION / "phase1/Dockerfile.frontend.offline.in")
        .read_bytes()
        .replace(b"@SOURCE_COMMIT@", COMMIT.encode())
    )
    files = {"frontend-dist/" + n: raw for n, raw in public.items()}
    files.update(
        {
            "control/Dockerfile.frontend.offline": recipe,
            "release.json": json.dumps(RELEASE).encode(),
        }
    )
    context = private_tree(root, "frontend", files)
    base_tag = "clothinkai-trial-nginx-base:20261018"
    assert (
        docker(
            "image", "ls", "--quiet", "--filter", "reference=" + base_tag
        ).stdout.strip()
        == b""
    )
    docker("tag", nginx_image, base_tag)
    assert (
        docker("image", "inspect", "--format", "{{.Id}}", base_tag)
        .stdout.decode()
        .strip()
        == nginx_image
    )
    tag = "flow78-r1-frontend:" + secrets.token_hex(8)
    command = list(plan("frontend", COMMIT, nginx_image, nginx_image, True)["argv"][1:])
    command[command.index("-f") + 1] = str(
        context / "control/Dockerfile.frontend.offline"
    )
    command[command.index("-t") + 1] = tag
    command[-1] = str(context)
    built = docker(*command, check=False)
    (output / "r1-frontend-build.log").write_bytes(built.stdout + built.stderr)
    built.check_returncode()
    image_id = (
        docker("image", "inspect", "--format", "{{.Id}}", tag).stdout.decode().strip()
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
    assert (
        docker("image", "inspect", "--format", "{{.Id}}", base_tag)
        .stdout.decode()
        .strip()
        == nginx_image
    )
    base_files = await _frontend_files(nginx_image)
    observed = await _frontend_files(
        image_id
    )  # Actual production pre-close readback, --user nginx.
    assert observed == {**base_files, **{n: sha(raw) for n, raw in public.items()}}
    for name, raw in files.items():
        member = context / name
        assert (
            member.read_bytes() == raw and stat.S_IMODE(member.stat().st_mode) == 0o600
        )
    assert all(
        stat.S_IMODE(p.stat().st_mode) == 0o700
        for p in (context, *context.rglob("*"))
        if p.is_dir()
    )
    config = root / "nginx.conf"
    config.write_text(
        "user nginx; pid /tmp/nginx.pid; error_log /dev/stderr; events {} http { access_log off; client_body_temp_path /tmp/client; proxy_temp_path /tmp/proxy; fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi; server { listen 8080; root /usr/share/nginx/html; } }",
        encoding="utf-8",
    )
    config.chmod(0o600)
    container = (
        docker(
            "run",
            "-d",
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--cap-add=CHOWN",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--security-opt=no-new-privileges=true",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,src={config},dst=/fixture.conf,readonly",
            "--entrypoint",
            "nginx",
            image_id,
            "-c",
            "/fixture.conf",
            "-g",
            "daemon off;",
        )
        .stdout.decode()
        .strip()
    )
    assert re.fullmatch(r"[0-9a-f]{64}", container)
    try:
        bound = identity(container, image_id)
        deadline = time.monotonic() + 5
        while True:
            response = docker(
                "exec",
                "--user",
                "nginx",
                container,
                "wget",
                "-q",
                "-O",
                "-",
                "http://127.0.0.1:8080/index.html",
                check=False,
            )
            if response.returncode == 0:
                assert response.stdout == public["index.html"]
                break
            assert time.monotonic() < deadline
            await asyncio.sleep(0.1)
        http = {}
        for name, raw in public.items():
            response = docker(
                "exec",
                "--user",
                "nginx",
                container,
                "wget",
                "-q",
                "-O",
                "-",
                "http://127.0.0.1:8080/" + name,
            )
            assert response.stdout == raw
            http[name] = sha(response.stdout)
        (output / "r1-frontend-result.json").write_text(
            json.dumps(
                {
                    "scope": "R1-02",
                    "container": bound,
                    "recipe_sha256": sha(recipe),
                    "synthetic_base_image": nginx_image,
                    "actual_write_tree_used": True,
                    "host_modes_preserved": {"directory": "0700", "file": "0600"},
                    "readback_user": "nginx",
                    "all_image_files_sha256": observed,
                    "http_sha256": http,
                    "http_vantage": "bound_container_loopback_only",
                    "production_nginx_routes_tested": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        logs = docker("logs", "--tail", "50", container, check=False)
        (output / "r1-nginx.log").write_bytes(logs.stdout + logs.stderr)
        docker("rm", "-f", container)


def main():
    assert (
        sys.platform == "linux"
        and os.geteuid() == 0
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    product = Path(os.environ["FLOW78_CI_PRODUCT_SOURCE"])
    assert (
        subprocess.check_output(
            (
                "git",
                "-c",
                "safe.directory=" + str(product),
                "-C",
                str(product),
                "rev-parse",
                "HEAD",
            )
        )
        .decode()
        .strip()
        == COMMIT
    )
    output = Path(os.environ["FLOW78_CI_OUTPUT"])
    assert (
        output.is_absolute() and output.name == "release-validation" and output.is_dir()
    )
    dependency, nginx = image("FLOW78_CI_CONTROL_IMAGE"), image("FLOW78_CI_NGINX_IMAGE")
    with tempfile.TemporaryDirectory(prefix="flow78-r1-", dir="/root") as temporary:
        root = Path(temporary)
        ttl(root, product, dependency, output)
        asyncio.run(frontend(root, nginx, output))
    print(
        json.dumps(
            {
                "scope": ["R1-01", "R1-02"],
                "targeted_checks_passed": True,
                "full_host_chain_executed": False,
                "production_actions": False,
                "deployable": False,
            }
        )
    )


if __name__ == "__main__":
    main()
