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

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TENANT_TZ: ZoneInfo = ZoneInfo("Asia/Shanghai")
"""默认租户时区。MVP 阶段全部硬编码；V1+ 按 tenant.timezone 切换。"""


@dataclass(frozen=True)
class UrgeThresholds:
    """urge_status 的两个分界天数，来自租户的 ``urge_config``（后台可改）。

    取值走 ``UrgeService.get_urge_thresholds``。这里不给默认值：以前列表、详情、
    工作进度各写死一份 10 / 3，后台改了阈值只有企微扫描生效，页面上的标签和报表
    计数纹丝不动 —— 所以每个调用方都必须显式拿到租户配置再传进来。
    """

    urge_days: int
    """距预定发布日 > 这么多天为「档期内」，否则进入「催发」。"""

    important_days: int
    """距预定发布日 ≤ 这么多天（且未过期）为「重要催发」。"""

    def sql_params(self) -> dict[str, int]:
        """``URGE_STATUS_SQL_EXPR`` 需要的两个绑定参数。"""
        return {"urge_days": self.urge_days, "important_days": self.important_days}


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
    :urge_days       — 租户 urge_config.urge_threshold_days（``UrgeThresholds.sql_params()``）
    :important_days  — 租户 urge_config.important_threshold_days

「超时」只取决于 ``scheduled_publish_date < today``，与两个阈值无关；只数超时的查询
（BI 工作量、发文进度）传进来的阈值不影响结果，但 SQL 里仍需要这两个参数。

依赖列：
    publish_status / scheduled_publish_date
"""


__all__ = [
    "DEFAULT_TENANT_TZ",
    "URGE_STATUS_SQL_EXPR",
    "UrgeThresholds",
    "calculate_urge_status",
    "get_today",
]
