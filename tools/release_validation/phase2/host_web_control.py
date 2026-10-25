"""Atomic fixed web-control transitions, with durable intent and existing authority.

No nginx success/drain/open assertion follows from a successful filesystem switch.
The caller must perform identity-bound test/reload and independent observations.
"""

import hashlib
import os
import stat
from pathlib import Path

from contracts import require
from dashboard_contract import ACTIVE_SHA, original_active, validate_routes
from fixed_host_transport import ROOT, _private_fixed_file
from host_sequence import HostJournal
from sql_policy import require_host_authority

TEMPLATE_PINS = {
    "nginx-maintenance.conf": "85198931f26d43f8fb36475ca47e699800d9a46d027684453ad8ba97564f04de",
    "nginx-release-closed.conf": "85198931f26d43f8fb36475ca47e699800d9a46d027684453ad8ba97564f04de",
    "nginx-release-open.conf": "89519863caaa05ed9e7e314bf7f912db240e2a8371ee26208c64579d98e05a93",
}


def _control_fd(spec, step):
    require(step in {"CLOSE_GATE", "REPLACE_WEB", "OPEN_GATE"}, "web_control_step")
    relative = (
        ("build", "control")
        if step == "CLOSE_GATE"
        else ("releases", spec.data["release_id"], "web-control")
    )
    target = Path(ROOT).joinpath(*relative)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in target.parts[1:]:
            new = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
            info = os.fstat(fd)
            require(
                info.st_uid == 0 and not info.st_mode & 0o022, "web_control_directory"
            )
        return fd, target
    except BaseException:
        os.close(fd)
        raise


def _read_active(parent, absent=False):
    try:
        fd = os.open("nginx-active.conf", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        require(absent, "web_active_missing")
        return None
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_gid == os.getegid()
            and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o644
            and 0 < info.st_size <= 65536,
            "web_active_metadata",
        )
        raw = os.read(fd, 65537)
        require(len(raw) == info.st_size, "web_active_changed")
        return raw
    finally:
        os.close(fd)


def switch_active(spec, journal, step):
    require_host_authority(spec)
    require(
        type(journal) is HostJournal and journal.lock is not None, "web_journal_lock"
    )
    require(
        journal.root == Path(ROOT) / "releases" / spec.data["release_id"] / "state",
        "web_journal_binding",
    )
    from host_sequence import replay

    state = replay(spec, journal.read())
    require(state.pending is not None and state.hold is None, "web_switch_intent")
    last = journal.read()[-1]
    require(
        last["kind"] == "intent"
        and last["payload"]["name"] == step
        and last["payload"]["input_sha256"] == spec.sha256,
        "web_switch_step",
    )
    template = {
        "CLOSE_GATE": "nginx-maintenance.conf",
        "REPLACE_WEB": "nginx-release-closed.conf",
        "OPEN_GATE": "nginx-release-open.conf",
    }[step]
    raw = _private_fixed_file(Path(__file__).with_name("templates") / template, 65536)
    require(
        hashlib.sha256(raw).hexdigest() == TEMPLATE_PINS[template], "web_template_pin"
    )
    validate_routes(raw)
    parent, target = _control_fd(spec, step)
    try:
        current = _read_active(parent, absent=step == "REPLACE_WEB")
        if step == "CLOSE_GATE":
            original_active(current)
            journal.directory.create("old-active.conf", current)
        elif step == "OPEN_GATE":
            require(
                hashlib.sha256(current).hexdigest()
                == TEMPLATE_PINS["nginx-release-closed.conf"],
                "web_open_requires_closed",
            )
            original_active(journal.directory.read("old-active.conf", 65536))
        else:
            original_active(journal.directory.read("old-active.conf", 65536))
            require(
                current is None or hashlib.sha256(current).hexdigest() == ACTIVE_SHA,
                "web_new_control_unknown",
            )
        _atomic_replace(parent, current, raw, absent=step == "REPLACE_WEB")
        return {
            "step": step,
            "control_directory": str(target),
            "active_sha256": hashlib.sha256(raw).hexdigest(),
            "nginx_reloaded": False,
            "gate_verified": False,
            "dashboard_verified": False,
        }
    finally:
        os.close(parent)


def _atomic_replace(parent, current, raw, *, absent=False):
    """Fixed-basename dirfd kernel; also exercised only inside disposable Linux CI."""
    require(type(raw) is bytes and 0 < len(raw) <= 65536, "web_active_size")
    require(_read_active(parent, absent=absent) == current, "web_active_race")
    fd = os.open(
        "nginx-active.conf.pending",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o644,
        dir_fd=parent,
    )
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o644)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    require(_read_active(parent, absent=absent) == current, "web_active_race")
    os.replace(
        "nginx-active.conf.pending",
        "nginx-active.conf",
        src_dir_fd=parent,
        dst_dir_fd=parent,
    )
    os.fsync(parent)
    require(_read_active(parent) == raw, "web_active_readback")
