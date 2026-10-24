"""Fixed original timer freeze with private original snapshot and exact readbacks."""

from datetime import datetime, timezone
from pathlib import Path

from contracts import TARGET, canonical, digest, exact, require
from fixed_host_transport import ROOT, _capture, collect, command
from host_collectors import UNIT_PINS
from sql_policy import parse, require_host_authority, sha


def manual_freeze(spec, journal):
    """Consume an externally supplied operator attestation; never create it.

    This records the operator's manual-entry freeze, not an OS proof that a root
    user is unable to invoke a script. Automatic timer state is observed separately.
    """
    raw = journal.directory.read("manual-freeze.json", 16384)
    binding = parse(spec._binding)
    require(sha(raw) == binding["manual_freeze_sha256"], "manual_freeze_hash")
    row = parse(raw)
    exact(
        row,
        (
            "purpose",
            "target",
            "release_id",
            "boot_id",
            "entries",
            "issued_at",
            "expires_at",
        ),
    )
    require(
        row["purpose"] == "operator-attested-manual-backup-renew-freeze"
        and row["target"] == TARGET
        and row["release_id"] == spec.data["release_id"]
        and row["boot_id"] == binding["boot_id"]
        and row["entries"] == ["manual-backup", "manual-renew"],
        "manual_freeze_scope",
    )
    try:
        issued, expires = (
            datetime.fromisoformat(row[k]) for k in ("issued_at", "expires_at")
        )
        now = datetime.now(timezone.utc)
        require(
            issued.utcoffset().total_seconds()
            == expires.utcoffset().total_seconds()
            == 0
            and 0 < (expires - issued).total_seconds() <= 21600
            and issued <= now < expires,
            "manual_freeze_expiry",
        )
    except (ValueError, TypeError, AttributeError):
        require(False, "manual_freeze_time")
    return sha(raw)


async def observe_timer(subject):
    timer = (await collect("timer_show", subject))["facts"]
    service = (await collect("service_show", subject))["facts"]
    sources = []
    for suffix, value in (("", timer), ("-service", service)):
        source = (await collect("unit_source_show", subject + suffix))["facts"]
        require(
            source["dropins"] == []
            and source["fragment_path"] == "/etc/systemd/system/" + source["unit"],
            "timer_source",
        )
        require(
            value["unit_metadata"]["unit_sha256"] == UNIT_PINS[source["unit"]],
            "timer_unit_drift",
        )
        sources.append(source)
    require(
        timer["healthy"] is True
        and service["healthy"] is True
        and timer["queued_jobs"] == service["queued_jobs"] == 0
        and service["service_running"] is False
        and service["main_pid"] == service["control_pid"] == 0
        and service["unit_metadata"]["cgroup_pid_count"] == 0,
        "timer_inflight",
    )
    return {"timer": timer, "service": service, "sources": sources}


async def freeze_timers(spec, journal):
    require_host_authority(spec)
    from host_sequence import HostJournal

    require(
        type(journal) is HostJournal
        and journal.lock is not None
        and journal.root == Path(ROOT) / "releases" / spec.data["release_id"] / "state",
        "timer_host_lock",
    )
    from host_sequence import replay

    state = replay(spec, journal.read())
    last = journal.read()[-1]
    require(
        state.pending is not None
        and state.hold is None
        and last["kind"] == "intent"
        and last["payload"]["name"] == "FREEZE_TIMERS"
        and last["payload"]["input_sha256"] == spec.sha256,
        "timer_intent",
    )
    attestation_sha = manual_freeze(spec, journal)
    original = {
        subject: await observe_timer(subject) for subject in ("backup", "renew")
    }
    journal.directory.create("original-timers.json", canonical(original))
    after = {}
    for subject in ("backup", "renew"):
        timer = original[subject]["timer"]
        require(
            not timer["masked"] or not (timer["active"] or timer["enabled"]),
            "timer_mask_conflict",
        )
        for condition, operation in (
            (timer["active"], "timer_stop"),
            (timer["enabled"], "timer_disable"),
        ):
            if condition:
                require_host_authority(spec)
                manual_freeze(spec, journal)
                await _capture(command(operation, subject), timeout=15)
        after[subject] = await observe_timer(subject)
        current = after[subject]["timer"]
        require(
            current["active"] is False
            and current["enabled"] is False
            and current["masked"] == timer["masked"],
            "timer_freeze_readback",
        )
    manual_freeze(spec, journal)
    return {
        "original_sha256": digest(original),
        "after_sha256": digest(after),
        "operator_attestation_sha256": attestation_sha,
        "automatic_timers_frozen": True,
        "manual_entry_status": "operator_attested_not_OS_enforced",
        "timers": {
            key: {
                field: after[key]["timer"][field]
                for field in ("active", "enabled", "masked")
            }
            for key in after
        },
    }
