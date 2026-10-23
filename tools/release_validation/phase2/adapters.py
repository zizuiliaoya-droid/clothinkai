"""Synthetic adapter and journal coordinator. No real process or socket access."""

from dataclasses import replace
import os

from contracts import Rejected, STEPS, canonical, digest, parse_json, require
from journal import regular, sandbox_root
from state_machine import (
    ActionSpec,
    State,
    fixture_receipt,
    intent,
    next_action,
    result,
)


class SyntheticAdapter:
    def __init__(self, root, spec, facts):
        self.root = sandbox_root(root)
        self.spec, self.facts = spec, parse_json(canonical(facts))
        self.events = []
        self.exit_confirmed = True
        self.effects = self.root / "synthetic-effects.jsonl"

    def execute(self, action):
        require(
            type(action) is ActionSpec
            and action.name in STEPS
            and action.input_sha256 == self.spec.sha256,
            "closed_action",
        )
        regular(self.effects)
        previous = (
            self.effects.read_bytes().splitlines() if self.effects.exists() else []
        )
        require(
            all(parse_json(row)["action_id"] != action.action_id for row in previous),
            "action_replay",
        )
        with self.effects.open("ab") as output:
            output.write(
                canonical({"mode": "synthetic", "action_id": action.action_id}) + b"\n"
            )
            output.flush()
            os.fsync(output.fileno())
        self.events.append(action.name + ":effect")

    def observe(self, action):
        require(action.name in self.facts, "missing_observation")
        self.events.append(action.name + ":observe")
        return fixture_receipt(self.spec, action, self.facts[action.name])

    def confirm_owned_stopped(self):
        # Synthetic exit evidence only; never interpreted as a real Docker process result.
        return self.exit_confirmed


class Runner:
    def __init__(self, spec, journal, adapter, validator, fail_at=None):
        from synthetic_world import WorldAdapter

        require(
            type(adapter) in (SyntheticAdapter, WorldAdapter),
            "synthetic_adapter_required",
        )
        require(
            adapter.root == journal.root and adapter.spec == spec, "adapter_binding"
        )
        self.spec, self.journal, self.adapter, self.state = (
            spec,
            journal,
            adapter,
            State(),
        )
        self.validator, self.fail_at = validator, fail_at
        self.reloaded = False
        try:
            if journal.read():
                self.reloaded = True
                self.state = replace(self.state, hold="HOLD_UNKNOWN")
        except Rejected:
            self.reloaded = True
            self.state = replace(self.state, hold="HOLD_UNKNOWN")

    def fault(self, point):
        if self.fail_at == point:
            raise Rejected("injected_" + point)

    def step(self):
        action = next_action(self.spec, self.state)
        if not isinstance(action, ActionSpec):
            return action
        if self.journal.lock is None:
            self.journal.acquire()
        try:
            self.fault("before_intent")
            self.journal.append(
                "intent",
                {
                    "action_id": action.action_id,
                    "name": action.name,
                    "boot_id": self.spec.data["boot_id"],
                    "input_sha256": self.spec.sha256,
                    "collector_sha256": self.spec.data["collector_sha256"],
                },
            )
            self.state = intent(self.spec, self.state, action)
            self.fault("after_intent")
            self.adapter.execute(action)
            self.fault("after_effect")
            receipt = self.adapter.observe(action)
            self.fault("before_result")
            proposed = result(self.spec, self.state, action, receipt, self.validator)
            self.journal.append(
                "result", {"action_id": action.action_id, "receipt": receipt}
            )
            self.fault("after_result")
            self.state = proposed
        except BaseException:
            # Never advance, restart old applications or retry a possibly executed action.
            self.state = replace(self.state, hold="HOLD_UNKNOWN")
            try:
                self.journal.append(
                    "hold",
                    {
                        "action_id": action.action_id,
                        "code": "operation_unresolved",
                        "input_sha256": self.spec.sha256,
                    },
                )
            except BaseException:
                # An unavailable journal cannot establish a safe resume point.
                pass
        finally:
            if self.adapter.confirm_owned_stopped() and (
                self.state.hold is not None or len(self.state.completed) == len(STEPS)
            ):
                self.journal.release()
        return self.state

    def close_after_confirmation(self):
        require(self.adapter.confirm_owned_stopped(), "owned_exit_unknown")
        self.journal.release()


def reconcile_fixture(journal, spec):
    """Read-only classification; no receipts become execution capability."""
    rows = journal.read()
    outstanding, seen = set(), set()
    mismatch = False
    hold_recorded = False
    for row in rows:
        payload = row["payload"]
        if row["kind"] == "intent":
            key = payload["action_id"]
            require(key not in seen, "duplicate_intent")
            seen.add(key)
            outstanding.add(key)
            mismatch |= (
                payload["boot_id"] != spec.data["boot_id"]
                or payload["input_sha256"] != spec.sha256
            )
        elif row["kind"] == "result":
            require(payload["action_id"] in outstanding, "orphan_result")
            receipt = payload.get("receipt")
            require(
                type(receipt) is dict
                and receipt.get("action_id") == payload["action_id"]
                and receipt.get("input_sha256") == spec.sha256
                and receipt.get("boot_id") == spec.data["boot_id"]
                and receipt.get("sha256")
                == digest({k: v for k, v in receipt.items() if k != "sha256"}),
                "reconcile_receipt_binding",
            )
            outstanding.remove(payload["action_id"])
        elif row["kind"] == "hold":
            hold_recorded = True
    return {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "classification": "HOLD_UNKNOWN"
        if outstanding or mismatch or hold_recorded
        else "READ_ONLY_RECONCILED",
        "unclosed_actions": len(outstanding),
        "boot_or_input_changed": mismatch,
        "automatic_replay": False,
        "next_write": None,
    }
