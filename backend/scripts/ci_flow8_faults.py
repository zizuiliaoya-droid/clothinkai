"""PR8临时CI副本故障注入；精确业务断言红，源码字节还原且同node恢复绿。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci_flow7_faults import edit
from scripts.ci_flow7_pg18 import ARTIFACTS, environment

P = "app/modules/promotion/"
G = "tests/integration/test_return_freight_guards.py::"
EXPECTED_MARKERS = {
    "D8": "published_not_complete",
    "D9": "no_service_payment",
    "D10": "completed_resubmit",
    "D11": "pending_fee_lock",
    "D14": "zero_due_recall_qr",
    "D15": "uncompleted_rejected_qr",
    "D16": "cancelled_completed_qr",
    "completed_auto": "completed_auto_freight",
}


def fault_cases() -> list:
    qr = P + "service_parts/payment_qr.py"
    exception = 'exception = promotion.recall_status == "召回中" or freight_status == "已驳回"'
    return [
        (
            "D8",
            "tests/integration/test_recall_closure.py::test_published_recall_does_not_complete_without_normal_requirements",
            [
                (
                    P + "repository.py",
                    'and_(Promotion.publish_status == "已取消", Promotion.recall_status == "召回成功")',
                    'and_(Promotion.recall_status == "召回成功")',
                    "maybe_complete",
                )
            ],
        ),
        (
            "D9",
            "tests/integration/test_return_freight_reads.py::test_freight_paid_does_not_advance_service_settlement",
            [
                (
                    P + "freight_service.py",
                    "            # R33只写自己主线事件，绝不发送SettlementPaid或修改服务费结款链。",
                    """            from app.core.events import dispatch
            from app.modules.finance.events import SettlementPaid
            await dispatch(SettlementPaid(event_id=uuid4(), timestamp=datetime.now(UTC), tenant_id=user.tenant_id, settlement_id=freight.id, promotion_id=context.promotion.id, payment_amount=freight.amount, payment_date=get_today(), paid_by=user.id), session=self._session)""",
                    "pay",
                )
            ],
        ),
        (
            "D10",
            G + "test_completed_rejected_resubmit_http",
            [
                (
                    P + "freight_service.py",
                    "        # 完结专用例外仅此已驳回动作；绝不清除completed_at，也不放开通用PATCH。",
                    "        if promotion.completed_at is not None:\n            raise PromotionCompletedReadonlyError()",
                    "resubmit",
                )
            ],
        ),
        (
            "D11",
            G + "test_pending_freight_fee_patch_forbidden_http",
            [
                (
                    P + "service.py",
                    "                is not None\n            ):\n                raise FreightStateConflictError(",
                    '                == "已付款"\n            ):\n                raise FreightStateConflictError(',
                    "update_promotion",
                )
            ],
        ),
        (
            "D14",
            G + "test_zero_service_due_recall_can_upload_first_qr",
            [
                (
                    qr,
                    exception,
                    'exception = freight_status == "已驳回"',
                    "_assert_payment_qr_changeable",
                )
            ],
        ),
        (
            "D15",
            G + "test_rejected_freight_qr_replacement_and_resubmit[published_uncompleted]",
            [
                (
                    qr,
                    exception,
                    'exception = promotion.recall_status == "召回中" or (freight_status == "已驳回" and promotion.completed_at is not None)',
                    "_assert_payment_qr_changeable",
                )
            ],
        ),
        (
            "D16",
            G + "test_rejected_freight_qr_replacement_and_resubmit[cancelled_completed]",
            [
                (
                    qr,
                    "promotion.publish_status in _BEFORE_PUBLISH",
                    'promotion.publish_status != "已发布"',
                    "_assert_payment_qr_changeable",
                )
            ],
        ),
        (
            "completed_auto",
            "tests/integration/test_recall_closure.py::test_published_recall_continues_after_last_retro_completes[success]",
            [
                (
                    P + "service_parts/recall.py",
                    '            if success.return_shipping_payer == "博主垫付" and success.return_shipping_fee > 0:',
                    '            if updated.completed_at is None and success.return_shipping_payer == "博主垫付" and success.return_shipping_fee > 0:',
                    "_recall_finish",
                )
            ],
        ),
    ]


def validate_junit(xml: Path, node: str, fault: str, *, red: bool, returncode: int) -> None:
    cases = ET.parse(xml).findall(".//testcase")  # noqa: S314 只解析本次pytest生成的报告
    assert len(cases) == 1, "必须精确执行一个case"
    case = cases[0]
    path, *parts = node.split("::")
    assert case.attrib["name"] == parts[-1]
    classname = path.removesuffix(".py").replace("/", ".")
    if len(parts) > 1:
        classname += "." + ".".join(parts[:-1])
    assert case.attrib["classname"] == classname
    assert case.find("error") is None and case.find("skipped") is None
    failures = case.findall("failure")
    if not red:
        assert returncode == 0 and not failures
        return
    assert returncode == 1 and len(failures) == 1
    marker = "FLOW8_FAULT_EXPECTED:" + EXPECTED_MARKERS[fault]
    failure = failures[0]
    message = failure.attrib.get("message", "").removeprefix("AssertionError: ")
    assert message == marker or message.startswith(marker + "\n"), "不是指定业务断言红灯"
    assert f"AssertionError: {marker}" in (failure.text or ""), "异常/驱动错误不能替代业务红灯"


def run_test(node: str, fault: str, phase: str) -> dict:
    label = f"flow8-{fault}-{phase}"
    xml = ARTIFACTS / (label + ".xml")
    with (ARTIFACTS / (label + ".log")).open("w", encoding="utf-8") as log:
        result = subprocess.run(  # noqa: S603 固定pytest和本地node，不使用shell
            [sys.executable, "-m", "pytest", "-q", node, "--tb=short", f"--junitxml={xml}"],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
    validate_junit(xml, node, fault, red=phase == "red", returncode=result.returncode)
    return {"node": node, "phase": phase, "exit_code": result.returncode, "passed": True}


def main() -> None:
    environment()
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    for name, node, edits in fault_cases():
        baseline = run_test(node, name, "baseline")
        originals = {path: Path(path).read_bytes() for path, _, _, _ in edits}
        hashes = {path: hashlib.sha256(content).hexdigest() for path, content in originals.items()}
        try:
            for path, old, new, method in edits:
                edit(path, old, new, method=method)
            red = run_test(node, name, "red")
        finally:
            for path, content in originals.items():
                Path(path).write_bytes(content)
                assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == hashes[path]
        green = run_test(node, name, "green")
        results.append(
            {
                "name": name,
                "baseline": baseline,
                "red": red,
                "green": green,
                "source_sha256": hashes,
                "source_restored": True,
                "passed": True,
            }
        )
        (ARTIFACTS / "flow8-faults.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
    print(json.dumps({"mutations": len(results), "passed": True}))


if __name__ == "__main__":
    main()
