"""Local candidate CLI. No live mode, host detection, network or process launcher."""

import argparse
import json
from pathlib import Path

from contracts import Rejected, STEPS, parse_json, require, validate_input
from linux_adapter import integration_blockers


def plan(spec):
    return {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "input_sha256": spec.sha256,
        "completed": {name: False for name in STEPS},
        "live_blockers": list(integration_blockers()),
    }


def dispatch(command, data):
    require(command in {"plan", "simulate", "reconcile-fixture"}, "command_disabled")
    if command == "plan":
        return plan(validate_input(data))
    if command == "simulate":
        from simulator import simulate

        return simulate(data)
    raise Rejected("reconcile_requires_owned_sandbox_api_not_arbitrary_path")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="plan",
        choices=("plan", "simulate", "reconcile-fixture"),
    )
    parser.add_argument("--input", default="tests/fixtures/release.synthetic.json")
    parser.add_argument("--scenario", default=None)
    args = parser.parse_args(argv)
    try:
        path = Path(args.scenario or args.input).absolute()
        allowed = Path(__file__).parent.resolve() / "tests/fixtures"
        require(
            path.resolve().is_relative_to(allowed)
            and path.name.endswith(".synthetic.json")
            and not path.is_symlink()
            and not path.is_junction(),
            "fixture_input_only",
        )
        for part in (path.parent, *path.parents):
            require(
                not part.is_symlink() and not part.is_junction(), "fixture_parent_link"
            )
        require(path.stat().st_size <= 1024 * 1024, "input_limit")
        result = dispatch(args.command, parse_json(path.read_bytes()))
        print(json.dumps(result, sort_keys=True))
        return 1 if str(result.get("status", "")).startswith("HOLD") else 0
    except (Rejected, OSError) as exc:
        print(
            json.dumps(
                {
                    "mode": "synthetic",
                    "live_enabled": False,
                    "deployable": False,
                    "status": "HOLD",
                    "code": str(exc)
                    if isinstance(exc, Rejected)
                    else type(exc).__name__,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
