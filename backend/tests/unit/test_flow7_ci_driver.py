"""CI迁移driver只接受独占本机合成库，拒绝泄露DSN。"""

import pytest

from scripts.ci_flow7_pg18 import DB, validate_dsn

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value",
    [
        f"postgresql://u:synthetic-secret@47.110.76.9:5432/{DB}",
        "postgresql://u:synthetic-secret@127.0.0.1:5432/clothing_erp",
        f"postgresql://u:synthetic-secret@127.0.0.1:5433/{DB}",
        f"postgresql://u:synthetic-secret@127.0.0.1:5432/{DB}?host=remote",
        f"postgresql://u:synthetic-secret@127.0.0.1:5432/{DB}#fragment",
    ],
)
def test_reject_nonisolated_dsn_without_printing_credentials(value: str) -> None:
    with pytest.raises(ValueError) as error:
        validate_dsn(value)
    assert "synthetic-secret" not in str(error.value)


def test_accept_exact_local_test_database_and_maintenance_only_for_bootstrap() -> None:
    assert (
        validate_dsn(f"postgresql+asyncpg://u:placeholder@127.0.0.1:5432/{DB}")
        == f"postgresql://u:placeholder@127.0.0.1:5432/{DB}"
    )
    with pytest.raises(ValueError):
        validate_dsn("postgresql://u:placeholder@127.0.0.1:5432/postgres")
    assert validate_dsn("postgresql://u:placeholder@localhost:5432/postgres", admin=True)


def test_fault_cases_match_unique_source_and_produce_valid_python() -> None:
    """只验证注入器可运行，不声称业务故障测试已经红灯。"""
    import ast
    from pathlib import Path

    from scripts.ci_flow7_faults import fault_cases

    root = Path(__file__).resolve().parents[2]
    cases = fault_cases()
    assert len(cases) == 14 and len({name for name, _, _ in cases}) == 14
    for name, _, edits in cases:
        sources = {}
        for path, old, new, method in edits:
            source = sources.get(path, (root / path).read_text(encoding="utf-8"))
            start = source.index(f"    async def {method}(") if method else 0
            end = source.find("\n    async def ", start + 1) if method else -1
            if end < 0:
                end = len(source)
            section = source[start:end]
            assert section.count(old) == 1, (name, path)
            sources[path] = source[:start] + section.replace(old, new) + source[end:]
        for path, source in sources.items():
            ast.parse(source, filename=f"{name}:{path}")


def test_drill_insert_columns_exist_in_head_metadata() -> None:
    """066基础列沿用head；防合成driver误拼不存在列，实际约束仍交PG演练验证。"""
    import ast
    import re
    from pathlib import Path

    from app.core.db import Base

    source = (Path(__file__).resolve().parents[2] / "scripts/ci_flow7_pg18.py").read_text(
        encoding="utf-8"
    )
    inserts = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found = re.search(r'INSERT INTO (\w+|"user")\s*\(([^)]+)\)', node.value)
            if found:
                table = found[1].strip('"')
                columns = {col.strip() for col in found[2].split(",")}
                assert columns <= set(Base.metadata.tables[table].columns.keys()), (table, columns)
                if table == "promotion":
                    assert {
                        "platform",
                        "style_code_snapshot",
                        "style_short_name_snapshot",
                    } <= columns
                inserts.append(table)
    assert {
        "promotion",
        "style",
        "blogger",
        "user",
        "blogger_retrospective",
        "user_permission_override",
    } <= set(inserts)


@pytest.mark.parametrize("plain_raise", [False, True])
def test_fault_junit_accepts_only_selected_business_assertion(tmp_path, plain_raise) -> None:
    import xml.etree.ElementTree as ET

    from scripts.ci_flow7_faults import validate_junit

    report = ET.Element("testsuites")
    case = ET.SubElement(
        report,
        "testcase",
        classname="tests.integration.test_metrics_gate.TestGate",
        name="test_day_seven_succeeds_beijing_midnight",
    )
    marker = "FLOW7_FAULT_EXPECTED:beijing"
    message = "AssertionError: " + marker if plain_raise else marker + "\nassert False"
    failure = ET.SubElement(case, "failure", message=message)
    failure.text = "E AssertionError: " + marker
    xml = tmp_path / "junit.xml"
    ET.ElementTree(report).write(xml)
    assert (
        validate_junit(
            xml,
            "tests/integration/test_metrics_gate.py::TestGate::test_day_seven_succeeds_beijing_midnight",
            "E2_beijing",
            red=True,
            returncode=1,
        )
        == 1
    )


@pytest.mark.parametrize(
    "bad",
    [
        "driver",
        "type",
        "other_assertion",
        "wrong_marker",
        "wrong_case",
        "wrong_module",
        "zero",
        "multiple",
        "skip",
        "error",
        "returncode",
        "fake_assertion",
    ],
)
def test_fault_junit_rejects_accidental_red(tmp_path, bad) -> None:
    import xml.etree.ElementTree as ET

    from scripts.ci_flow7_faults import validate_junit

    report = ET.Element("testsuites")
    case = ET.SubElement(
        report,
        "testcase",
        classname="tests.integration.test_metrics_gate.TestGate",
        name="test_day_seven_succeeds_beijing_midnight",
    )
    marker = "FLOW7_FAULT_EXPECTED:beijing"
    failure = ET.SubElement(case, "failure", message=marker + "\nassert False")
    failure.text = "E AssertionError: " + marker
    if bad in {"driver", "type", "other_assertion", "wrong_marker"}:
        failure.set(
            "message",
            {
                "driver": "asyncpg.UndefinedColumnError: bad column",
                "type": "TypeError: bad fixture",
                "other_assertion": "assert 1 == 2",
                "wrong_marker": "FLOW7_FAULT_EXPECTED:scope",
            }[bad],
        )
    elif bad == "wrong_case":
        case.set("name", "other_case")
    elif bad == "wrong_module":
        case.set("classname", "tests.other.TestGate")
    elif bad == "zero":
        report.remove(case)
    elif bad == "multiple":
        report.append(ET.Element("testcase"))
    elif bad in {"skip", "error"}:
        ET.SubElement(case, "skipped" if bad == "skip" else "error")
    elif bad == "fake_assertion":
        failure.text = "TypeError: " + marker
    xml = tmp_path / "junit.xml"
    ET.ElementTree(report).write(xml)
    with pytest.raises(AssertionError):
        validate_junit(
            xml,
            "tests/integration/test_metrics_gate.py::TestGate::test_day_seven_succeeds_beijing_midnight",
            "E2_beijing",
            red=True,
            returncode=2 if bad == "returncode" else 1,
        )


@pytest.mark.parametrize("action", ["bootstrap", "drill", "verify-results"])
def test_ci_driver_actions_use_backend_module_entry(action) -> None:
    """CI必须保留backend为导入根；直接scripts/path会让历史004的app导入失败。"""
    import re
    from pathlib import Path

    workflow = Path(__file__).resolve().parents[3] / ".github/workflows/ci.yml"
    source = workflow.read_text(encoding="utf-8")
    calls = re.findall(r"python [^\n]*ci_flow7_pg18[^\n]*", source)
    matching = [call for call in calls if f" {action} " in call]
    assert len(matching) == 1
    assert matching[0].startswith(f"python -m scripts.ci_flow7_pg18 {action} ")


def test_history_scan_from_clean_backend_interpreter() -> None:
    """真实子进程扫描历史迁移，无pytest导入根或继承PYTHONPATH掩盖入口错误。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alembic.config import Config; from alembic.script import ScriptDirectory; "
            "cfg=Config(); cfg.set_main_option('script_location','alembic'); "
            "assert ScriptDirectory.from_config(cfg).get_heads()==['067_retro_completion']",
        ],
        cwd=root,
        env={key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
