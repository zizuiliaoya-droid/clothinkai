"""补充 2：BI 图表标签由后端给显示名、不带商品编码（设计 §11.1，AC 61）；7e R6 单品块。"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.report.advanced_schemas import BiGoodsRow, ProductionRow
from app.modules.report.bi_service import BiService, bi_chart_labels

pytestmark = pytest.mark.unit

_ZERO = Decimal("0")


def _row(
    title: str = "春日套装两件套",
    short_name: str | None = None,
    *,
    is_suit: bool = False,
    style_codes: list[str] | None = None,
    code: str | None = None,
    pay: str = "0",
    net_roi: str | None = None,
) -> ProductionRow:
    return ProductionRow(
        goods_id=uuid4(),
        goods_code=code or f"G-{uuid4().hex[:6]}",
        goods_title=title,
        goods_short_name=short_name,
        is_suit=is_suit,
        style_codes=style_codes or [],
        pay_amount=Decimal(pay),
        refund_amount=_ZERO,
        confirmed_amount=_ZERO,
        promo_cost=_ZERO,
        ad_spend=_ZERO,
        total_spend=_ZERO,
        pay_orders=0,
        add_cart_count=0,
        ad_add_cart_count=0,
        net_roi=None if net_roi is None else Decimal(net_roi),
    )


class TestBiChartLabels:
    def test_display_name_and_suit_mark(self) -> None:
        rows = [
            _row("冰雪飞狐皮草外套长款", "冰雪飞狐", style_codes=["260415"]),
            _row("没填简称的全称", style_codes=["260416"]),
            _row("春日套装两件套", "春日套装", is_suit=True, style_codes=["260417", "260418"]),
        ]
        assert bi_chart_labels(rows) == ["冰雪飞狐", "没填简称的全称", "春日套装（套装）"]

    def test_duplicate_names_get_style_codes(self) -> None:
        rows = [
            _row("A全称", "同名", style_codes=["260415"]),
            _row("B全称", "同名", style_codes=["260419", "260420"]),
            _row("C全称", "不同名", style_codes=["260421"]),
        ]
        assert bi_chart_labels(rows) == [
            "同名（款号 260415）",
            "同名（款号 260419、260420）",
            "不同名",
        ]

    def test_suit_and_single_with_same_name_are_distinct(self) -> None:
        # 套装标记已经让两者不同名，不再追加款号
        rows = [
            _row("x", "春日", style_codes=["A"]),
            _row("y", "春日", is_suit=True, style_codes=["A", "B"]),
        ]
        assert bi_chart_labels(rows) == ["春日", "春日（套装）"]

    def test_more_than_three_codes_truncated(self) -> None:
        rows = [
            _row("x", "同名", is_suit=True, style_codes=["S1", "S2", "S3", "S4"]),
            _row("y", "同名", is_suit=True, style_codes=["S5", "S6"]),
        ]
        assert bi_chart_labels(rows) == [
            "同名（套装）（款号 S1、S2、S3等）",
            "同名（套装）（款号 S5、S6）",
        ]

    def test_never_contains_goods_code(self) -> None:
        rows = [
            _row("同名", code="SUIT-1074568657697", is_suit=True, style_codes=["260415", "260419"]),
            _row("同名", code="SUIT-1074568657698", is_suit=True, style_codes=["260415", "260420"]),
            _row("单品", code="260421", style_codes=[]),
        ]
        labels = bi_chart_labels(rows)
        assert len(labels) == 3
        for label, row in zip(labels, rows, strict=True):
            assert row.goods_code not in label

    def test_empty(self) -> None:
        assert bi_chart_labels([]) == []


class TestGoodsRow:
    def test_maps_production_row_without_goods_code(self) -> None:
        row = _row(style_codes=["260415", "260419"], pay="100.00", net_roi="2.5000").model_copy(
            update={
                "ad_spend": Decimal("10.00"),
                "promo_cost": Decimal("5.00"),
                "total_spend": Decimal("15.00"),
                "confirmed_amount": Decimal("90.00"),
                "refund_amount": Decimal("10.00"),
            }
        )
        goods = BiService._goods_row(row)
        assert isinstance(goods, BiGoodsRow)
        assert "goods_code" not in goods.model_dump()
        assert goods.style_codes == ["260415", "260419"]
        assert (goods.pay_amount, goods.internal_spend, goods.external_spend) == (
            Decimal("100.00"),
            Decimal("10.00"),
            Decimal("5.00"),
        )
        assert (goods.total_spend, goods.confirmed_amount, goods.net_roi) == (
            Decimal("15.00"),
            Decimal("90.00"),
            Decimal("2.5000"),
        )


class TestGoodsBlock:
    def test_null_roi_not_in_roi_top(self) -> None:
        rows = [
            _row("甲", net_roi=None, pay="50"),
            _row("乙", net_roi="1.5000", pay="40"),
            _row("丙", net_roi="0.0000", pay="30"),
        ]
        block = BiService._goods_block(rows)
        assert [d.label for d in block.roi_top] == ["乙", "丙"]
        assert [d.value for d in block.roi_top] == [Decimal("1.5000"), Decimal("0.0000")]
        assert block.roi_top[0].goods_id == rows[1].goods_id
        # 单品表照样有空 ROI 的商品
        assert [g.goods_id for g in block.items] == [r.goods_id for r in rows]
        assert block.items[0].net_roi is None

    def test_roi_top_is_top10_desc(self) -> None:
        rows = [_row(f"商品{i}", net_roi=f"{i}.0000") for i in range(12)]
        block = BiService._goods_block(rows)
        assert [d.label for d in block.roi_top] == [f"商品{i}" for i in range(11, 1, -1)]

    def test_pay_share_top10_plus_other(self) -> None:
        rows = [_row(f"商品{i}", pay=f"{i + 1}.00") for i in range(12)]
        block = BiService._goods_block(rows)
        assert [d.label for d in block.pay_share] == [
            *[f"商品{i}" for i in range(11, 1, -1)],
            "其他",
        ]
        other = block.pay_share[-1]
        assert (other.goods_id, other.value) == (None, Decimal("3.00"))
        assert all(d.goods_id is not None for d in block.pay_share[:-1])

    def test_pay_share_no_other_when_rest_not_positive(self) -> None:
        rows = [_row(f"商品{i}", pay="10.00") for i in range(10)] + [_row("退光了", pay="0.00")]
        block = BiService._goods_block(rows)
        assert len(block.pay_share) == 10
        assert "其他" not in [d.label for d in block.pay_share]

    def test_empty(self) -> None:
        block = BiService._goods_block([])
        assert (block.items, block.roi_top, block.pay_share) == ([], [], [])
