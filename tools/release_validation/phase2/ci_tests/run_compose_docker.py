"""Real Compose creation/replacement binding; original application commands never run."""

import copy
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compose_candidate import APPS, R, generate, up_plan
from contracts import Rejected
from switch_apps import verify_replaced

COMMIT = "55477c44f09bb1a25169bfb44ff6c90bc4f9cedb"
ORIGINAL_SHA = "5b23a8f8d87d0a044db6a41f97b3eef23cd5fd045e23badcb23861ae31b7bb09"
PROJECTION = (
    '{"id":{{json .Id}},"image":{{json .Image}},"name":{{json .Name}},'
    '"status":{{json .State.Status}},"running":{{json .State.Running}},'
    '"command":{{json .Config.Cmd}},"entrypoint":{{json .Config.Entrypoint}},'
    '"labels":{{json .Config.Labels}},"readonly":{{json .HostConfig.ReadonlyRootfs}},'
    '"ports":{{json .HostConfig.PortBindings}},"mounts":['
    "{{$first := true}}{{range .Mounts}}{{if not $first}},{{end}}"
    '{"source":{{json .Source}},"destination":{{json .Destination}},'
    '"rw":{{json .RW}},"type":{{json .Type}}}{{$first = false}}{{end}}]}'
)


def call(*args):
    try:
        return subprocess.check_output(
            ("docker", *args), stderr=subprocess.PIPE, timeout=60
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print((exc.stderr or b"")[-8192:].decode("utf-8", "replace"), file=sys.stderr)
        raise


def image(key):
    value = os.environ[key]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", value)
    assert (
        call("image", "inspect", "--format", "{{.Id}}", value).decode().strip() == value
    )
    return value


def fixture_compose(model, root, project, networks, fallback, *, original):
    """Explicit disposable path/name/image mapping, never a production compose."""
    result = copy.deepcopy(model)
    result["name"] = project
    for key in result["networks"]:
        result["networks"][key] = {"name": networks[key]["name"], "external": True}
    for name, service in result["services"].items():
        service["container_name"] = project + "-" + name
        service.pop("ports", None)  # Nothing is published, even while only created.
        if original or name in {"prod-db", "prod-redis"}:
            service["image"] = fallback
        volumes = []
        for item in service.get("volumes", []):
            source, target, *options = item.split(":")
            assert source.startswith(R + "/") and options in ([], ["ro"])
            source = root / "mapped" / source[len(R) + 1 :]
            source.mkdir(parents=True, exist_ok=True, mode=0o700)
            volumes.append(str(source) + ":" + target + (":ro" if options else ""))
        service["volumes"] = volumes
    return result


def observe(compose, project, config):
    result = {}
    for name, service in config["services"].items():
        identity = call(*compose, "ps", "--all", "--quiet", name).decode().strip()
        assert re.fullmatch(r"[0-9a-f]{64}", identity)
        row = json.loads(call("inspect", "--format", PROJECTION, identity))
        assert row["id"] == identity and row["image"] == service["image"]
        assert row["name"] == "/" + project + "-" + name
        assert row["labels"]["com.docker.compose.project"] == project
        assert row["labels"]["com.docker.compose.service"] == name
        assert row["status"] == "created" and row["running"] is False
        assert row["ports"] in ({}, None)
        if "command" in service:
            assert row["command"] == service["command"]
        if "entrypoint" in service:
            assert row["entrypoint"] == service["entrypoint"]
        assert row["readonly"] is service.get("read_only", False)
        expected = []
        for volume in service["volumes"]:
            source, destination, *options = volume.split(":")
            expected.append(
                {
                    "source": source,
                    "destination": destination,
                    "rw": not options,
                    "type": "bind",
                }
            )
        assert sorted(row["mounts"], key=lambda r: r["destination"]) == sorted(
            expected, key=lambda r: r["destination"]
        )
        result[name.removeprefix("prod-")] = row
    return result


def main():
    assert sys.platform == "linux" and os.geteuid() == 0
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    old = image("FLOW78_CI_CONTROL_IMAGE")
    backend = image("FLOW78_CI_PRODUCER_IMAGE")
    frontend = image("FLOW78_CI_NGINX_IMAGE")
    assert len({old, backend, frontend}) == 3
    raw = (
        Path(__file__).resolve().parents[1] / "templates/original-compose.json"
    ).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_SHA
    base = json.loads(raw)
    candidate = generate(base, COMMIT, backend, frontend, "dbm.gnu")
    project = "flow78-compose-" + secrets.token_hex(6)
    networks = {}
    version = call("compose", "version").decode().strip()
    output = Path(os.environ["FLOW78_CI_OUTPUT"])
    assert (
        output.is_absolute() and output.name == "release-validation" and output.is_dir()
    )
    observations = []
    with tempfile.TemporaryDirectory(
        prefix="flow78-compose-", dir="/root"
    ) as temporary:
        root = Path(temporary)
        compose = (
            "compose",
            "--project-name",
            project,
            "-f",
            str(root / "compose.json"),
        )
        try:
            for key in candidate["networks"]:
                name = project + "-" + key
                identity = (
                    call("network", "create", "--internal", name).decode().strip()
                )
                assert re.fullmatch(r"[0-9a-f]{64}", identity)
                networks[key] = {"name": name, "id": identity}
                observed = json.loads(
                    call(
                        "network",
                        "inspect",
                        "--format",
                        '{"id":{{json .Id}},"internal":{{json .Internal}}}',
                        identity,
                    )
                )
                assert observed == {"id": identity, "internal": True}
            original = fixture_compose(
                base, root, project, networks, old, original=True
            )
            after_model = fixture_compose(
                candidate, root, project, networks, old, original=False
            )
            current = copy.deepcopy(original)
            path = root / "compose.json"
            path.write_text(json.dumps(current), encoding="utf-8")
            call(*compose, "up", "--no-start", "--no-build", "--pull", "never")
            before = observe(compose, project, current)
            steps = []
            for role in APPS:
                current["services"]["prod-" + role] = after_model["services"][
                    "prod-" + role
                ]
                path.write_text(json.dumps(current), encoding="utf-8")
                plan = up_plan(COMMIT, ("prod-" + role,))
                assert plan[6:] == (
                    "up",
                    "--no-deps",
                    "-d",
                    "--no-build",
                    "--pull",
                    "never",
                    "prod-" + role,
                )
                # Scope and no-build/no-pull match host plan. --no-start is an
                # explicit fixture boundary: no guard/config/business execution.
                call(
                    *compose,
                    "up",
                    "--no-deps",
                    "--no-start",
                    "--no-build",
                    "--pull",
                    "never",
                    "prod-" + role,
                )
                after = observe(compose, project, current)
                observations.append(
                    {
                        "role": role,
                        "before": before,
                        "after": after,
                        "expected_image": current["services"]["prod-" + role]["image"],
                    }
                )
                (output / "compose-observations.json").write_text(
                    json.dumps(observations, sort_keys=True), encoding="utf-8"
                )
                verify_replaced(
                    before, after, role, current["services"]["prod-" + role]["image"]
                )
                assert (
                    call(
                        "ps",
                        "--all",
                        "--quiet",
                        "--no-trunc",
                        "--filter",
                        "id=" + before[role]["id"],
                    ).strip()
                    == b""
                )
                negative = copy.deepcopy(after)
                negative["db"]["id"] = "f" * 64
                try:
                    verify_replaced(before, negative, role, after[role]["image"])
                except Rejected:
                    pass
                else:
                    raise AssertionError("unrelated infrastructure accepted")
                steps.append(
                    {
                        "role": role,
                        "old_id": before[role]["id"],
                        "new": after[role],
                        "other_five_unchanged": True,
                    }
                )
                before = after
            print(
                json.dumps(
                    {
                        "mode": "actual_compose_created_container_replacement",
                        "compose_version": version,
                        "source_commit": COMMIT,
                        "original_compose_sha256": ORIGINAL_SHA,
                        "networks": networks,
                        "steps": steps,
                        "final_infrastructure": {k: before[k] for k in ("db", "redis")},
                        "mapped_fixture_only": True,
                        "containers_started": False,
                        "original_start_guard_executed": False,
                        "application_readiness_verified": False,
                        "production_image_verified": False,
                        "production_29_actions_verified": False,
                        "release_gate": "HOLD_remaining_release_validation",
                    },
                    sort_keys=True,
                )
            )
        finally:
            # Discover any partially created result, bind exact project/name, then
            # remove full IDs once. Never replay an unknown-result Compose write.
            found = (
                call(
                    "ps",
                    "--all",
                    "--quiet",
                    "--no-trunc",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                )
                .decode()
                .split()
            )
            for identity in found:
                assert re.fullmatch(r"[0-9a-f]{64}", identity)
                row = json.loads(call("inspect", "--format", PROJECTION, identity))
                assert (
                    row["id"] == identity
                    and row["labels"]["com.docker.compose.project"] == project
                )
                assert row["name"].startswith("/" + project + "-prod-")
                call("rm", "--force", identity)
            for network in networks.values():
                call("network", "rm", network["id"])


if __name__ == "__main__":
    main()
