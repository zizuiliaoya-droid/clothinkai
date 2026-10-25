"""Pure four-application compose transformation with an exact allowed difference set."""

from copy import deepcopy

from contracts import require, sha
from dashboard_contract import with_external_network

R = "/srv/clothinkai/production-20261018"
APPS = ("api", "web", "worker", "beat")
BEAT_HEALTH = {
    "test": ["CMD", "python", "/opt/update/beat_health_candidate.py"],
    "interval": "15s",
    "timeout": "5s",
    "retries": 5,
}


def generate(base, commit, backend, frontend, beat_format):
    sha(commit, 40)
    require(beat_format in {"dbm.gnu", "dbm.dumb"}, "unknown_beat_format")
    for image in (backend, frontend):
        require(image.startswith("sha256:"), "image_id")
        sha(image[7:])
    release = R + "/releases/flow78-" + commit[:12]
    result = with_external_network(base)
    for role in APPS:
        service = result["services"]["prod-" + role]
        service["image"] = frontend if role == "web" else backend
        service["restart"] = "no"
        if role == "web":
            require(
                service["volumes"].count(R + "/build/control:/opt/production:ro") == 1,
                "web_original_control_mount",
            )
            service["volumes"] = [
                release + "/web-control:/opt/production:ro"
                if value == R + "/build/control:/opt/production:ro"
                else value
                for value in service["volumes"]
            ]
        else:
            service["command"] = [
                "python",
                "/opt/release/start_067.py",
                role,
                "--expected-commit",
                commit,
            ]
            service["volumes"] += [
                release + "/guards/" + role + ":/run/release-guard:ro",
                release + "/control:/opt/update:ro",
            ]
            if role == "worker":
                service["healthcheck"] = {"disable": True}
            elif role == "beat":
                service["command"] += ["--beat-db-format", beat_format]
                service["healthcheck"] = deepcopy(BEAT_HEALTH)
    validate(result, base, commit)
    return result


def validate(candidate, base, commit):
    require(
        set(candidate) == set(base)
        and candidate["name"] == "clothinkai-production-20261018"
        and candidate["networks"] == with_external_network(base)["networks"],
        "compose_identity",
    )
    require(
        set(candidate["services"])
        == set(base["services"])
        == {"prod-db", "prod-redis", *["prod-" + r for r in APPS]},
        "services",
    )
    for name in ("prod-db", "prod-redis"):
        require(
            candidate["services"][name] == base["services"][name],
            "infrastructure_changed",
        )
    release = R + "/releases/flow78-" + commit[:12]
    allowed = {"image", "restart", "command", "volumes", "healthcheck"}
    for role in APPS:
        old, new = (
            base["services"]["prod-" + role],
            candidate["services"]["prod-" + role],
        )
        require(
            all(
                old.get(k) == new.get(k)
                for k in set(old) | set(new)
                if k not in allowed and not (role == "web" and k == "networks")
            ),
            "compose_unauthorized_difference",
        )
        require(new["restart"] == "no", "restart_policy")
        require(
            type(new["image"]) is str and new["image"].startswith("sha256:"), "image_id"
        )
        sha(new["image"][7:])
        if role != "web":
            expected = old["volumes"] + [
                release + "/guards/" + role + ":/run/release-guard:ro",
                release + "/control:/opt/update:ro",
            ]
            require(new["volumes"] == expected, "ops_mounts")
            require(
                R + "/build/control:/opt/production:ro" in expected
                and R + "/backups:/run/backups:ro" in expected,
                "uploader_mount",
            )
            command = [
                "python",
                "/opt/release/start_067.py",
                role,
                "--expected-commit",
                commit,
            ]
            if role == "beat":
                require(
                    len(new["command"]) == 7
                    and new["command"][-1] in {"dbm.gnu", "dbm.dumb"},
                    "beat_format",
                )
                command += ["--beat-db-format", new["command"][-1]]
            require(new["command"] == command, "start_entry")
        else:
            require(
                new["networks"]
                == with_external_network(base)["services"]["prod-web"]["networks"],
                "dashboard_web_network",
            )
            require(
                new.get("command") == old.get("command")
                and new.get("healthcheck") == old.get("healthcheck"),
                "web_entry",
            )
            require(
                new["volumes"]
                == [
                    release + "/web-control:/opt/production:ro"
                    if v == R + "/build/control:/opt/production:ro"
                    else v
                    for v in old["volumes"]
                ],
                "web_mount",
            )
    require(
        candidate["services"]["prod-worker"]["healthcheck"] == {"disable": True},
        "worker_api_health",
    )
    require(
        candidate["services"]["prod-api"].get("healthcheck")
        == base["services"]["prod-api"].get("healthcheck"),
        "api_health_changed",
    )
    require(
        candidate["services"]["prod-beat"]["healthcheck"] == BEAT_HEALTH,
        "beat_health_changed",
    )
    require(
        len(
            {
                candidate["services"]["prod-" + r]["image"]
                for r in ("api", "worker", "beat")
            }
        )
        == 1,
        "backend_image_split",
    )


def up_plan(commit, roles):
    sha(commit, 40)
    require(
        roles
        in (
            ("prod-api", "prod-web"),
            ("prod-api",),
            ("prod-web",),
            ("prod-worker",),
            ("prod-beat",),
        ),
        "up_scope",
    )
    return (
        "docker",
        "compose",
        "--project-name",
        "clothinkai-production-20261018",
        "-f",
        R + "/releases/flow78-" + commit[:12] + "/compose.json",
        "up",
        "--no-deps",
        "-d",
        "--no-build",
        "--pull",
        "never",
        *roles,
    )


def recovery_compose(base):
    """Recover the old web image/control while retaining the deployed board network."""
    result = with_external_network(base)
    require(
        result["services"]["prod-web"]["volumes"].count(
            R + "/build/control:/opt/production:ro"
        )
        == 1,
        "web_original_control_mount",
    )
    return result
