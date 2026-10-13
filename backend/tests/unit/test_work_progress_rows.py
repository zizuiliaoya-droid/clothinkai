"""工作进度行构造（7e R5，报表视图设计 2.5）：PRD 公式手算、分母 ≤ 0 → null、F3 置空范围、按键相加。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.modules.report.advanced_schemas import PrWorkProgress, WorkProgressMetrics
from app.modules.report.summary_models import PR_PROGRESS_METRICS
from app.modules.report.work_progress_rows import progress_metrics, sum_progress_rows


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "quote_count": 10,
        "in_schedule_count": 1,
        "urge_count": 2,
        "important_urge_count": 1,
        "overdue_count": 2,
        "publish_count": 4,
        "info_complete_count": 3,
        "cancel_count": 1,
        "recall_due_count": 2,
        "recall_success_count": 1,
        "effective_quote_count": 8,
        "hit_count": 1,
        "like_count": Decimal("250.5"),
        "cost": "40",
        "quote_amount": Decimal("1000"),
        "published_quote_amount": Decimal("450.00"),
        "effective_quote_amount": Decimal("800"),
        "unpublished_quote_amount": Decimal("400"),
        "cancelled_quote_amount": Decimal("150"),
        "promo_cost": Decimal("1253"),
        "unpublished_count": 5,
        "shipped_count": 3,
        "shipped_published_count": 2,
    }
    base.update(over)
    return base


_AMOUNT_COLS = {
    "cost",
    "quote_amount",
    "published_quote_amount",
    "effective_quote_amount",
    "unpublished_quote_amount",
    "cancelled_quote_amount",
    "promo_cost",
    "avg_published_quote",
    "amount_publish_rate",
    "total_amount_publish_rate",
    "cpl",
}


def test_formulas_hand_computed() -> None:
    m = progress_metrics(_row(), amounts=True)
    # 发布单篇均价 = 已发布服务费 450 ÷ 发布量 4
    assert m["avg_published_quote"] == Decimal("112.50")
    # 金额发布率 = 450 ÷ 总约稿服务费 1000
    assert m["amount_publish_rate"] == Decimal("0.4500")
    # 总金额发布率 = 450 ÷ 有效约稿服务费 800（不是 ÷ 1000）
    assert m["total_amount_publish_rate"] == Decimal("0.5625")
    # 发货发布率 = 已发货且已发布 2 ÷ 已发货 3
    assert m["ship_publish_rate"] == Decimal("0.6667")
    # CPL = 推广成本 1253 ÷ 折算点赞 250.5（未取整），不是 cost 40 ÷ 点赞
    assert m["cpl"] == Decimal("5.0020")
    # 金额统一两位小数，计数是 int，点赞取整
    assert (m["cost"], m["promo_cost"], m["like_count"]) == (
        Decimal("40.00"),
        Decimal("1253.00"),
        250,
    )
    assert str(m["quote_amount"]) == "1000.00"
    assert (m["unpublished_count"], m["shipped_count"], m["shipped_published_count"]) == (5, 3, 2)
    # 原有比率不变：完成率分母是有效约稿量
    assert m["month_complete_rate"] == Decimal("0.5000")
    assert m["overdue_rate"] == Decimal("0.2500")


def test_fields_match_schema() -> None:
    """``progress_metrics`` 的键恰好是指标基类的字段；PR 行 = 基类 + pr_id / pr_name。"""
    m = progress_metrics(_row(), amounts=True)
    assert set(m) == set(WorkProgressMetrics.model_fields)
    assert set(PrWorkProgress.model_fields) == {
        *WorkProgressMetrics.model_fields,
        "pr_id",
        "pr_name",
    }
    PrWorkProgress(pr_id=None, pr_name="未分配", **m)


@pytest.mark.parametrize("den", [0, -1], ids=["zero", "negative"])
def test_non_positive_denominators_null(den: int) -> None:
    m = progress_metrics(
        _row(
            publish_count=den,
            quote_amount=Decimal(den),
            effective_quote_amount=Decimal(den),
            shipped_count=den,
            like_count=Decimal(den),
        ),
        amounts=True,
    )
    for c in (
        "avg_published_quote",
        "amount_publish_rate",
        "total_amount_publish_rate",
        "ship_publish_rate",
        "cpl",
    ):
        assert m[c] is None, c


def test_amounts_hidden_nulls_money_and_money_ratios_only() -> None:
    shown = progress_metrics(_row(), amounts=True)
    hidden = progress_metrics(_row(), amounts=False)
    assert {c for c, v in hidden.items() if v is None} == _AMOUNT_COLS
    # 件数类照给，与可见时相同（含发货发布率）
    for c in set(shown) - _AMOUNT_COLS:
        assert hidden[c] == shown[c], c
    assert hidden["ship_publish_rate"] == Decimal("0.6667")


def test_sum_progress_rows_sums_then_rates() -> None:
    """按键相加的是精确值；比率在相加之后算（两行完成率不能取首行或求平均）。"""
    a = _row(publish_count=1, effective_quote_count=4, like_count=Decimal("1.5"), goods="g1")
    b = _row(publish_count=3, effective_quote_count=4, like_count=Decimal("1.5"), goods="g1")
    c = _row(goods="g2")
    summed = sum_progress_rows([c, a, b], key=lambda r: r["goods"])
    assert list(summed) == ["g2", "g1"]  # 第一次出现的顺序
    g1 = summed["g1"]
    assert set(g1) == set(PR_PROGRESS_METRICS)
    assert (g1["publish_count"], g1["effective_quote_count"], g1["like_count"]) == (4, 8, 3)
    assert g1["promo_cost"] == Decimal("2506")
    m = progress_metrics(g1, amounts=True)
    assert m["month_complete_rate"] == Decimal("0.5000")
    assert m["like_count"] == 3  # 1.5 + 1.5，逐行取整会得到 2


def test_sum_progress_rows_treats_none_as_zero() -> None:
    summed = sum_progress_rows([_row(cost=None)], key=lambda _r: None)
    assert summed[None]["cost"] == 0
