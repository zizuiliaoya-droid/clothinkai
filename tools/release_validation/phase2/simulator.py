"""Bounded local synthetic scenarios only; all files live in an owned temporary sandbox."""

from pathlib import Path
import tempfile
from contracts import STEPS, exact, require, validate_input
from adapters import Runner, reconcile_fixture
from journal import Journal
from synthetic_world import WorldAdapter


def simulate(data):
    exact(data, ("schema_version", "mode", "release", "fail_action", "fail_at"))
    require(
        data["schema_version"] == 1
        and type(data["schema_version"]) is int
        and data["mode"] == "synthetic",
        "scenario_mode",
    )
    require(
        (data["fail_action"] is None and data["fail_at"] is None)
        or (
            data["fail_action"] in STEPS
            and data["fail_at"]
            in {
                "before_intent",
                "after_intent",
                "after_effect",
                "before_result",
                "after_result",
            }
        ),
        "scenario_fault",
    )
    spec = validate_input(data["release"])
    with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as folder:
        root = Path(folder)
        journal = Journal(root)
        adapter = WorldAdapter(root, spec)
        runner = Runner(spec, journal, adapter, adapter.world.validate)
        try:
            for name in STEPS:
                runner.fail_at = (
                    data["fail_at"] if name == data["fail_action"] else None
                )
                runner.step()
                if runner.state.hold:
                    break
            reconciled = reconcile_fixture(journal, spec)
            world = adapter.world.data
            return {
                "mode": "synthetic",
                "live_enabled": False,
                "deployable": False,
                "status": runner.state.hold or "SYNTHETIC_COMPLETE",
                "completed_steps": len(runner.state.completed),
                "effect_steps": len(world["history"]),
                "schema": world["schema"],
                "gate": world["gate"],
                "possible_writes": runner.state.possible_writes,
                "migration_count": world["migration_count"],
                "pending_business_samples": world["business_pending"],
                "reconcile": reconciled,
                "sql_runtime_executed": False,
                "linux_execution_implemented": False,
            }
        finally:
            runner.close_after_confirmation()
