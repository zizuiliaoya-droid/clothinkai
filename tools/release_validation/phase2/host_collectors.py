"""Strict, credential-free projections of actual finite host observations."""

import json
import re

from contracts import exact, require, sha


def _integer(value):
    require(type(value) is int and 0 <= value <= 2**63 - 1, "observation_integer")
    return value


def container_projection(raw, role):
    require(role in ("api", "web", "worker", "beat", "db", "redis"), "container_role")
    require(type(raw) is bytes and 0 < len(raw) <= 65536, "container_size")

    def pairs(items):
        require(len(items) == len(dict(items)), "duplicate_projection_key")
        return dict(items)

    try:
        row = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        require(False, "container_json")
    exact(
        row,
        (
            "id",
            "name",
            "image",
            "running",
            "paused",
            "restarting",
            "oom",
            "exit_code",
            "restarts",
            "restart_policy",
            "pid",
            "networks",
        ),
    )
    sha(row["id"])
    require(row["name"] == "/clothinkai-prod-" + role + "-20261018", "container_name")
    require(
        type(row["image"]) is str and row["image"].startswith("sha256:"),
        "container_image",
    )
    sha(row["image"][7:])
    for key in ("running", "paused", "restarting", "oom"):
        require(type(row[key]) is bool, "container_boolean")
    for key in ("exit_code", "restarts", "pid"):
        _integer(row[key])
    require(
        row["restart_policy"] in {"no", "always", "unless-stopped", "on-failure"},
        "restart_policy",
    )
    require(
        type(row["networks"]) is dict and 0 < len(row["networks"]) <= 4,
        "container_networks",
    )
    for name, network_id in row["networks"].items():
        require(
            type(name) is str and re.fullmatch(r"clothinkai-[a-z0-9-]{1,64}", name),
            "network_name",
        )
        sha(network_id)
    return row


def _systemd(raw, subject, service=False):
    require(
        subject in {"backup", "renew"} and type(raw) is bytes and 0 < len(raw) <= 8192,
        "systemd_input",
    )
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeError:
        require(False, "systemd_encoding")
    require(all("=" in line for line in lines), "systemd_format")
    pairs = [line.split("=", 1) for line in lines]
    require(len(pairs) == len(dict(pairs)), "systemd_duplicate")
    row = dict(pairs)
    keys = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "Job")
    exact(row, keys + (("MainPID", "ControlPID", "ControlGroup") if service else ()))
    unit = "clothinkai-production-" + subject + (".service" if service else ".timer")
    require(row["Id"] == unit and row["LoadState"] == "loaded", "systemd_unit")
    require(
        row["ActiveState"] in {"active", "inactive", "failed"},
        "systemd_transition_unknown",
    )
    require(
        row["UnitFileState"] in {"enabled", "disabled", "masked", "static"},
        "systemd_enable_unknown",
    )
    require(re.fullmatch(r"[0-9]{1,10}", row["Job"]) is not None, "systemd_job")
    return row


def timer_projection(raw, subject):
    row = _systemd(raw, subject)
    require(
        row["SubState"] in {"waiting", "elapsed", "dead", "failed"}, "timer_substate"
    )
    return {
        "active": row["ActiveState"] == "active",
        "enabled": row["UnitFileState"] == "enabled",
        "masked": row["UnitFileState"] == "masked",
        "queued_jobs": int(row["Job"] != "0"),
        "healthy": row["ActiveState"] != "failed",
        "unit_sha256": None,
        "pending": ["unit_file_hash", "service_children", "manual_entries"],
    }


def service_projection(raw, subject):
    row = _systemd(raw, subject, True)
    require(
        row["SubState"] in {"running", "exited", "dead", "failed"}, "service_substate"
    )
    for key in ("MainPID", "ControlPID"):
        require(re.fullmatch(r"[0-9]{1,10}", row[key]) is not None, "service_pid")
    require(
        row["ControlGroup"]
        in {"", "/system.slice/clothinkai-production-" + subject + ".service"},
        "service_cgroup",
    )
    return {
        "service_running": row["ActiveState"] == "active"
        or row["MainPID"] != "0"
        or row["ControlPID"] != "0",
        "queued_jobs": int(row["Job"] != "0"),
        "main_pid": int(row["MainPID"]),
        "control_pid": int(row["ControlPID"]),
        "child_processes": None,
        "pending": ["cgroup_children_and_external_children"],
    }


def queue_projection(row):
    exact(row, ("ready", "unacked_hash", "unacked_index", "business_sessions"))
    exact(row["ready"], ("default", "backup", "crawler", "report"))
    for values in row["ready"].values():
        require(type(values) is list and len(values) == 4, "queue_priorities")
        for value in values:
            _integer(value)
    for key in ("unacked_hash", "unacked_index", "business_sessions"):
        _integer(row[key])
    return {
        **row,
        "drain_verified": False,
        "pending": [
            "scheduled_retry_active",
            "producer_freeze",
            "http_requests",
            "broker_settings_binding",
            "two_stable_windows",
        ],
    }
