"""FI驱动不能把收集/驱动错误当业务红灯；所有补丁唯一命中且语法完整。"""

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import ci_flow8_faults as driver

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("fault", driver.fault_cases(), ids=lambda item: item[0])
def test_fault_patch_has_unique_anchor_and_valid_python(tmp_path, monkeypatch, fault):
    _, _, edits = fault
    root = Path.cwd()
    for path, _, _, _ in edits:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / path).read_bytes())
    monkeypatch.chdir(tmp_path)
    for path, old, new, method in edits:
        driver.edit(path, old, new, method=method)
    for path, _, _, _ in edits:
        ast.parse(Path(path).read_text(encoding="utf-8"))


def report(tmp_path, *, failure=None, tag="failure", node="test_case"):
    suite = ET.Element("testsuite")
    case = ET.SubElement(suite, "testcase", classname="tests.integration.test_sample", name=node)
    if failure is not None:
        ET.SubElement(case, tag, message=failure).text = failure
    target = tmp_path / "result.xml"
    ET.ElementTree(suite).write(target)
    return target


def test_exact_business_assertion_is_accepted(tmp_path):
    xml = report(tmp_path, failure="AssertionError: FLOW8_FAULT_EXPECTED:published_not_complete")
    driver.validate_junit(
        xml, "tests/integration/test_sample.py::test_case", "D8", red=True, returncode=1
    )


@pytest.mark.parametrize(
    "failure,tag,node,code",
    [
        ("TypeError: bad signature", "failure", "test_case", 1),
        ("AssertionError: wrong assertion", "failure", "test_case", 1),
        ("AssertionError: FLOW8_FAULT_EXPECTED:published_not_complete", "error", "test_case", 1),
        ("AssertionError: FLOW8_FAULT_EXPECTED:published_not_complete", "failure", "other_case", 1),
        ("skip", "skipped", "test_case", 0),
        (None, "failure", "test_case", 0),
    ],
)
def test_non_business_failures_are_rejected(tmp_path, failure, tag, node, code):
    xml = report(tmp_path, failure=failure, tag=tag, node=node)
    with pytest.raises(AssertionError):
        driver.validate_junit(
            xml, "tests/integration/test_sample.py::test_case", "D8", red=True, returncode=code
        )


def test_original_bytes_restored_when_red_validation_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "source.py"
    original = b"value = 1\r\n"
    target.write_bytes(original)
    monkeypatch.setattr(driver, "environment", lambda: {})
    monkeypatch.setattr(driver, "ARTIFACTS", tmp_path / "artifacts")
    monkeypatch.setattr(
        driver,
        "fault_cases",
        lambda: [("D8", "node", [("source.py", "value = 1", "value = 2", None)])],
    )

    def run(node, fault, phase):
        if phase == "red":
            raise RuntimeError("invalid red result")
        return {"passed": True}

    monkeypatch.setattr(driver, "run_test", run)
    with pytest.raises(RuntimeError, match="invalid red result"):
        driver.main()
    assert target.read_bytes() == original
