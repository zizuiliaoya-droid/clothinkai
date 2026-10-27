"""PR9三条业务故障注入；复用既有CI临时环境，源码原字节恢复。"""

import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.ci_flow7_pg18 import ARTIFACTS, environment

TEST = "tests/integration/test_promotion_content_platform.py::"
CASES = (
    (
        "snapshot",
        TEST + "test_created_snapshot_is_independent",
        "app/modules/promotion/service.py",
        "else blogger.content_category,",
        "else None,",
    ),
    (
        "import_category",
        TEST + "test_import_category_uses_mapped_value_or_blogger[mapped]",
        "app/modules/importer/adapters/promotion.py",
        'content_category=parsed.get("content_category") or blogger.content_category,',
        "content_category=None,",
    ),
    (
        "primary_report",
        TEST + "test_platform_replace_is_atomic_and_primary_remains_authoritative",
        "app/modules/report/repository.py",
        '{effective_like_sum("like_count", "platform")} AS like_count',
        '{effective_like_sum("like_count", "platform")} + COALESCE((SELECT SUM(pp.like_count) FROM promotion_platform_post pp WHERE pp.tenant_id = :tenant_id), 0) AS like_count',
    ),
)


def run(node, marker, phase):
    xml = ARTIFACTS / f"flow9-{marker}-{phase}.xml"
    with (ARTIFACTS / f"flow9-{marker}-{phase}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", node, "--tb=short", f"--junitxml={xml}"],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )  # noqa: S603 fixed test nodes
    cases = ET.parse(xml).findall(".//testcase")  # noqa: S314 own pytest output
    assert len(cases) == 1 and cases[0].attrib["name"] == node.split("::")[-1]
    case = cases[0]
    assert case.find("error") is None and case.find("skipped") is None
    if phase == "red":
        failure = case.find("failure")
        assert result.returncode == 1 and failure is not None
        expected = f"FLOW9_FAULT_EXPECTED:{marker}"
        assert (
            failure.attrib.get("message", "").removeprefix("AssertionError: ").splitlines()[0]
            == expected
        )
        assert f"AssertionError: {expected}" in (failure.text or "")
    else:
        assert result.returncode == 0 and case.find("failure") is None


def main():
    environment()
    ARTIFACTS.mkdir(exist_ok=True)
    results = []
    for marker, node, filename, old, new in CASES:
        path = Path(filename)
        original = path.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        run(node, marker, "baseline")
        try:
            source = original.decode("utf-8")
            assert source.count(old) == 1
            path.write_text(source.replace(old, new), encoding="utf-8")
            run(node, marker, "red")
        finally:
            path.write_bytes(original)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        run(node, marker, "green")
        results.append({"name": marker, "passed": True, "source_restored": True, "sha256": digest})
        (ARTIFACTS / "flow9-faults.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    main()
