"""R8 独立 PG18 证据：限定六文件，逐项核对收集结果与零跳过 JUnit。"""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

FILES = (
    "tests/integration/test_promotion_r8_filters.py",
    "tests/integration/test_promotion_r8_visibility.py",
    "tests/integration/test_filter_preference.py",
    "tests/api/test_report_drill_api.py",
    "tests/api/test_report_view_permissions.py",
    "tests/integration/test_summary_read_equivalence.py",
)
MODULES = {path.removesuffix(".py").replace("/", ".") for path in FILES}
RLS_TEST = "test_shared_access_uses_real_app_role"
REQUIRED = {
    "test_attribution_pr_intersection_pagination_and_options",
    "test_other_tenant_selected_goods_not_disclosed",
    "test_report_link_matches_detail_and_grant",
    RLS_TEST,
    "test_detail_service_rejects_report_and_finance_only",
    "test_promotion_dimensions_are_user_and_page_local",
}
MAX_CASES = 1024


def pytest_addoption(parser):
    parser.addoption("--r8-collection", help="R8 定向验证收集清单输出")


def pytest_collection_finish(session):
    output = session.config.getoption("--r8-collection")
    if not output:
        return
    if (
        set(session.config.args) != set(FILES)
        or session.config.option.keyword
        or session.config.option.markexpr
    ):
        raise ValueError("R8 必须完整选择六文件，不允许 -k / -m 过滤")
    cases = []
    for item in session.items:
        filename, *parts = item.nodeid.split("::")
        cases.append(
            {
                "classname": ".".join(
                    [filename.removesuffix(".py").replace("/", "."), *parts[:-1]]
                ),
                "name": parts[-1],
                "rls": item.get_closest_marker("rls") is not None,
            }
        )
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cases), encoding="utf-8")


def _identity(case):
    return case["classname"], case["name"]


def verify(collection: Path, junit: Path) -> int:
    if collection.stat().st_size > 256_000 or junit.stat().st_size > 2_000_000:
        raise ValueError("R8 证据超出大小上限")
    selected = json.loads(collection.read_text(encoding="utf-8"))
    if not isinstance(selected, list) or not 1 <= len(selected) <= MAX_CASES:
        raise ValueError("R8 收集清单为空或超出用例上限")
    expected = {_identity(case) for case in selected}
    if len(expected) != len(selected):
        raise ValueError("R8 收集清单存在重复")
    modules = set()
    for classname, _name in expected:
        matched = {m for m in MODULES if classname == m or classname.startswith(m + ".")}
        if not matched:
            raise ValueError("R8 收集了范围外测试")
        modules.update(matched)
    if modules != MODULES or not REQUIRED <= {name.split("[", 1)[0] for _, name in expected}:
        raise ValueError("R8 必选文件或业务用例缺失")
    if not any(case["name"] == RLS_TEST and case["rls"] for case in selected):
        raise ValueError("R8 真实 app role 用例缺少 rls 标记")
    root = ET.fromstring(junit.read_bytes())  # noqa: S314 限长、本 job 生成的 JUnit
    cases = root.findall(".//testcase")
    if not cases or len(cases) > MAX_CASES:
        raise ValueError("R8 JUnit 为空或超出用例上限")
    if any(root.findall(f".//{tag}") for tag in ("failure", "error", "skipped")):
        raise ValueError("R8 不接受失败、错误或跳过")
    actual = {_identity(case.attrib) for case in cases}
    if actual != expected or len(actual) != len(cases):
        raise ValueError("R8 JUnit 与收集清单不一致或存在重复")
    return len(cases)


def main() -> None:
    # 复用现有仅 GitHub 临时 PG18.6 / 本机独占测试库的限制，未接入任何外部库。
    from scripts.ci_flow7_pg18 import ARTIFACTS, connect, environment, verify_server

    values = environment()
    count = verify(ARTIFACTS / "r8-pg18-collection.json", ARTIFACTS / "r8-pg18-tests.xml")
    conn = connect(values["DATABASE_URL_SYNC"])
    try:
        verify_server(conn)
    finally:
        conn.close()
    report = {
        "passed": True,
        "tests": count,
        "skipped": 0,
        "files": list(FILES),
        "head_sha": os.environ.get("GITHUB_SHA"),
        "evidence_type": "r8_actual_pg18_api_preferences_summary_and_app_role_rls",
    }
    (ARTIFACTS / "r8-pg18-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
