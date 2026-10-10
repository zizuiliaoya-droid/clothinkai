"""推广单发货接口（流程线 7.3 ``ship/*`` 与仓库回填）与仓库页 ``/api/warehouse``（7.4）。

scope 一律挂在路由层：``promotion_ship`` 是独立一级域，PR 的 ``promotion.*:*``、运营的 ``promotion.*:read``
通配捞不到（4.2）——缺 scope 在进 service 之前 403 ``PERMISSION_DENIED``。状态机 / 矩阵规则 / ★ 在 service 里按 7.1 的顺序判。
``ship/*`` 返回动作之后的整张推广单（带 ``ui``）；仓库端点（列表、导出、回填）是 7.1 的例外，只回
``WarehouseShipmentRow`` 投影——仓库 060 起没有 ``promotion:read``，不能从回填响应拿到整张单。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.promotion.deps import PromotionServiceDep
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionShipPushRequest,
    PromotionShipWithdrawRequest,
    PromotionWarehouseWaybillRequest,
    WarehouseBucket,
    WarehouseShipmentPage,
    WarehouseShipmentRow,
)

router = APIRouter(prefix="/api", tags=["promotion-shipping"])

_SHIP_PUSH = require_permission("promotion_ship", "push")
_SHIP_FILL = require_permission("promotion_ship", "fill")
_SHIP_EXPORT = require_permission("promotion_ship", "export")

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


@router.patch(
    "/promotions/{promotion_id}/warehouse-waybill",
    response_model=WarehouseShipmentRow,
    dependencies=[_SHIP_FILL],
)
async def update_warehouse_waybill(
    promotion_id: UUID,
    payload: PromotionWarehouseWaybillRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> WarehouseShipmentRow:
    """S5 / S6 仓库回填与改快递信息：只接受待打单 / 已发货；发货时间默认现在、不能晚于现在。"""
    return await service.update_warehouse_waybill(promotion_id, payload, user)


@router.get(
    "/warehouse/shipments",
    response_model=WarehouseShipmentPage,
    dependencies=[_SHIP_FILL],
)
async def list_warehouse_shipments(
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    bucket: WarehouseBucket = "待打单",
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> WarehouseShipmentPage:
    """仓库页列表：待打单（默认，先推先打）/ 已发货（发货时间倒序）/ 全部（= 前两者）。"""
    return await service.list_warehouse_shipments(
        bucket=bucket, keyword=keyword, page=page, page_size=page_size, user=user
    )


@router.get(
    "/warehouse/shipments/export",
    response_class=Response,
    dependencies=[_SHIP_EXPORT],
)
async def export_warehouse_shipments(
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    bucket: WarehouseBucket = "待打单",
    keyword: Annotated[str | None, Query(max_length=64)] = None,
) -> Response:
    """导出 xlsx（参数同列表）。一行一个商品明细；超过 5,000 张单 → 422 ``EXPORT_TOO_MANY_ROWS``。"""
    content = await service.export_warehouse_shipments(bucket=bucket, keyword=keyword, user=user)
    return Response(
        content=content,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": 'attachment; filename="shipments.xlsx"'},
    )


__all__ = ["router"]
