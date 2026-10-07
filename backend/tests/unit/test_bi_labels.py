"""补充 2：BI 图表标签由后端给显示名、不带商品编码（设计 §11.1，AC 61）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.modules.report.advanced_schemas import BiStylePerformance, ProductionRow
from app.modules.report.bi_service import BiService, bi_chart_labels

pytestmark = pytest.mark.unit

_ZERO = Decimal("0")


def _perf(
    title: str,
    short_name: str | None = None,
    *,
    is_suit: bool = False,
    style_codes: list[str] | None = None,
    code: str | None = None,
) -> BiStylePerformance:
    return BiStylePerformance(
        goods_id=uuid4(),
        goods_code=code or f"G-{uuid4().hex[:6]}",
        goods_title=title,
        goods_short_name=short_name,
        is_suit=is_suit,
        style_codes=style_codes or [],
        sales_amount=_ZERO,
        refund_amount=_ZERO,
        confirmed_amount=_ZERO,
        internal_spend=_ZERO,
        external_spend=_ZERO,
        total_spend=_ZERO,
    )


class TestBiChartLabels:
    def test_display_name_and_suit_mark(self) -> None:
        rows = [
            _perf("冰雪飞狐皮草外套长款", "冰雪飞狐", style_codes=["260415"]),
            _perf("没填简称的全称", style_codes=["260416"]),
            _perf("春日套装两件套", "春日套装", is_suit=True, style_codes=["260417", "260418"]),
        ]
        assert bi_chart_labels(rows) == ["冰雪飞狐", "没填简称的全称", "春日套装（套装）"]

    def test_duplicate_names_get_style_codes(self) -> None:
        rows = [
            _perf("A全称", "同名", style_codes=["260415"]),
            _perf("B全称", "同名", style_codes=["260419", "260420"]),
            _perf("C全称", "不同名", style_codes=["260421"]),
        ]
        assert bi_chart_labels(rows) == [
            "同名（款号 260415）",
            "同名（款号 260419、260420）",
            "不同名",
        ]

    def test_suit_and_single_with_same_name_are_distinct(self) -> None:
        # 套装标记已经让两者不同名，不再追加款号
        rows = [
            _perf("x", "春日", style_codes=["A"]),
            _perf("y", "春日", is_suit=True, style_codes=["A", "B"]),
        ]
        assert bi_chart_labels(rows) == ["春日", "春日（套装）"]

    def test_more_than_three_codes_truncated(self) -> None:
        rows = [
            _perf("x", "同名", is_suit=True, style_codes=["S1", "S2", "S3", "S4"]),
            _perf("y", "同名", is_suit=True, style_codes=["S5", "S6"]),
        ]
        assert bi_chart_labels(rows) == [
            "同名（套装）（款号 S1、S2、S3等）",
            "同名（套装）（款号 S5、S6）",
        ]

    def test_never_contains_goods_code(self) -> None:
        rows = [
            _perf(
                "同名", code="SUIT-1074568657697", is_suit=True, style_codes=["260415", "260419"]
            ),
            _perf(
                "同名", code="SUIT-1074568657698", is_suit=True, style_codes=["260415", "260420"]
            ),
            _perf("单品", code="260421", style_codes=[]),
        ]
        labels = bi_chart_labels(rows)
        assert len(labels) == 3
        for label, row in zip(labels, rows, strict=True):
            assert row.goods_code not in label

    def test_empty(self) -> None:
        assert bi_chart_labels([]) == []


def _production_row(**kw: Any) -> ProductionRow:
    base: dict[str, Any] = {
        "goods_id": uuid4(),
        "goods_code": "SUIT-260415_260419",
        "goods_title": "春日套装两件套",
        "goods_short_name": "春日套装",
        "is_suit": True,
        "style_codes": ["260415", "260419"],
        "pay_amount": Decimal("100"),
        "refund_amount": _ZERO,
        "confirmed_amount": Decimal("100"),
        "promo_cost": _ZERO,
        "ad_spend": Decimal("10"),
        "total_spend": Decimal("10"),
        "add_cart_count": 0,
    }
    base.update(kw)
    return ProductionRow(**base)


class TestGoodsRow:
    def test_carries_style_codes(self) -> None:
        perf = BiService._goods_row(_production_row(), Decimal("5"))
        assert perf.style_codes == ["260415", "260419"]
        assert perf.total_spend == Decimal("15")

    def test_style_codes_default_empty(self) -> None:
        perf = BiService._goods_row(_production_row(style_codes=[]), _ZERO)
        assert perf.style_codes == []
