"""Closed offline build descriptions, never a subprocess executor."""

from contracts import AFTER, exact, require, sha

R = "/srv/clothinkai/production-20261018"


def plan(kind, commit, expected_base, actual_base, wheels_complete):
    require(kind in {"backend", "frontend"}, "build_kind")
    sha(commit, 40)
    require(
        expected_base == actual_base and expected_base.startswith("sha256:"),
        "base_tag_drift",
    )
    sha(expected_base[7:])
    require(wheels_complete is True, "dependency_members_missing")
    root = R + "/releases/flow78-" + commit[:12]
    return {
        "mode": "synthetic",
        "executable": False,
        "timeout_seconds": 600,
        "requires_absent_tag": True,
        "output_metadata": root + "/built-app-images.json",
        "argv": (
            "docker",
            "build",
            "--pull=false",
            "--network=none",
            "--no-cache",
            "--memory=1536m",
            "--cpu-period=100000",
            "--cpu-quota=100000",
            "-f",
            root + "/build/control/Dockerfile." + kind + ".offline",
            "-t",
            "clothinkai-flow78-" + kind + ":" + commit[:12],
            root + "/build",
        ),
    }


def accept(receipt, commit):
    exact(
        receipt,
        (
            "mode",
            "image_id",
            "source_commit",
            "os",
            "architecture",
            "exit_code",
            "pip_check",
            "imports",
            "head",
        ),
    )
    require(
        receipt["mode"] == "synthetic"
        and receipt["source_commit"] == commit
        and receipt["os"] == "linux"
        and receipt["architecture"] == "amd64"
        and receipt["exit_code"] == 0
        and receipt["pip_check"] is True
        and receipt["imports"] is True
        and receipt["head"] == AFTER,
        "build_not_verified",
    )
    require(receipt["image_id"].startswith("sha256:"), "image_id")
    sha(receipt["image_id"][7:])
    return receipt["image_id"]


def accept_frontend(receipt, commit):
    exact(
        receipt,
        (
            "mode",
            "image_id",
            "source_commit",
            "os",
            "architecture",
            "exit_code",
            "assets_sha256",
            "expected_assets_sha256",
            "origin_matches",
            "syntax_ok",
        ),
    )
    sha(commit, 40)
    sha(receipt["assets_sha256"])
    require(
        receipt["mode"] == "synthetic"
        and receipt["source_commit"] == commit
        and receipt["os"] == "linux"
        and receipt["architecture"] == "amd64"
        and type(receipt["exit_code"]) is int
        and receipt["exit_code"] == 0
        and receipt["assets_sha256"] == receipt["expected_assets_sha256"]
        and receipt["origin_matches"] is True
        and receipt["syntax_ok"] is True,
        "frontend_not_verified",
    )
    require(receipt["image_id"].startswith("sha256:"), "image_id")
    sha(receipt["image_id"][7:])
    return receipt["image_id"]
