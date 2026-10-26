"""验证PR8 PG18阶段实际产物，不接受只收集、跳过或缺少关键测试。"""

import json
import os
import xml.etree.ElementTree as ET

from scripts.ci_flow7_pg18 import ARTIFACTS, environment
from scripts.ci_flow8_faults import EXPECTED_MARKERS


def main() -> None:
    environment()
    cases = ET.parse(ARTIFACTS / "flow8-pg18-tests.xml").findall(".//testcase")  # noqa: S314 本job报告
    assert cases and all(
        not any(c.find(tag) is not None for tag in ("failure", "error", "skipped")) for c in cases
    )
    names = [c.attrib["name"] for c in cases]
    for required in (
        "test_two_connections_serialize_freight_actions",
        "test_failure_rolls_back_entire_freight_transaction",
        "test_default_manager_finance_list_is_read_only",
        "test_reads_own_tenant_only",
        "test_real_personal_grants_and_field_revokes",
        "test_cancelled_freight_cost_is_counted_once_in_work_but_excluded_from_roi",
    ):
        assert any(n.startswith(required) for n in names), required
    faults = json.loads((ARTIFACTS / "flow8-faults.json").read_text(encoding="utf-8"))
    assert {f["name"] for f in faults} == set(EXPECTED_MARKERS) and len(faults) == len(
        EXPECTED_MARKERS
    )
    assert all(f["passed"] and f["source_restored"] and f["source_sha256"] for f in faults)
    report = {
        "passed": True,
        "tests": len(cases),
        "skipped": 0,
        "faults": len(faults),
        "head_sha": os.environ.get("GITHUB_SHA"),
        "evidence_type": "actual_pg18_services_rls_concurrency_and_faults",
    }
    (ARTIFACTS / "flow8-pg18-result.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
