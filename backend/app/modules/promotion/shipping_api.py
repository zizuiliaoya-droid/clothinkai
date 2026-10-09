"""推广单发货接口（流程线 7.3 ``ship/*``；仓库页 ``/api/warehouse`` 随后放这里，7.4）。

scope 一律挂在路由层：``promotion_ship`` 是独立一级域，PR 的 ``promotion.*:*``、运营的 ``promotion.*:read`` 通配捞不到
（4.2）——缺 scope 在进 service 之前 403 ``PERMISSION_DENIED``。状态机 / 矩阵规则 / ★ 在 service 里按 7.1 的顺序判。
所有动作返回动作之后的整张推广单（带 ``ui``）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.promotion.deps import PromotionServiceDep
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionShipPushRequest,
    PromotionShipWithdrawRequest,
)

router = APIRouter(prefix="/api", tags=["promotion-shipping"])

_SHIP_PUSH = require_permission("promotion_ship", "push")


@router.post(
    "/promotions/{promotion_id}/ship/include",
    response_model=PromotionResponse,
    dependencies=[_SHIP_PUSH],
)
async def ship_include(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """S2 纳入发货：历史单（发货为空、未发布、未召回、启用）→ 待发货。"""
    return await service.ship_include(promotion_id, user)


@router.post(
    "/promotions/{promotion_id}/ship/push",
    response_model=PromotionResponse,
    dependencies=[_SHIP_PUSH],
)
async def ship_push(
    promotion_id: UUID,
    payload: PromotionShipPushRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """S3 确认推送仓库（管理员或 PR 主管）：待发货 → 待打单。

    body 是推送弹窗里补的颜色尺码与收件信息，与推送同一事务写入；写完仍缺收件三项 / 电话不合格 /
    颜色尺码不齐 → 422 ``FLOW_GATE_MISSING``（``missing`` 与 ``ui`` 逐条相同），整笔回滚。
    """
    return await service.ship_push(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/ship/withdraw",
    response_model=PromotionResponse,
    dependencies=[_SHIP_PUSH],
)
async def ship_withdraw(
    promotion_id: UUID,
    payload: PromotionShipWithdrawRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """S4 撤回推送：待打单 → 待发货，原因必填（1 ~ 500 字）。"""
    return await service.ship_withdraw(promotion_id, payload, user)


__all__ = ["router"]
