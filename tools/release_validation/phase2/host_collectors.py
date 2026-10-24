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
    require(
        row["Job"] == "" or re.fullmatch(r"[0-9]{1,10}", row["Job"]) is not None,
        "systemd_job",
    )
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
        "queued_jobs": int(row["Job"] not in {"", "0"}),
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
        "healthy": row["ActiveState"] != "failed",
        "service_running": row["ActiveState"] == "active"
        or row["MainPID"] != "0"
        or row["ControlPID"] != "0",
        "queued_jobs": int(row["Job"] not in {"", "0"}),
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


def dashboard_board_projection(raw):
    from contracts import parse_json
    from dashboard_contract import BOARD, BOARD_ID, BOARD_IMAGE, NETWORK, NETWORK_ID

    require(type(raw) is bytes and len(raw) <= 16384, "dashboard_board_size")
    row = parse_json(raw)
    exact(row, ("id", "image", "networks", "aliases"))
    require(
        row["id"] == BOARD_ID
        and row["image"] == BOARD_IMAGE
        and row["networks"] == {NETWORK: NETWORK_ID},
        "dashboard_board_identity",
    )
    require(
        type(row["aliases"]) is list
        and "progress-dashboard" in row["aliases"]
        and set(row["aliases"]) <= {"progress-dashboard", BOARD, BOARD_ID[:12]},
        "dashboard_board_alias",
    )
    return row


def collect_unit_metadata(subject, service=False):
    """Fixed root-owned unit and fixed cgroup counts; never emits unit text or argv."""
    import hashlib
    import os
    import stat

    from linux_transport import require_linux

    require_linux()
    require(
        subject in {"backup", "renew"} and type(service) is bool,
        "unit_metadata_subject",
    )
    unit = "clothinkai-production-" + subject + (".service" if service else ".timer")
    path = "/etc/systemd/system/" + unit
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == info.st_gid == 0
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o644
            and 0 < info.st_size <= 16384,
            "unit_metadata_file",
        )
        raw = os.read(fd, 16385)
        require(len(raw) == info.st_size, "unit_file_changed")
    finally:
        os.close(fd)
    count, present = 0, False
    if service:
        group = "/sys/fs/cgroup/system.slice/" + unit
        try:
            parent = os.open(group, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except FileNotFoundError:
            parent = None
        if parent is not None:
            try:
                fd = os.open("cgroup.procs", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    raw_pids = os.read(fd, 65537)
                finally:
                    os.close(fd)
                require(len(raw_pids) <= 65536, "unit_cgroup_size")
                lines = raw_pids.decode("ascii").splitlines()
                require(
                    all(re.fullmatch(r"[1-9][0-9]{0,9}", value) for value in lines),
                    "unit_cgroup_format",
                )
                count, present = len(set(lines)), True
            finally:
                os.close(parent)
    return {
        "unit_sha256": hashlib.sha256(raw).hexdigest(),
        "unit_bytes": len(raw),
        "cgroup_present": present,
        "cgroup_pid_count": count,
        "exclusive_idle": False,
        "pending": [
            "unit_fragment_path_binding",
            "manual_entries",
            "children_outside_unit_cgroup",
        ],
    }


UNIT_PINS = {
    "clothinkai-production-backup.service": "923b26991127bb0ac869c1c673174983ad9aaa80f5b6d965ef7fd7a39337f726",
    "clothinkai-production-backup.timer": "ca97bad4998f9a1f1e38c14751a1930896480b5aa0f091d909511ced6527e6e0",
    "clothinkai-production-renew.service": "b9c444995810b378baff204d497c6f0d330be07fd83058efa181157ed6a29444",
    "clothinkai-production-renew.timer": "bd9f88d9b6c8ad7a5c4b8cd18389d5744ff303ada918c09ee58097f510c8fa5c",
}


def unit_source_projection(raw, subject):
    require(
        subject in {"backup", "renew", "backup-service", "renew-service"},
        "unit_source_subject",
    )
    service = subject.endswith("-service")
    timer = subject.removesuffix("-service")
    unit = "clothinkai-production-" + timer + (".service" if service else ".timer")
    require(type(raw) is bytes and len(raw) <= 8192, "unit_source_size")
    lines = raw.decode("ascii").splitlines()
    require(all("=" in line for line in lines), "unit_source_format")
    pairs = [line.split("=", 1) for line in lines]
    require(len(pairs) == len(dict(pairs)), "unit_source_duplicate")
    row = dict(pairs)
    exact(row, ("Id", "FragmentPath", "DropInPaths"))
    require(
        row
        == {
            "Id": unit,
            "FragmentPath": "/etc/systemd/system/" + unit,
            "DropInPaths": "",
        },
        "unit_source_binding",
    )
    return {"unit": unit, "fragment_path": row["FragmentPath"], "dropins": []}


def web_control_projection(raw):
    from contracts import parse_json

    require(type(raw) is bytes and len(raw) <= 8192, "web_control_size")
    row = parse_json(raw)
    exact(row, ("id", "name", "image", "mounts"))
    sha(row["id"])
    require(row["name"] == "/clothinkai-prod-web-20261018", "web_control_name")
    require(
        type(row["image"]) is str and row["image"].startswith("sha256:"),
        "web_control_image",
    )
    sha(row["image"][7:])
    require(
        type(row["mounts"]) is list and len(row["mounts"]) == 1,
        "web_control_mount_count",
    )
    mount = row["mounts"][0]
    exact(mount, ("source", "destination", "rw"))
    require(
        type(mount["source"]) is str
        and mount["destination"] == "/opt/production"
        and mount["rw"] is False,
        "web_control_mount",
    )
    return row
