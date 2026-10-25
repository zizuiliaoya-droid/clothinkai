"""仅验证证据检查器的拒绝行为；合成 JUnit 不是实际数据库验证。"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ci_r8_verify import (
    FILES,
    MODULES,
    REQUIRED,
    RLS_TEST,
    pytest_collection_finish,
    verify,
)


@pytest.fixture
def evidence(tmp_path):
    rows = [
        {"classname": module, "name": "test_example", "rls": False} for module in sorted(MODULES)
    ]
    rows += [
        {
            "classname": "tests.integration.test_promotion_r8_visibility",
            "name": name,
            "rls": name == RLS_TEST,
        }
        for name in sorted(REQUIRED)
    ]
    return tmp_path, rows


def write_evidence(directory, rows, *, junit_rows=None, tag=None):
    collection = directory / "collected.json"
    collection.write_text(json.dumps(rows), encoding="utf-8")
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for row in rows if junit_rows is None else junit_rows:
        case = ET.SubElement(suite, "testcase", classname=row["classname"], name=row["name"])
        if tag:
            ET.SubElement(case, tag)
    junit = directory / "junit.xml"
    ET.ElementTree(root).write(junit)
    return collection, junit


def test_matching_synthetic_evidence(evidence):
    directory, rows = evidence
    assert verify(*write_evidence(directory, rows)) == len(rows)


@pytest.mark.parametrize("tag", ["skipped", "failure", "error"])
def test_rejects_non_pass_result(evidence, tag):
    directory, rows = evidence
    with pytest.raises(ValueError, match="失败、错误或跳过"):
        verify(*write_evidence(directory, rows, tag=tag))


@pytest.mark.parametrize("change", ["missing", "duplicate", "unexpected", "empty"])
def test_rejects_wrong_executed_selection(evidence, change):
    directory, rows = evidence
    actual = {
        "missing": rows[:-1],
        "duplicate": rows + rows[:1],
        "unexpected": [*rows, {"classname": "tests.unit.unrelated", "name": "test_x"}],
        "empty": [],
    }[change]
    with pytest.raises(ValueError):
        verify(*write_evidence(directory, rows, junit_rows=actual))


@pytest.mark.parametrize("change", ["module", "required", "rls"])
def test_rejects_incomplete_collection(evidence, change):
    directory, rows = evidence
    if change == "module":
        rows = [r for r in rows if r["classname"] != "tests.api.test_report_drill_api"]
    elif change == "required":
        rows = [r for r in rows if r["name"] != RLS_TEST]
    else:
        rows = [{**r, "rls": False} for r in rows]
    with pytest.raises(ValueError):
        verify(*write_evidence(directory, rows))


def test_rejects_oversized_evidence(evidence):
    directory, rows = evidence
    collection, junit = write_evidence(directory, rows)
    Path(collection).write_text(" " * 256_001, encoding="utf-8")
    with pytest.raises(ValueError, match="大小上限"):
        verify(collection, junit)


@pytest.mark.parametrize("filter_kind", [None, "keyword", "markexpr", "files"])
def test_collection_hook_checks_selection_and_preserves_class_params(tmp_path, filter_kind):
    output = tmp_path / "collection.json"
    config = SimpleNamespace(
        getoption=lambda _: str(output),
        args=FILES[:-1] if filter_kind == "files" else FILES,
        option=SimpleNamespace(
            keyword="subset" if filter_kind == "keyword" else "",
            markexpr="not rls" if filter_kind == "markexpr" else "",
        ),
    )
    item = SimpleNamespace(
        nodeid="tests/integration/test_promotion_r8_visibility.py::TestAccess::test_one[spring]",
        get_closest_marker=lambda _: True,
    )
    session = SimpleNamespace(config=config, items=[item])
    if filter_kind:
        with pytest.raises(ValueError, match="不允许 -k / -m"):
            pytest_collection_finish(session)
    else:
        pytest_collection_finish(session)
        assert json.loads(output.read_text()) == [
            {
                "classname": "tests.integration.test_promotion_r8_visibility.TestAccess",
                "name": "test_one[spring]",
                "rls": True,
            }
        ]
