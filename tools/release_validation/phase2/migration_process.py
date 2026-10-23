"""Synthetic-only parent: owns host update lock, never the DDL advisory key.

No subprocess/DB/container implementation exists in this candidate. An injected
transport supplies all observations. Uncertain ownership retains the host lock
and strong references to owned tasks; it never retries, unlocks or opens traffic.
"""

import asyncio
import re

from sql_migration_driver import validate_evidence, validate_identity
from sql_policy import (
    AFTER,
    BEFORE,
    SqlRejected,
    fields,
    need,
    safe_failure,
    validate_spec,
)

# Retain uncertain owned operations for reconciliation; deliberately no retry API.
# A future live adapter additionally needs durable host journal/lock ownership.
_HELD = {}


class SyntheticTransport:
    execution_mode = "synthetic"

    async def acquire_host_lock(self, spec):
        raise SqlRejected("transport_unimplemented")

    async def spawn(self, spec, host_lock):
        raise SqlRejected("transport_unimplemented")

    async def collect(self, child, spec):
        raise SqlRejected("transport_unimplemented")

    async def inspect_backend(self, identity, spec):
        raise SqlRejected("transport_unimplemented")

    async def inspect_container(self, child, spec):
        raise SqlRejected("transport_unimplemented")

    async def release_host_lock(self, host_lock, spec):
        raise SqlRejected("transport_unimplemented")


async def _until(task, deadline):
    """Timeout/caller cancellation does not discard or cancel the owned task."""
    remaining = max(0, deadline - asyncio.get_running_loop().time())
    done, _pending = await asyncio.wait({task}, timeout=remaining)
    if task not in done:
        raise TimeoutError
    return task.result()


def _task(state, name, coroutine):
    if asyncio.get_running_loop().time() >= state["deadline"]:
        coroutine.close()
        raise TimeoutError
    task = asyncio.create_task(coroutine)
    state["tasks"][name] = task
    return task


def _valid_host_lock(value, spec):
    fields(value, {"policy_sha256", "token", "exclusive", "kind"}, "host_lock_fields")
    need(
        value["policy_sha256"] == spec.sha256
        and value["exclusive"] is True
        and value["kind"] == "host-update-lock"
        and type(value["token"]) is str
        and re.fullmatch(
            r"linux-update-[0-9]+-[0-9]+"
            if spec.data["mode"] == "linux_ci"
            else r"fixture-update-[0-9]{3,12}",
            value["token"],
        ),
        "host_lock_identity",
    )


def _valid_child(child, spec):
    need(
        type(child.child_id) is str
        and re.fullmatch(
            r"linux-child-[0-9]+"
            if spec.data["mode"] == "linux_ci"
            else r"fixture-child-[0-9]{3,12}",
            child.child_id,
        )
        and type(child.container_id) is str
        and (
            child.container_id == "ci-process-" + spec.data["nonce"]
            if spec.data["mode"] == "linux_ci"
            else re.fullmatch(r"fixture-container-[0-9]{3,12}", child.container_id)
        ),
        "child_identity",
    )


async def _cleanup(state, spec, transport, deadline):
    """One cleanup owner. All calls and exits are bounded by the overall deadline."""
    tasks = state["tasks"]
    if "host_lock" in tasks:
        state["host_lock"] = await _until(tasks["host_lock"], deadline)
        _valid_host_lock(state["host_lock"], spec)
    if "spawn" not in tasks:
        # Even failed spawn/lock acquisition needs an explicit transport no-child
        # attestation; this minimal candidate has no such attestation => HOLD.
        return
    child = state["child"] = await _until(tasks["spawn"], deadline)
    _valid_child(child, spec)
    wait = tasks.get("wait")
    if wait is None:
        wait = _task(state, "wait", child.wait())
    if child.returncode is None:
        try:
            child.terminate()
        except ProcessLookupError:
            pass
        grace_end = min(
            deadline,
            asyncio.get_running_loop().time()
            + spec.data["timeouts"]["terminate_seconds"],
        )
        try:
            await _until(wait, grace_end)
        except TimeoutError:
            try:
                child.kill()
            except ProcessLookupError:
                pass
    await _until(wait, deadline)
    need(type(child.returncode) is int, "child_exit_unconfirmed")
    state["exits"]["process"] = True
    snapshot = await _until(
        _task(state, "collect", transport.collect(child, spec)), deadline
    )
    fields(
        snapshot,
        {"policy_sha256", "identity", "proof", "post_head", "failure"},
        "snapshot_fields",
    )
    need(snapshot["policy_sha256"] == spec.sha256, "snapshot_binding")
    identity = validate_identity(snapshot["identity"], spec)
    state["snapshot"] = snapshot
    container = await _until(
        _task(state, "container", transport.inspect_container(child, spec)), deadline
    )
    fields(
        container,
        {"policy_sha256", "container_id", "state", "after_process_exit"},
        "container_fields",
    )
    need(
        container
        == {
            "policy_sha256": spec.sha256,
            "container_id": child.container_id,
            "state": "not_applicable" if spec.data["mode"] == "linux_ci" else "stopped",
            "after_process_exit": True,
        }
        and container["after_process_exit"] is True,
        "container_exit_unconfirmed",
    )
    state["exits"]["container"] = True
    backend = await _until(
        _task(state, "backend", transport.inspect_backend(identity, spec)), deadline
    )
    fields(
        backend,
        {
            "policy_sha256",
            "identity",
            "state",
            "after_process_exit",
            "owned_lock_absent",
        },
        "backend_fields",
    )
    need(
        backend
        == {
            "policy_sha256": spec.sha256,
            "identity": identity,
            "state": "absent",
            "after_process_exit": True,
            "owned_lock_absent": True,
        }
        and backend["after_process_exit"] is True
        and backend["owned_lock_absent"] is True,
        "backend_exit_unconfirmed",
    )
    state["exits"]["backend"] = True
    proof = snapshot["proof"]
    need(proof["identity"] == identity, "snapshot_backend_mismatch")
    if child.returncode == 0 and not state["cancelled"] and not state["timed_out"]:
        safe_proof = validate_evidence(proof, spec)
        need(
            snapshot["post_head"] == AFTER and snapshot["failure"] is None,
            "snapshot_success",
        )
        outcome = "verified"
    else:
        safe_proof = validate_evidence(proof, spec, complete=False)
        need(
            snapshot["post_head"] == BEFORE and proof["post_head"] in (None, BEFORE),
            "failure_head_unknown",
        )
        fields(snapshot["failure"], {"code", "sqlstate"}, "failure_fields")
        need(
            snapshot["failure"]["code"]
            in ("guard_rejected", "execution_failed", "cancelled", "timeout"),
            "failure_code",
        )
        sqlstate = snapshot["failure"]["sqlstate"]
        need(
            sqlstate is None
            or (type(sqlstate) is str and re.fullmatch(r"[0-9A-Z]{5}", sqlstate)),
            "failure_sqlstate",
        )
        state["failure"] = snapshot["failure"]
        outcome = "failed_known"
    state["safe_proof"] = safe_proof
    state["safe_head"] = snapshot["post_head"]
    # Release only after exact process/container/backend absence AND known head.
    release = await _until(
        _task(state, "release", transport.release_host_lock(state["host_lock"], spec)),
        deadline,
    )
    need(release is True, "host_unlock_unconfirmed")
    state["host_lock_released"] = True
    # Cancellation may arrive during confirmed-quiescent host unlock. The unlock
    # cannot be undone, but the caller must still receive HOLD, never verified.
    state["outcome"] = (
        "unknown" if state["cancelled"] and outcome == "verified" else outcome
    )


async def run_guarded_migration(spec, transport):
    loop = asyncio.get_running_loop()
    start = loop.time()
    state = {
        "tasks": {},
        "child": None,
        "host_lock": None,
        "outcome": "unknown",
        "cancelled": False,
        "timed_out": False,
        "host_lock_released": False,
        "exits": {"process": False, "container": False, "backend": False},
        "snapshot": None,
        "safe_proof": None,
        "safe_head": None,
        "failure": None,
    }
    valid = False
    try:
        spec = validate_spec(spec)
        if spec.data["mode"] == "linux_ci":
            from linux_transport import LinuxCITransport

            need(
                type(transport) is LinuxCITransport and transport.spec == spec,
                "linux_ci_transport_required",
            )
        else:
            need(
                isinstance(transport, SyntheticTransport)
                and transport.execution_mode == "synthetic",
                "synthetic_transport_only",
            )
        need(spec.sha256 not in _HELD, "prior_operation_held")
        valid = True
        state["transport"], state["spec"] = transport, spec
        _HELD[spec.sha256] = state
        deadline = start + spec.data["timeouts"]["overall_seconds"]
        state["deadline"] = deadline
        lock_task = _task(state, "host_lock", transport.acquire_host_lock(spec))
        host_lock = await _until(lock_task, deadline)
        _valid_host_lock(host_lock, spec)
        state["host_lock"] = host_lock
        spawn = _task(state, "spawn", transport.spawn(spec, host_lock))
        child = state["child"] = await _until(spawn, deadline)
        _valid_child(child, spec)
        wait = _task(state, "wait", child.wait())
        child_end = min(deadline, loop.time() + spec.data["timeouts"]["child_seconds"])
        await _until(wait, child_end)
    except asyncio.CancelledError:
        state["cancelled"] = True
        state["failure"] = {"code": "cancelled", "sqlstate": None}
    except TimeoutError:
        state["timed_out"] = True
        state["failure"] = {"code": "timeout", "sqlstate": None}
    except Exception as exc:
        state["failure"] = safe_failure(exc)
    if valid:
        cleanup = asyncio.create_task(_cleanup(state, spec, transport, deadline))
        state["tasks"]["cleanup"] = cleanup
        # Repeated caller cancellation cannot start a second cleanup or abandon it.
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                state["cancelled"] = True
            except Exception:
                break
        try:
            cleanup.result()
        except BaseException as exc:
            state["failure"] = safe_failure(exc)
            state["outcome"] = "unknown"
        if state["outcome"] != "unknown":
            _HELD.pop(spec.sha256, None)
    end = loop.time()
    return {
        "mode": spec.data["mode"] if valid else "rejected",
        "execution_scope": "linux_process_only"
        if valid and spec.data["mode"] == "linux_ci"
        else "synthetic",
        "live_enabled": False,
        "deployable": False,
        "outcome": state["outcome"],
        "action": "HOLD"
        if state["outcome"] != "verified"
        else (
            "LINUX_CI_VERIFIED"
            if spec.data["mode"] == "linux_ci"
            else "SYNTHETIC_VERIFIED"
        ),
        "policy_sha256": spec.sha256 if valid else None,
        "cancelled": state["cancelled"],
        "timed_out": state["timed_out"],
        "owned_exit": state["exits"],
        "host_lock_released": state["host_lock_released"],
        "proof": state["safe_proof"],
        "post_head": state["safe_head"],
        "failure": state["failure"],
        "interval": {
            "start_monotonic": start,
            "end_monotonic": end,
            "elapsed_seconds": end - start,
        },
    }
