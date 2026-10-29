"""复用现有受控注入/精确JUnit校验器，保留原flow7证据。"""

import argparse
import hashlib
import json
from pathlib import Path

from scripts import ci_flow7_faults as runner


def cases():
    runner.EXPECTED_MARKERS.update(
        G1="matrix_cell", G2="patch_whitelist", G3="stage_sql", G4="stage_sql"
    )
    parity = "tests/integration/test_stage_sql_parity.py::test_python_and_sql_agree_on_every_combo"
    return [
        (
            "G1",
            "tests/unit/test_flow_matrix.py::test_real_matrices_match_expected",
            [
                (
                    runner.PROMOTION + "flow_matrix.py",
                    "Cell(edit=(_PUSH,), read=(_WRITE,), reason=SHIP_PUSH_REASON, gates=SHIP_PUSH_GATES)",
                    "Cell(edit=(_WRITE,), read=(_WRITE,), reason=SHIP_PUSH_REASON, gates=SHIP_PUSH_GATES)",
                    None,
                )
            ],
        ),
        (
            "G2",
            "tests/unit/test_flow10_filters.py::test_fault_patch_whitelist",
            [
                (
                    "app/modules/flow/matrix.py",
                    "return WritableFields(managed=managed, allowed=allowed)",
                    "return WritableFields(managed=managed, allowed=managed)",
                    None,
                )
            ],
        ),
        (
            "G3",
            parity,
            [
                (
                    runner.PROMOTION + "stage_calculator.py",
                    "for rule in STAGE_RULES\n    ]",
                    "for rule in (*STAGE_RULES[:3], STAGE_RULES[4], STAGE_RULES[3], *STAGE_RULES[5:])\n    ]",
                    None,
                )
            ],
        ),
        (
            "G4",
            parity,
            [
                (
                    runner.PROMOTION + "stage_calculator.py",
                    "else _sql_literal(rule.name))",
                    'else _sql_literal(rule.name.replace("待主管审核", "错误阶段")))',
                    None,
                )
            ],
        ),
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", action="store_true", help="本机仅执行无数据库的G1/G2")
    args = parser.parse_args()
    if not args.unit:
        runner.environment()
    runner.ARTIFACTS.mkdir(exist_ok=True)
    results = []
    for name, node, edits in cases()[:2] if args.unit else cases():
        originals = {path: Path(path).read_bytes() for path, _, _, _ in edits}
        try:
            for path, old, new, method in edits:
                runner.edit(path, old, new, method=method)
            red = runner.run_test(node, name + "-red", red=True)
        finally:
            for path, content in originals.items():
                Path(path).write_bytes(content)
                assert (
                    hashlib.sha256(Path(path).read_bytes()).digest()
                    == hashlib.sha256(content).digest()
                )
        green = runner.run_test(node, name + "-green", red=False)
        results.append(
            {"name": name, "passed": True, "red": red, "green": green, "source_restored": True}
        )
        output = "flow10-unit-faults.json" if args.unit else "flow10-faults.json"
        (runner.ARTIFACTS / output).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps({"mutations": len(results), "passed": True}))


if __name__ == "__main__":
    main()
