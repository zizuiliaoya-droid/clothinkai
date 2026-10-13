"""工作进度的行构造（7e R5，报表视图设计 2.5）：PR 表、单品卡片、周明细三条路径共用。

纯函数，不碰数据库：输入是仓储行（``aggregate_by_pr`` / ``SummaryReadRepository`` 的
``work_progress_by_*``，列 = ``PR_PROGRESS_METRICS``），输出是 ``WorkProgressMetrics`` 的字段。

比率一律在**求和之后**现算（``safe_div``，分母 ≤ 0 → null）：按商品 / 按周合并多行时，
先用 ``sum_progress_rows`` 把计数与金额相加，再交给 ``progress_metrics``。
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, TypeVar

from app.modules.report.summary_models import PR_PROGRESS_METRICS
from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")
_CENT = Decimal("0.01")

# 金额列：F3 读不到时置 null；两条路径写法可能不同（"0" vs "0.00"），统一两位小数
_MONEY = (
    "cost",
    "quote_amount",
    "published_quote_amount",
    "effective_quote_amount",
    "unpublished_quote_amount",
    "cancelled_quote_amount",
    "promo_cost",
)

K = TypeVar("K", bound=Hashable)


def _money(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def progress_metrics(r: Mapping[str, Any], *, amounts: bool) -> dict[str, Any]:
    """仓储行 → 指标字段（``WorkProgressMetrics``）。

    ``amounts``（F3 聚合金额可见）必填：False 时金额列与金额派生的四列（单篇均价、两个金额发布率、
    CPL）置 null，``ship_publish_rate`` 是件数比照给。
    """
    quote = int(r["quote_count"])
    publish = int(r["publish_count"])
    effective_quote = int(r["effective_quote_count"])
    money = {c: _money(r[c]) for c in _MONEY}
    published_quote = money["published_quote_amount"]
    out: dict[str, Any] = {
        "quote_count": quote,
        "in_schedule_count": int(r["in_schedule_count"]),
        "urge_count": int(r["urge_count"]),
        "important_urge_count": int(r["important_urge_count"]),
        "overdue_count": int(r["overdue_count"]),
        "publish_count": publish,
        "info_complete_count": int(r["info_complete_count"]),
        "info_complete_rate": safe_div(r["info_complete_count"], publish, quantize=_Q4),
        "cancel_count": int(r["cancel_count"]),
        "recall_due_count": int(r["recall_due_count"]),
        "recall_success_count": int(r["recall_success_count"]),
        "recall_complete_rate": safe_div(
            r["recall_success_count"], r["recall_due_count"], quantize=_Q4
        ),
        "effective_quote_count": effective_quote,
        # PRD 第 9 章：超时率 / 完成率的分母要扣掉召回量与取消量。
        # 用裸约稿量做分母会把已取消、已召回的单也算成「待发布」，完成率被系统性低估。
        "overdue_rate": safe_div(r["overdue_count"], effective_quote, quantize=_Q4),
        "month_complete_rate": safe_div(publish, effective_quote, quantize=_Q4),
        "hit_count": int(r["hit_count"]),
        "hit_rate": safe_div(r["hit_count"], publish, quantize=_Q4),
        "like_count": int(r["like_count"]),
        "unpublished_count": int(r["unpublished_count"]),
        "shipped_count": int(r["shipped_count"]),
        "shipped_published_count": int(r["shipped_published_count"]),
        # 发货发布率 =（已发货且已发布）÷ 已发货
        "ship_publish_rate": safe_div(
            r["shipped_published_count"], r["shipped_count"], quantize=_Q4
        ),
    }
    if not amounts:
        out.update({c: None for c in _MONEY})
        out.update(
            avg_published_quote=None,
            amount_publish_rate=None,
            total_amount_publish_rate=None,
            cpl=None,
        )
        return out
    out.update(money)
    out.update(
        # 发布单篇均价 = 已发布服务费 ÷ 发布量
        avg_published_quote=safe_div(published_quote, publish, quantize=_CENT),
        # 金额发布率 = 已发布服务费 ÷ 总约稿服务费
        amount_publish_rate=safe_div(published_quote, money["quote_amount"], quantize=_Q4),
        # 总金额发布率 = 已发布服务费 ÷ 有效约稿服务费（扣取消与召回，同完成率分母）
        total_amount_publish_rate=safe_div(
            published_quote, money["effective_quote_amount"], quantize=_Q4
        ),
        # CPL = 推广成本 ÷ 折算点赞（默 17）；点赞用未取整的和
        cpl=safe_div(money["promo_cost"], r["like_count"], quantize=_Q4),
    )
    return out


def sum_progress_rows(
    rows: Iterable[Mapping[str, Any]], key: Callable[[Mapping[str, Any]], K]
) -> dict[K, dict[str, Decimal]]:
    """按 ``key`` 把仓储行的 ``PR_PROGRESS_METRICS`` 逐列相加（精确值，取整留给 ``progress_metrics``）。

    返回的 dict 按 key 第一次出现的顺序；比率不在这里算。
    """
    out: dict[K, dict[str, Decimal]] = {}
    for r in rows:
        acc = out.setdefault(key(r), dict.fromkeys(PR_PROGRESS_METRICS, Decimal(0)))
        for c in PR_PROGRESS_METRICS:
            acc[c] += Decimal(str(r[c] or 0))
    return out


__all__ = ["progress_metrics", "sum_progress_rows"]
