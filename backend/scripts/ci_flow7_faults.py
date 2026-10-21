"""GitHub临时副本内逐项故障注入：必须业务断言红、原字节恢复后绿。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci_flow7_pg18 import ARTIFACTS, environment

PROMOTION = "app/modules/promotion/"
COMPLETION = "tests/integration/test_promotion_completion.py"
EXPECTED_MARKERS = {
    "E1_scope": "scope",
    "E2_beijing": "beijing",
    "E5_idempotent": "completion_repeated",
    "E6_metrics_once": "metrics_once",
    "collector_freeze": "collector",
    "import_completed": "import",
    "stage_sql": "stage_sql",
    "E4_retro_order": "completion_order",
    "matrix_revise": "matrix_revise",
    **{
        f"E3_{step}": "completion_missing"
        for step in ("review", "chat", "metrics", "retro", "recall")
    },
}


def validate_junit(xml: Path, node: str, fault: str, *, red: bool, returncode: int) -> int:
    """仅接受精确选中的业务断言；测试体内异常同样是failure，不能只数XML标签。"""
    cases = ET.parse(xml).findall(".//testcase")  # noqa: S314 本次pytest自己生成的报告
    assert len(cases) == 1, "每个注入必须精确选中一个case"
    case = cases[0]
    path, *parts = node.split("::")
    assert case.attrib["name"] == parts[-1], "报告case不是预期node"
    expected_class = path.removesuffix(".py").replace("/", ".")
    if len(parts) > 1:
        expected_class += "." + ".".join(parts[:-1])
    assert case.attrib["classname"] == expected_class, "报告case来源不是预期模块/类"
    assert case.find("error") is None and case.find("skipped") is None
    failures = case.findall("failure")
    if red:
        assert returncode == 1 and len(failures) == 1
        marker = "FLOW7_FAULT_EXPECTED:" + EXPECTED_MARKERS[fault]
        failure = failures[0]
        message = failure.attrib.get("message", "").removeprefix("AssertionError: ")
        assert message == marker or message.startswith(marker + "\n"), (
            "非预期业务断言，禁止以驱动/类型/其它断言异常充当红灯",
            message,
        )
        assert f"AssertionError: {marker}" in (failure.text or ""), "必须是真实AssertionError"
    else:
        assert returncode == 0 and not failures
    return len(cases)


def edit(path: str, old: str, new: str, *, method: str | None = None) -> None:
    file = Path(path)
    source = file.read_text(encoding="utf-8")
    start = source.index(f"    async def {method}(") if method else 0
    end = source.find("\n    async def ", start + 1) if method else -1
    if end < 0:
        end = len(source)
    section = source[start:end]
    assert section.count(old) == 1, (path, method, "注入原文需唯一命中")
    file.write_text(source[:start] + section.replace(old, new) + source[end:], encoding="utf-8")


def run_test(node: str, label: str, *, red: bool) -> dict:
    xml = ARTIFACTS / f"fault-{label}.xml"
    with (ARTIFACTS / f"fault-{label}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(  # noqa: S603 本脚本固定pytest/nodeid，无shell或外部命令
            [sys.executable, "-m", "pytest", "-q", node, "--tb=short", f"--junitxml={xml}"],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    fault = label.removesuffix("-red").removesuffix("-green")
    tests = validate_junit(xml, node, fault, red=red, returncode=result.returncode)
    return {
        "name": label,
        "tests": tests,
        "failures": int(red),
        "expected_marker": EXPECTED_MARKERS[fault] if red else None,
        "exit_code": result.returncode,
    }


def fault_cases() -> list:
    scope_node = "tests/integration/test_retrospective.py::TestRetrospectiveFlow::test_independent_scope_http_forbids_old_wildcards[pr]"
    faults = [
        (
            "E1_scope",
            scope_node,
            [
                (
                    PROMOTION + "api.py",
                    'require_permission("promotion_retro", "write")',
                    'require_permission("promotion.retro", "write")',
                    None,
                ),
                (
                    PROMOTION + "service_parts/retro.py",
                    'actor.perms.has("promotion_retro", "write")',
                    'actor.perms.has("promotion.retro", "write")',
                    "write_retrospective",
                ),
            ],
        ),
        (
            "E2_beijing",
            "tests/integration/test_metrics_gate.py::TestGate::test_day_seven_succeeds_beijing_midnight",
            [
                (
                    PROMOTION + "service_parts/retro.py",
                    "today = get_today()",
                    "today = __import__('datetime').date.today()",
                    "record_metrics",
                ),
            ],
        ),
        (
            "E5_idempotent",
            COMPLETION + "::test_every_last_step_completes_once[retro]",
            [
                (
                    PROMOTION + "repository.py",
                    "Promotion.completed_at.is_(None),",
                    "",
                    "maybe_complete",
                ),
            ],
        ),
        (
            "E6_metrics_once",
            "tests/integration/test_metrics_gate.py::TestRecordOnceConcurrent::test_two_connections_only_one_succeeds",
            [
                (
                    PROMOTION + "repository.py",
                    "Promotion.metrics_recorded_at.is_(None),",
                    "",
                    "record_metrics_once",
                ),
            ],
        ),
        (
            "collector_freeze",
            COMPLETION + "::test_completed_admin_edit_does_not_reopen_and_collector_freezes",
            [
                (
                    PROMOTION + "repository.py",
                    "Promotion.completed_at.is_(None),",
                    "",
                    "update_like_count",
                ),
            ],
        ),
        (
            "import_completed",
            COMPLETION + "::test_completed_import_rejects_real_export_row_even_for_admin",
            [
                (
                    "app/modules/importer/adapters/settlement.py",
                    "if promo is None or promo.completed_at is not None:",
                    "if promo is None:",
                    "upsert",
                ),
            ],
        ),
        (
            "stage_sql",
            "tests/integration/test_stage_sql_parity.py::test_python_and_sql_agree_on_every_combo",
            [
                (
                    PROMOTION + "stage_calculator.py",
                    '"completed_at IS NOT NULL"',
                    '"completed_at IS NULL"',
                    None,
                ),
            ],
        ),
    ]
    for last, file, method in (
        ("review", "review.py", "review"),
        ("chat", "settlement_link.py", "upload_settlement_chat"),
        ("metrics", "retro.py", "record_metrics"),
        ("retro", "retro.py", "write_retrospective"),
        ("recall", "recall.py", "_recall_finish"),
    ):
        # 保留if分支合法：用pass替换调用，不能以IndentationError冒充业务红。
        faults.append(
            (
                f"E3_{last}",
                COMPLETION + f"::test_every_last_step_completes_once[{last}]",
                [
                    (
                        PROMOTION + "service_parts/" + file,
                        "await self.maybe_complete(promotion_id, user)",
                        "pass",
                        method,
                    ),
                ],
            )
        )
    faults.extend(
        [
            (
                "E4_retro_order",
                "tests/integration/test_retro_completion_concurrency.py::test_last_steps_and_completed_gate_are_serialized[chat]",
                [
                    (
                        PROMOTION + "service_parts/retro.py",
                        "await self.maybe_complete(promotion_id, user)",
                        "pass",
                        "write_retrospective",
                    ),
                    (
                        PROMOTION + "service_parts/retro.py",
                        "updated = await self._repo.update_state(",
                        "await self.maybe_complete(promotion_id, user)\n        updated = await self._repo.update_state(",
                        "write_retrospective",
                    ),
                ],
            ),
            (
                "matrix_revise",
                "tests/unit/test_flow_matrix.py::test_flow7_retro_revision_requires_true_star",
                [
                    (
                        PROMOTION + "flow_matrix.py",
                        '_fill(_retro_revise, "DEFH", Cell(edit=(_STAR,)))',
                        '_fill(_retro_revise, "DEFH", Cell(edit=(_WRITE,)))',
                        None,
                    ),
                ],
            ),
        ]
    )
    return faults


def main() -> None:
    environment()
    ARTIFACTS.mkdir(exist_ok=True)
    faults = fault_cases()
    results = []
    for name, node, edits in faults:
        originals = {path: Path(path).read_bytes() for path, _, _, _ in edits}
        try:
            for path, old, new, method in edits:
                edit(path, old, new, method=method)
            red = run_test(node, name + "-red", red=True)
        finally:
            for path, content in originals.items():
                Path(path).write_bytes(content)
                assert (
                    hashlib.sha256(Path(path).read_bytes()).digest()
                    == hashlib.sha256(content).digest()
                )
        green = run_test(node, name + "-green", red=False)
        results.append(
            {"name": name, "passed": True, "red": red, "green": green, "source_restored": True}
        )
        (ARTIFACTS / "flow7-faults.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
    print(json.dumps({"mutations": len(results), "passed": True}))


if __name__ == "__main__":
    main()
