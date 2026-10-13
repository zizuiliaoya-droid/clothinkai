"""工作进度导出的列定义（7e R5，设计 2.5）：三个 sheet 共用一段指标列。

- 「PR 进度」= PR + 指标段；
  「单品汇总」= 商品显示名 + 商品全称（7e R6；未归属留空）+ 指标段（末行「未归属商品」，不导商品编码）；
  「周明细」= 所在周的周一 + 实际起止 + 指标段
- 指标段覆盖 ``WorkProgressMetrics`` 除 ``cost`` 外的全部字段（``cost`` 是旧的样品成本，R5 起
  页面与导出都不再给，「成本」改成「推广成本」= ``promo_cost``）；
  ``tests/api/test_report_export_api.py`` 守「指标段 + cost = 模型字段」，模型加列漏了这里就红
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkProgressColumn:
    key: str  # 行模型的字段名
    header: str


WORK_PROGRESS_METRIC_COLUMNS: tuple[WorkProgressColumn, ...] = (
    WorkProgressColumn("quote_count", "约篇量"),
    WorkProgressColumn("in_schedule_count", "档期内"),
    WorkProgressColumn("urge_count", "催发"),
    WorkProgressColumn("important_urge_count", "重要催发"),
    WorkProgressColumn("overdue_count", "超时"),
    WorkProgressColumn("publish_count", "发布量"),
    WorkProgressColumn("info_complete_count", "信息完整数"),
    WorkProgressColumn("info_complete_rate", "信息完整率"),
    WorkProgressColumn("cancel_count", "取消量"),
    WorkProgressColumn("recall_due_count", "待召回"),
    WorkProgressColumn("recall_success_count", "召回成功"),
    WorkProgressColumn("recall_complete_rate", "召回完成率"),
    WorkProgressColumn("overdue_rate", "超时率"),
    WorkProgressColumn("month_complete_rate", "月度完成率"),
    WorkProgressColumn("hit_count", "爆文数"),
    WorkProgressColumn("hit_rate", "爆文率"),
    WorkProgressColumn("like_count", "点赞数"),
    WorkProgressColumn("promo_cost", "推广成本"),
    WorkProgressColumn("cpl", "CPL"),
    WorkProgressColumn("effective_quote_count", "有效约稿量"),
    WorkProgressColumn("unpublished_count", "未发布量"),
    WorkProgressColumn("quote_amount", "约稿服务费"),
    WorkProgressColumn("published_quote_amount", "已发布服务费"),
    WorkProgressColumn("effective_quote_amount", "有效约稿服务费"),
    WorkProgressColumn("unpublished_quote_amount", "未发布服务费"),
    WorkProgressColumn("cancelled_quote_amount", "取消服务费"),
    WorkProgressColumn("avg_published_quote", "发布单篇均价"),
    WorkProgressColumn("amount_publish_rate", "金额发布率"),
    WorkProgressColumn("total_amount_publish_rate", "总金额发布率"),
    WorkProgressColumn("shipped_count", "已发货"),
    WorkProgressColumn("shipped_published_count", "已发货且已发布"),
    WorkProgressColumn("ship_publish_rate", "发货发布率"),
)

WORK_PROGRESS_METRIC_HEADERS: tuple[str, ...] = tuple(
    c.header for c in WORK_PROGRESS_METRIC_COLUMNS
)

SHEET_PR = "PR 进度"
SHEET_GOODS = "单品汇总"
SHEET_WEEKS = "周明细"

PR_HEADERS: tuple[str, ...] = ("PR", *WORK_PROGRESS_METRIC_HEADERS)
GOODS_HEADERS: tuple[str, ...] = ("商品", "商品全称", *WORK_PROGRESS_METRIC_HEADERS)
WEEK_HEADERS: tuple[str, ...] = (
    "所在周（周一）",
    "实际起",
    "实际止",
    *WORK_PROGRESS_METRIC_HEADERS,
)


__all__ = [
    "GOODS_HEADERS",
    "PR_HEADERS",
    "SHEET_GOODS",
    "SHEET_PR",
    "SHEET_WEEKS",
    "WEEK_HEADERS",
    "WORK_PROGRESS_METRIC_COLUMNS",
    "WORK_PROGRESS_METRIC_HEADERS",
    "WorkProgressColumn",
]
