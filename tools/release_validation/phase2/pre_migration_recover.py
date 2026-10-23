"""Synthetic pre066 recovery rehearsal only; no Linux recovery executor."""

import os
from contracts import Rejected, canonical, digest, require
from journal import Journal, regular, sandbox_root, sync_parent
from recovery_plan import propose


def simulate(root, state, facts, fail_after=None):
    root = sandbox_root(root)
    advice = propose(state, facts)
    result = {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "status": advice["branch"],
        "gate": "closed",
        "possible_writes": False,
        "actions": [],
    }
    if advice["branch"] != "PRE066_RECOVERY_ELIGIBLE":
        return result
    worldpath = root / "recovery-world.json"
    regular(worldpath)
    journal = Journal(root)
    if worldpath.exists() or journal.read():
        return {**result, "status": "HOLD_UNKNOWN"}
    require(
        fail_after is None or fail_after in advice["proposed_order"],
        "recovery_failure_point",
    )
    world = {
        "gate": "closed",
        "api": 0,
        "worker": 0,
        "beat": 0,
        "controls": "old-pinned",
        "database_identity": "fixture-original-db",
        "redis_identity": "fixture-original-redis",
        "scheduler_identity": "fixture-original-schedule",
        "accepted": False,
        "operations_restored": False,
        "possible_writes": False,
        "actions": [],
    }
    journal.acquire()
    try:
        for index, action in enumerate(advice["proposed_order"]):
            action_id = "fixture-recovery:" + str(index) + ":" + action
            journal.append(
                "intent",
                {
                    "action_id": action_id,
                    "name": action,
                    "baseline_sha256": digest(facts),
                },
            )
            if action == "CLOSE_GATE":
                world["gate"] = "closed"
            elif action == "RESTORE_OLD_CONFIG":
                require(world["gate"] == "closed", "recovery_gate")
                world["controls"] = "old-pinned-restored"
            elif action == "START_OLD_API":
                require(
                    world["controls"] == "old-pinned-restored" and world["api"] == 0,
                    "recovery_api",
                )
                world["api"] = 1
            elif action == "START_UNIQUE_OLD_WORKER":
                require(world["api"] == 1 and world["worker"] == 0, "recovery_worker")
                world["worker"] = 1
                world["possible_writes"] = True
            elif action == "START_UNIQUE_OLD_BEAT":
                require(world["worker"] == 1 and world["beat"] == 0, "recovery_beat")
                world["beat"] = 1
                world["possible_writes"] = True
            elif action == "ACCEPT_OLD_INTERNAL":
                require(
                    world["api"] == world["worker"] == world["beat"] == 1
                    and world["gate"] == "closed",
                    "recovery_acceptance",
                )
                world["accepted"] = True
            elif action == "RESTORE_OLD_OPERATIONS":
                require(world["accepted"], "recovery_operations")
                world["operations_restored"] = True
            elif action == "OPEN_OLD_AFTER_ACCEPTANCE":
                require(
                    world["accepted"] and world["operations_restored"], "recovery_open"
                )
                world["gate"] = "open-old"
            world["actions"].append(action)
            temporary = root / "recovery-world.pending"
            regular(temporary)
            with temporary.open("xb") as output:
                output.write(canonical(world))
                output.flush()
                os.fsync(output.fileno())
            regular(worldpath)
            os.replace(temporary, worldpath)
            sync_parent(root)
            if fail_after == action:
                raise Rejected("synthetic_result_lost")
            journal.append(
                "result", {"action_id": action_id, "world_sha256": digest(world)}
            )
        return {**result, **world, "status": "SYNTHETIC_COMPLETE"}
    except BaseException:
        try:
            journal.append("hold", {"code": "recovery_unresolved"})
        except BaseException:
            pass
        return {**result, **world, "status": "HOLD_UNKNOWN"}
    finally:
        # This simulator creates no owned process; Linux exit confirmation is unimplemented.
        journal.release()
