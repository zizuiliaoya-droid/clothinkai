"""报表的字段级裁剪（7e R1，报表视图设计第 4 节「字段级」）。

report 模块原来没有字段裁剪；下钻（R1）、散点（R6）、毛利（R3）都从这里取规则，页面与导出同规则。
每请求只构造一次 :func:`build_field_perm_context`，再按下面的规则把金额置 ``null``：

- **F2 逐单金额**（下钻、散点、明细行）：服务费要 ``promotion.quote_amount`` 可读；
  站外推广成本要 ``quote_amount`` 与 ``cost_snapshot`` **都**可读（成本减样品费就是服务费，
  同推广详情的规则）
- **F3 聚合金额**（工作进度、BI、投产、店铺里按 PR / 商品 / 周 / 日聚合的服务费系与推广成本）：
  按报表读权限给，不受逐单字段权限约束（设计第 11 节决定 12）。残余：样本只有一单时聚合额等于单价。
  业务方要更严时**只改** :func:`aggregate_promo_amounts_visible`：对 ``work_progress`` / ``bi``
  返回 ``can_read_field("promotion", "quote_amount", ctx)``，``production`` / ``store_daily`` 照常 True（ROI 要用）
- F1 毛利随 R3
"""

from __future__ import annotations

from typing import Literal, get_args
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import FieldPermissionContext, can_read_field
from app.core.security.field_permissions import build_field_perm_context as _core_build
from app.modules.auth.repository import PermissionRepository, RoleRepository

# 有推广金额聚合列的报表（F3 的调用点按这里的名字传）
AggregateReport = Literal["work_progress", "bi", "production", "store_daily"]
AGGREGATE_REPORTS: frozenset[str] = frozenset(get_args(AggregateReport))


async def build_field_perm_context(user_id: UUID, session: AsyncSession) -> FieldPermissionContext:
    """当前用户的字段权限上下文（每请求一次；包 core 的同名函数）。"""
    return await _core_build(user_id, RoleRepository(session), PermissionRepository(session))


def promotion_amounts_visible(ctx: FieldPermissionContext) -> tuple[bool, bool]:
    """F2：``(服务费 quote_amount 可见, 站外推广成本 total_promo_cost 可见)``。"""
    quote = can_read_field("promotion", "quote_amount", ctx)
    return quote, quote and can_read_field("promotion", "cost_snapshot", ctx)


def aggregate_promo_amounts_visible(report: AggregateReport, ctx: FieldPermissionContext) -> bool:
    """F3：该报表的推广金额聚合列是否给（R1 恒 True，决定 12 的唯一落点）。"""
    del report, ctx  # 现行口径不看逐单字段权限；收紧时按模块 docstring 改这里
    return True


__all__ = [
    "AGGREGATE_REPORTS",
    "AggregateReport",
    "aggregate_promo_amounts_visible",
    "build_field_perm_context",
    "can_read_field",
    "promotion_amounts_visible",
]
