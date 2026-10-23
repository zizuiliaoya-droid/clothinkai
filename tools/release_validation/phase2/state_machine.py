"""Pure transitions only; no paths, processes, sockets or live permit creation."""

from dataclasses import asdict, dataclass, replace

from contracts import AFTER, BEFORE, READ_ONLY, STEPS, digest, exact, require


@dataclass(frozen=True)
class State:
    completed: tuple = ()
    pending: str | None = None
    hold: str | None = None
    possible_writes: bool = False
    migration_intent: bool = False
    schema: str = BEFORE

    @classmethod
    def from_dict(cls, value):
        exact(value, asdict(cls()))
        require(type(value["completed"]) is list, "completed_type")
        return cls(**{**value, "completed": tuple(value["completed"])})


@dataclass(frozen=True)
class ActionSpec:
    name: str
    action_id: str
    input_sha256: str
    write: bool
    classification: str = "ACTION"


@dataclass(frozen=True)
class Hold:
    classification: str
    reason: str


def next_action(spec, state):
    require(state.completed == STEPS[: len(state.completed)], "history_order")
    if state.pending:
        return Hold("HOLD_UNKNOWN", "unclosed_intent")
    if state.hold:
        return Hold(state.hold, "recorded_failure")
    if len(state.completed) == len(STEPS):
        return Hold("COMPLETE", "synthetic_only")
    name = STEPS[len(state.completed)]
    return ActionSpec(
        name,
        spec.data["release_id"] + ":" + str(len(state.completed)) + ":" + name,
        spec.sha256,
        name not in READ_ONLY,
    )


def intent(spec, state, action):
    require(
        next_action(spec, state) == action and isinstance(action, ActionSpec),
        "unexpected_action",
    )
    return replace(
        state,
        pending=action.action_id,
        migration_intent=state.migration_intent or action.name == "MIGRATE",
        possible_writes=state.possible_writes
        or action.name in {"START_WORKER", "START_BEAT", "OPEN_GATE"},
    )


def recovery_eligible(state):
    return (
        not (state.possible_writes or state.migration_intent or state.pending)
        and state.schema == BEFORE
    )


def fixture_receipt(spec, action, facts):
    data = spec.data
    result = {
        "mode": "synthetic",
        "release_id": data["release_id"],
        "boot_id": data["boot_id"],
        "action_id": action.action_id,
        "input_sha256": spec.sha256,
        "collector_sha256": data["collector_sha256"],
        "baseline_sha256": data["baseline_sha256"],
        "started_tick": 1,
        "finished_tick": 2,
        "facts": facts,
    }
    return {**result, "sha256": digest(result)}


def result(spec, state, action, receipt, validator):
    require(state.pending == action.action_id, "result_without_intent")
    exact(
        receipt,
        (
            "mode",
            "release_id",
            "boot_id",
            "action_id",
            "input_sha256",
            "collector_sha256",
            "baseline_sha256",
            "started_tick",
            "finished_tick",
            "facts",
            "sha256",
        ),
    )
    wanted = fixture_receipt(spec, action, receipt["facts"])
    for key in (
        "mode",
        "release_id",
        "boot_id",
        "action_id",
        "input_sha256",
        "collector_sha256",
        "baseline_sha256",
    ):
        require(receipt[key] == wanted[key], "evidence_identity")
    require(
        type(receipt["started_tick"]) is int
        and type(receipt["finished_tick"]) is int
        and 0 <= receipt["started_tick"] <= receipt["finished_tick"],
        "evidence_time",
    )
    require(
        receipt["sha256"]
        == digest({k: v for k, v in receipt.items() if k != "sha256"}),
        "evidence_hash",
    )
    validator(action.name, receipt["facts"], state)
    return replace(
        state,
        pending=None,
        completed=state.completed + (action.name,),
        schema=AFTER if action.name == "VERIFY_SCHEMA" else state.schema,
    )
