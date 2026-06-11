"""U04 urge_status 衍生字段计算（EP05-S06）。

按 nfr-design-patterns.md §5 + business-rules.md BR-U04-30 设计：
- Python 实现（service 层单条响应）+ SQL 表达式（列表 CTE）双实现
- **统一日期入口** ``get_today()``（FB8）：SQL 不用 CURRENT_DATE，传 ``:today`` 参数
- 时区固定 Asia/Shanghai（V1+ 评估按租户配置）

测试一致性（FB8）：
- ``test_urge_calculator_python_vs_sql_consistency``（freezegun + 100 mock）
- ``test_urge_status_at_scheduled_date``（边界日 == today）
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


DEFAULT_TENANT_TZ: ZoneInfo = ZoneInfo("Asia/Shanghai")
"""默认租户时区。MVP 阶段全部硬编码；V1+ 按 tenant.timezone 切换。"""


def get_today(tz: ZoneInfo = DEFAULT_TENANT_TZ) -> date:
    """统一日期获取入口。

    SQL 列表查询和 Python 单条响应必须使用同一个 ``today`` 值，
    否则边界日（如 23:59 UTC vs 00:00 GMT+8）可能产生不同分支。

    用法::

        today = get_today()
        # 1. service.list_promotions 透传给 SQL 表达式
        await session.execute(text("...:today..."), {"today": today, ...})
        # 2. service._to_response 透传给 Python 实现
        urge = calculate_urge_status(..., today=today, ...)
    """
    return datetime.now(tz).date()


def calculate_urge_status(
    *,
    publish_status: str,
    scheduled_publish_date: date | None,
    today: date,
    urge_threshold_days: int,
    important_threshold_days: int,
) -> str:
    """BR-U04-30: urge_status 计算（Python 实现）。

    与 ``URGE_STATUS_SQL_EXPR`` 必须保持完全一致的分支逻辑（FB8）。

    返回值（7 种）：
        - 已取消 / 已发布 / 已删除 / 未排期 / 档期内 / 催发 / 重要催发 / 超时
    """
    if publish_status == "已取消":
        return "已取消"
    if publish_status == "已发布":
        return "已发布"
    if publish_status not in {"未发布", "异常"}:
        return "已删除"
    if scheduled_publish_date is None:
        return "未排期"

    diff = (scheduled_publish_date - today).days
    if diff > urge_threshold_days:
        return "档期内"
    if diff > important_threshold_days:
        return "催发"
    if diff >= 0:
        return "重要催发"
    return "超时"


URGE_STATUS_SQL_EXPR: str = """
CASE
  WHEN publish_status = '已取消' THEN '已取消'
  WHEN publish_status = '已发布' THEN '已发布'
  WHEN publish_status NOT IN ('未发布', '异常') THEN '已删除'
  WHEN scheduled_publish_date IS NULL THEN '未排期'
  WHEN (scheduled_publish_date - :today) > :urge_days THEN '档期内'
  WHEN (scheduled_publish_date - :today) > :important_days THEN '催发'
  WHEN (scheduled_publish_date - :today) >= 0 THEN '重要催发'
  ELSE '超时'
END
"""
"""SQL 表达式片段（在 list CTE 中使用）。

绑定参数：
    :today           — 由 get_today() 注入（不用 CURRENT_DATE，FB8）
    :urge_days       — URGE_THRESHOLD_DAYS（10）
    :important_days  — IMPORTANT_THRESHOLD_DAYS（3）

依赖列：
    publish_status / scheduled_publish_date
"""


__all__ = [
    "DEFAULT_TENANT_TZ",
    "URGE_STATUS_SQL_EXPR",
    "calculate_urge_status",
    "get_today",
]
