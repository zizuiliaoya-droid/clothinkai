"""Finite host journal/29-step dispatcher candidate; incomplete steps stop explicitly.

No arbitrary argv callback is provided. Write dispatches require a separately
provisioned approval and a durable intent; incomplete actions retain the lock.
"""

import os
import stat
from dataclasses import asdict, replace
from pathlib import Path

from contracts import STEPS, Rejected, digest, exact, require, sha
from fixed_host_transport import ROLES, ROOT, _capture, collect, command
from host_release_input import HostReleaseSpec, validate_host_input
from linux_transport import LinuxJournal, PrivateDirectory, require_linux
from sql_policy import HostMigrationSpec, parse, require_host_authority
from state_machine import State, intent, next_action


class HostDirectory(PrivateDirectory):
    """Reuse reviewed dirfd IO; only the fixed root-owned release/state is reachable."""

    def __init__(self, commit):
        sha(commit, 40)
        require_linux()
        require(os.geteuid() == 0, "host_root_required")
        self.root = Path(ROOT) / "releases" / ("flow78-" + commit[:12]) / "state"
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
                    "host_directory_owner_mode",
                )
            require(stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "host_state_private")
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise


class HostJournal(LinuxJournal):
    mode = "linux_host_candidate"

    def __init__(self, spec):
        require(
            type(spec) in (HostMigrationSpec, HostReleaseSpec), "host_spec_required"
        )
        validate_host_input(spec)
        self.directory = HostDirectory(spec.data["source_commit"])
        self.root = self.directory.root
        self.lock = None
        self.global_lock = None
        self.claim = {
            "release_id": spec.data["release_id"],
            "source_commit": spec.data["source_commit"],
            "input_sha256": spec.sha256,
        }

    def acquire(self):
        import fcntl

        from contracts import canonical
        from host_backup_control import _directory, _read

        parent = _directory(ROOT)
        fd = None
        try:
            fd = os.open(
                "flow78-release.lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            info = os.fstat(fd)
            require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == 0
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600,
                "host_global_lock_metadata",
            )
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                claim = os.open(
                    "flow78-active.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                require(
                    parse(_read(parent, "flow78-active.json", 8192)) == self.claim,
                    "host_other_release_claimed",
                )
            else:
                with os.fdopen(claim, "wb") as handle:
                    handle.write(canonical(self.claim))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.fsync(parent)
            super().acquire()
            self.global_lock, fd = fd, None
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def release(self):
        import fcntl

        super().release()
        if self.global_lock is not None:
            fcntl.flock(self.global_lock, fcntl.LOCK_UN)
            os.close(self.global_lock)
            self.global_lock = None
        # Keep the durable claim. Operator closes it only after verified complete
        # or the separately reviewed PRE066 recovery; process death is not proof.

    def close_completed_claim(self, spec):
        from host_backup_control import _directory, _read
        from host_migration_control import recorded_facts

        require(
            self.lock is not None and self.global_lock is not None,
            "host_claim_lock_required",
        )
        state = replay(spec, self.read())
        require(
            state.completed == STEPS and state.pending is None and state.hold is None,
            "host_claim_not_complete",
        )
        for step in STEPS:
            recorded_facts(spec, self, step)
        parent = _directory(ROOT)
        try:
            before = os.stat("flow78-active.json", dir_fd=parent, follow_symlinks=False)
            require(
                parse(_read(parent, "flow78-active.json", 8192)) == self.claim,
                "host_claim_replaced",
            )
            after = os.stat("flow78-active.json", dir_fd=parent, follow_symlinks=False)
            require(
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
                "host_claim_changed",
            )
            os.unlink("flow78-active.json", dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


def replay(spec, rows):
    """Consume validated durable rows; never infer progress from a mutable phase."""
    state = State()
    active = None
    for row in rows:
        kind, payload = row["kind"], row["payload"]
        if kind == "intent":
            action = next_action(spec, state)
            require(
                hasattr(action, "action_id") and payload == asdict(action),
                "host_history_intent",
            )
            state, active = intent(spec, state, action), action
        elif kind == "result":
            require(
                active is not None and state.pending == active.action_id,
                "host_result_without_intent",
            )
            exact(payload, ("action_id", "input_sha256", "facts_sha256", "completed"))
            sha(payload["facts_sha256"])
            require(
                payload["action_id"] == active.action_id
                and payload["input_sha256"] == spec.sha256
                and payload["completed"] is True,
                "host_history_result",
            )
            state = replace(
                state, completed=state.completed + (active.name,), pending=None
            )
            if active.name == "VERIFY_SCHEMA":
                from contracts import AFTER

                state = replace(state, schema=AFTER)
            active = None
        elif kind == "hold":
            state = replace(state, hold="HOLD_UNKNOWN")
        else:
            raise Rejected("host_history_kind")
    return state


SUPPORTED = frozenset(
    {
        "PREPARE",
        "BUILD_BACKEND",
        "BUILD_FRONTEND",
        "MIGRATE",
        "BACKUP",
        "RESTORE",
        "PREFLIGHT",
        "FREEZE_TIMERS",
        "DISABLE_RESTART",
        "STOP_BEAT",
        "STOP_WORKER",
        "STOP_API",
        "CLOSE_GATE",
        "FREEZE_PRODUCERS",
        "DRAIN",
        "FREEZE_VERIFY",
        "REPLACE_API",
        "REPLACE_WEB",
        "START_WORKER",
        "START_BEAT",
        "VERIFY_SCHEMA",
        "ACCEPT_INTERNAL",
        "VERIFY_WORKER",
        "VERIFY_JOBS",
        "OPEN_GATE",
        "VERIFY_OPEN",
        "RESTORE_RESTART",
        "RESTORE_TIMERS",
        "VERIFY_COMPLETE",
    }
)


async def _dispatch(name, spec, baseline, journal):
    """Finite side effects with pre/post full container identity checks.

    This is not a complete execution chain: backup, migration container ownership,
    complete gate acceptance and app starts remain pending. No pending step is success.
    """
    require(name in SUPPORTED, "host_step_pending")
    require_host_authority(spec)
    require(
        digest(baseline) == parse(spec._binding)["baseline_sha256"],
        "host_baseline_hash",
    )
    if name == "PREPARE":
        from host_build_control import prepare

        return await prepare(spec, journal)
    if name in ("BUILD_BACKEND", "BUILD_FRONTEND"):
        from host_build_control import build

        return await build(spec, journal, name.removeprefix("BUILD_").lower())
    if name == "MIGRATE":
        from host_migration_control import migrate

        return await migrate(spec, journal, baseline)
    if name in ("BACKUP", "RESTORE"):
        from host_backup_control import backup, restore

        if name == "BACKUP":
            from host_acceptance_control import capture_pre_migration

            captured = await capture_pre_migration(spec, journal, baseline)
            return await backup(
                spec, journal, baseline, schema_before_sha256=captured["receipt_sha256"]
            )
        return await restore(spec, journal, baseline)
    if name in ("VERIFY_SCHEMA", "ACCEPT_INTERNAL"):
        from host_acceptance_control import accept_internal, verify_schema

        return await (verify_schema if name == "VERIFY_SCHEMA" else accept_internal)(
            spec, journal, baseline
        )
    if name in ("REPLACE_API", "REPLACE_WEB", "START_WORKER", "START_BEAT"):
        from host_app_control import replace_app

        return await replace_app(spec, journal, baseline, name.split("_")[-1].lower())
    if name in ("VERIFY_WORKER", "VERIFY_JOBS"):
        from host_jobs_control import verify_jobs, verify_worker

        return await (verify_worker if name == "VERIFY_WORKER" else verify_jobs)(
            spec, journal, baseline
        )
    if name in (
        "OPEN_GATE",
        "VERIFY_OPEN",
        "RESTORE_RESTART",
        "RESTORE_TIMERS",
        "VERIFY_COMPLETE",
    ):
        from host_open_control import (
            open_gate,
            restore_restart,
            restore_timers,
            verify_complete,
            verify_open,
        )

        operation = {
            "OPEN_GATE": open_gate,
            "VERIFY_OPEN": verify_open,
            "RESTORE_RESTART": restore_restart,
            "RESTORE_TIMERS": restore_timers,
            "VERIFY_COMPLETE": verify_complete,
        }[name]
        return await operation(spec, journal, baseline)
    if name == "FREEZE_PRODUCERS":
        from host_producer_control import freeze

        return await freeze(spec, journal, baseline)
    if name in ("DRAIN", "FREEZE_VERIFY"):
        from host_drain_control import drain, freeze_verify

        return await (drain if name == "DRAIN" else freeze_verify)(
            spec, journal, baseline
        )
    if name == "STOP_API":
        from host_producer_control import terminate

        return await terminate(spec, journal, baseline)
    if name == "CLOSE_GATE":
        from host_gate_control import close_gate

        return await close_gate(spec, journal, baseline)
    if name == "PREFLIGHT":
        from host_preflight_control import preflight

        return await preflight(spec, journal, baseline)
    if name == "FREEZE_TIMERS":
        from host_timer_control import freeze_timers

        return await freeze_timers(spec, journal)
    if name in ("STOP_BEAT", "STOP_WORKER", "STOP_API"):
        from host_stop_control import stop

        return await stop(spec, journal, baseline, name.removeprefix("STOP_").lower())
    roles = (
        ROLES[:4]
        if name == "DISABLE_RESTART"
        else (name.removeprefix("STOP_").lower(),)
    )
    operation = "disable_restart" if name == "DISABLE_RESTART" else "stop"
    before_rows = {role: (await collect("inspect", role))["facts"] for role in roles}
    require(
        all(before_rows[role]["id"] == baseline[role] for role in roles),
        "host_container_replaced",
    )
    if operation == "disable_restart":
        from contracts import canonical

        journal.directory.create("original-restart.json", canonical(before_rows))
    results = {}
    for role in roles:
        before = before_rows[role]
        require(before["id"] == baseline[role], "host_container_replaced")
        argv = command(operation, role)
        # Mutate the inspected full immutable ID, not a recyclable container name.
        require(
            argv[-1] == "clothinkai-prod-" + role + "-20261018", "host_fixed_subject"
        )
        await _capture(
            argv[:-1] + (before["id"],), timeout=135 if operation == "stop" else 15
        )
        after = (await collect("inspect", role))["facts"]
        require(
            after["id"] == before["id"] and after["image"] == before["image"],
            "host_result_identity",
        )
        if operation == "stop":
            require(
                after["running"] is False
                and after["pid"] == 0
                and after["restarting"] is False,
                "host_container_exit_unknown",
            )
            # A stopped container is insufficient for full business/DB drain.
            raise Rejected("host_exact_backend_absence_pending")
        require(after["restart_policy"] == "no", "host_restart_readback")
        results[role] = after
    return {"original_sha256": digest(before_rows), "containers": results}


class HostCoordinator:
    def __init__(self, spec, journal, baseline):
        require(
            type(spec) in (HostMigrationSpec, HostReleaseSpec)
            and type(journal) is HostJournal,
            "host_coordinator_types",
        )
        validate_host_input(spec)
        exact(baseline, ROLES)
        for identity in baseline.values():
            sha(identity)
        require(
            journal.root == Path(ROOT) / "releases" / spec.data["release_id"] / "state",
            "host_journal_binding",
        )
        require(
            digest(baseline) == parse(spec._binding)["baseline_sha256"],
            "host_baseline_hash",
        )
        self.spec, self.journal, self.baseline = spec, journal, dict(baseline)

    async def run_next(self):
        require_host_authority(self.spec)
        # collect verifies Linux/root/instance/data-disk UUID before any lock write.
        await collect("inspect", "api")
        if self.journal.lock is None:
            self.journal.acquire()
        state = replay(self.spec, self.journal.read())
        if (
            len(state.completed) == len(STEPS)
            and state.pending is None
            and state.hold is None
        ):
            return {
                "status": "COMPLETE",
                "scope": "29_host_actions_recorded",
                "live_enabled": False,
                "pending": ["independent_external_vantage", "K7-4", "R33"],
            }
        action = next_action(self.spec, state)
        if not hasattr(action, "action_id"):
            return {"status": "HOLD", "reason": action.reason, "live_enabled": False}
        require(action.name == STEPS[len(state.completed)], "host_order")
        if action.name not in SUPPORTED:
            return {
                "status": "PENDING_IMPLEMENTATION",
                "step": action.name,
                "live_enabled": False,
            }
        # fsync append returns before the first process launch. Append failure
        # propagates with the lock retained and no dispatch. Unknown never replays.
        self.journal.append("intent", asdict(action))
        try:
            facts = await _dispatch(action.name, self.spec, self.baseline, self.journal)
            from contracts import canonical

            self.journal.directory.create(
                "step-" + action.name.lower() + "-facts.json", canonical(facts)
            )
            self.journal.append(
                "result",
                {
                    "action_id": action.action_id,
                    "input_sha256": self.spec.sha256,
                    "facts_sha256": digest(facts),
                    "completed": True,
                },
            )
            return {
                "status": "STEP_RECORDED",
                "step": action.name,
                "live_enabled": False,
            }
        except BaseException:
            try:
                self.journal.append(
                    "hold",
                    {"reason": "host_action_unknown", "action_id": action.action_id},
                )
            except BaseException:
                pass
            # No finally release: cleanup/DB ownership is not yet proved.
            return {
                "status": "HOLD_UNKNOWN",
                "step": action.name,
                "live_enabled": False,
            }
