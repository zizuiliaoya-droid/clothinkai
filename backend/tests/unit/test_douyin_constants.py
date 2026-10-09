"""8b 灰豚抖音来源 ``huitun_douyin`` 的常量与纯函数（设计 §6.3；评审 r1 N3、r2 M4）。

- 迁移 059 下行要从原文里去掉的列（``_DOUYIN_PROTECTED_RAW_COLUMNS``，ast 读，迁移不 import app 代码）
  = adapter 固定列表里映射到 ``sensitive_targets`` 的源列：以后抖音加受保护列时这里会红
- adapter 自己覆盖 ``builtin_columns()``（N3）：失败明细遮挡不靠继承来的手工模版别名碰巧成立
- 统计列清单与前端 ``douyinMetrics.snapshot.json`` 逐字一致（前端博主页按它展示）
- 报价原文与数值、``platform_metrics`` 的拼法；造数全是脱敏假数据
"""

from __future__ import annotations

import ast
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.core.security.field_permissions import FIELD_PERMISSION_REGISTRY
from app.modules.importer import masking
from app.modules.importer.adapters.blogger_douyin import (
    DOUYIN_METRIC_COLUMNS,
    DOUYIN_NUMERIC_COLUMNS,
    DOUYIN_SHEET,
    HuitunDouyinImportAdapter,
    _douyin_quote,
)
from app.modules.importer.masking import masked_source_columns

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_059 = _ROOT / "alembic" / "versions" / "059_8b_blogger_library.py"
# 本机容器只挂 backend；CI 检出整个仓库（parents[3] = 仓库根）
_SNAPSHOT = (
    _ROOT.parent / "frontend" / "src" / "features" / "blogger" / "douyinMetrics.snapshot.json"
)

_ADAPTER = HuitunDouyinImportAdapter()


def _migration_constant(name: str) -> object:
    tree = ast.parse(_MIGRATION_059.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"059 里没有 {name}")


class TestBuiltinColumns:
    def test_fixed_list(self) -> None:
        """N3：抖音自己的固定列表，不继承手工模版（没有「微信」「手机号」这些别名）。"""
        cols = [(c["source_col"], c["target_field"]) for c in _ADAPTER.builtin_columns()]
        assert cols == [
            ("博主ID", "xiaohongshu_id"),
            ("抖音博主", "nickname"),
            ("网页ID", "web_id"),
            ("微信号", "wechat"),
            ("报价", "quote"),
            ("报价", "quote_note"),
            ("粉丝总量", "follower_count"),
        ]
        assert all(not c.get("aliases") for c in _ADAPTER.builtin_columns())

    def test_sensitive_targets(self) -> None:
        assert dict(_ADAPTER.sensitive_targets) == {
            "wechat": ("blogger", "wechat"),
            "quote": ("blogger", "quote"),
            "quote_note": ("blogger", "quote"),
        }
        for entity, field in _ADAPTER.sensitive_targets.values():
            assert field in FIELD_PERMISSION_REGISTRY[entity]

    def test_migration_strips_exactly_the_protected_columns(self) -> None:
        """r2 M4：下行去掉的原文列 = 映射到受保护目标字段的源列。"""
        protected = {
            c["source_col"]
            for c in _ADAPTER.builtin_columns()
            if c["target_field"] in _ADAPTER.sensitive_targets
        }
        assert set(_migration_constant("_DOUYIN_PROTECTED_RAW_COLUMNS")) == protected
        assert protected == {"微信号", "报价"}
        assert _migration_constant("_DOUYIN_SOURCE") == _ADAPTER.source == "huitun_douyin"

    def test_masking_uses_fixed_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """看不到微信 / 报价的人：失败明细只遮「微信号」「报价」两列。"""
        monkeypatch.setattr(masking, "can_read_field", lambda entity, field, ctx: False)
        assert masked_source_columns(_ADAPTER, None, None) == {"微信号", "报价"}  # type: ignore[arg-type]


class TestMetricColumns:
    def test_lists(self) -> None:
        assert DOUYIN_SHEET == "抖音博主库"
        assert len(DOUYIN_METRIC_COLUMNS) == 23
        assert len(set(DOUYIN_METRIC_COLUMNS)) == 23
        assert DOUYIN_NUMERIC_COLUMNS == tuple(
            c
            for c in DOUYIN_METRIC_COLUMNS
            if c not in {"赞粉比", "性别分布", "年龄分布", "曝光点赞比"}
        )
        assert len(DOUYIN_NUMERIC_COLUMNS) == 19
        # 受保护列、联系人、判重 / 身份列都不进统计
        assert {"报价", "微信号", "联系人", "博主ID", "网页ID", "抖音博主"}.isdisjoint(
            DOUYIN_METRIC_COLUMNS
        )
        # 重名列带分组前缀（file_layout.group_prefix_duplicates）
        assert "30天视频分析·点赞" in DOUYIN_METRIC_COLUMNS
        assert "7天视频分析·互动量" in DOUYIN_METRIC_COLUMNS
        assert "点赞" not in DOUYIN_METRIC_COLUMNS

    def test_frontend_snapshot_matches(self) -> None:
        if not _SNAPSHOT.exists():
            if os.environ.get("CI"):
                pytest.fail(f"缺少前端快照 {_SNAPSHOT}")
            pytest.skip("本机容器只挂了 backend，前端快照由 CI 比对")
        snapshot = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
        assert snapshot == {
            "source": "huitun_douyin",
            "columns": list(DOUYIN_METRIC_COLUMNS),
            "numeric_columns": list(DOUYIN_NUMERIC_COLUMNS),
        }


class TestDouyinQuote:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("500", (Decimal("500.00"), "500")),
            (" 1,200 ", (Decimal("1200.00"), "1,200")),
            ("1.2w", (Decimal("12000.00"), "1.2w")),
            ("图文500", (None, "图文500")),
            ("图文500 视频800", (None, "图文500 视频800")),
            ("-5", (None, "-5")),
            ("--", (None, None)),
            ("", (None, None)),
            (None, (None, None)),
        ],
    )
    def test_quote(self, raw: object, expected: tuple[Decimal | None, str | None]) -> None:
        assert _douyin_quote(raw) == expected


class TestParseRow:
    def test_parse_row_ignores_mapping_and_fixes_platform(self) -> None:
        row = {
            "联系人": "员工甲",
            "抖音博主": " 抖音甲 ",
            "博主ID": " DY.T1 ",
            "网页ID": "61900000001",
            "微信号": "wx-t1",
            "报价": "图文500",
            "粉丝总量": "1.2w",
            "灰豚指数": "85.3",
            "新增粉丝": "-1,234",
            "预估曝光": "3.5亿",
            "新增点赞": "--",
            "性别分布": "女性居多，占比60.00%",
            "7天视频分析·互动量": "1.5万+",
            "col_30": "",
        }

        class _Mapping:
            mapping_config: ClassVar[dict[str, Any]] = {
                "columns": [{"source_col": "联系人", "target_field": "nickname"}]
            }

        parsed = _ADAPTER.parse_row(row, _Mapping())  # type: ignore[arg-type]
        assert parsed == {
            "xiaohongshu_id": "DY.T1",
            "nickname": "抖音甲",
            "platform": "抖音",
            "web_id": "61900000001",
            "wechat": "wx-t1",
            "quote": None,
            "quote_note": "图文500",
            "follower_count": 12000,
            "platform_metrics": {
                "source": "huitun_douyin",
                "raw": {
                    "灰豚指数": "85.3",
                    "粉丝总量": "1.2w",
                    "新增粉丝": "-1,234",
                    "预估曝光": "3.5亿",
                    "性别分布": "女性居多，占比60.00%",
                    "7天视频分析·互动量": "1.5万+",
                },
                "values": {
                    "灰豚指数": 85.3,
                    "粉丝总量": 12000,
                    "新增粉丝": -1234,
                    "预估曝光": 350000000,
                },
            },
        }
        assert isinstance(parsed["platform_metrics"]["values"]["粉丝总量"], int)
        assert _ADAPTER.validate(parsed) == []

    def test_no_metrics_is_none(self) -> None:
        parsed = _ADAPTER.parse_row({"博主ID": "T2", "抖音博主": "乙"}, None)
        assert parsed["platform_metrics"] is None
        assert parsed["follower_count"] is None

    def test_missing_id_message(self) -> None:
        """D1③：没有博主ID 的行整行失败，原因写清楚。"""
        errs = _ADAPTER.validate(_ADAPTER.parse_row({"博主ID": "--", "抖音博主": "乙"}, None))
        assert errs == [
            "博主ID为空：灰豚导出的这一行没有博主ID，未导入；请在灰豚补齐后重新导出，或在博主页手工新建"
        ]
        errs = _ADAPTER.validate(_ADAPTER.parse_row({"博主ID": "T3", "抖音博主": ""}, None))
        assert errs == ["抖音博主不能为空"]

    def test_invalid_follower_fails(self) -> None:
        errs = _ADAPTER.validate(
            _ADAPTER.parse_row({"博主ID": "T4", "抖音博主": "乙", "粉丝总量": "很多"}, None)
        )
        assert errs == ["粉丝数必须为非负整数"]

    def test_compare_specs_include_quote_note(self) -> None:
        names = {s.name for s in _ADAPTER.compare_specs()}
        assert "quote_note" in names
        assert {"platform", "quality_tags"}.isdisjoint(names)
        assert "quote_note" in _ADAPTER.compare_field_names()
