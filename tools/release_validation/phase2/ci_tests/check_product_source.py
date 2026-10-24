"""Read-only reproduction of frozen path rejection. Emits paths/codes, never source."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from phase1_package_binding import PHASE1_PINS, frozen_contract  # noqa: E402


def check(repository, commit, tree):
    builder = frozen_contract()
    rejected = []
    names = builder.git(repository, "ls-tree", "-r", "-z", "--name-only", commit)
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = raw.decode()
        if builder.source_allowed(name) or name == "frontend/package-lock.json":
            try:
                builder.safe_name(name)
            except ValueError as exc:
                rejected.append({"path": name, "code": str(exc)})
    try:
        builder.source_files(repository, commit, tree)
    except ValueError as exc:
        outcome = {"source_verified": False, "code": str(exc)}
    else:
        outcome = {"source_verified": True, "code": None}
    return {
        "source_commit": commit,
        "source_tree": tree,
        "phase1_pins_sha256": PHASE1_PINS,
        "rejected_paths": rejected,
        **outcome,
        "deployable": False,
        "live_enabled": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tree", required=True)
    args = parser.parse_args()
    print(json.dumps(check(Path(args.repository), args.commit, args.tree), indent=2))
