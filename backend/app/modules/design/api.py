"""U10a design 模块 FastAPI router（设计制版全流程，EP03-S02~S14）。

路由前缀 /api/designs；各动作权限见 modules/design/permissions.py。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.design import permissions as scopes
from app.modules.design.deps import DesignServiceDep
from app.modules.design.schemas import (
    CancelRequest,
    CostingSubmit,
    CraftSubmit,
    DesignCreate,
    DesignDetailResponse,
    DesignListResponse,
    FabricComplete,
    FabricSubmit,
    GradingSubmit,
    PatternSubmit,
    RejectRequest,
    TagPriceSubmit,
)

router = APIRouter(prefix="/api/designs", tags=["design"])


@router.post(
    "/",
    response_model=DesignDetailResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_WRITE, "write")],
)
async def create_design(
    payload: DesignCreate, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S02 设计师上传设计稿创建款式。"""
    return await svc.create_design(payload, user)


@router.get(
    "/",
    response_model=DesignListResponse,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_READ, "read")],
)
async def list_designs(svc: DesignServiceDep, user: CurrentActiveUser) -> DesignListResponse:
    """EP03-S01 按状态分组的设计制版看板。"""
    return await svc.list_designs(user)


@router.get(
    "/{style_id}",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_READ, "read")],
)
async def get_design(
    style_id: UUID, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    return await svc.get_detail(style_id, user)


@router.put(
    "/{style_id}/fabric",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_WRITE, "write")],
)
async def submit_fabric(
    style_id: UUID, payload: FabricSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S03 设计师填写面辅料 → 制版中。"""
    return await svc.submit_fabric(style_id, payload, user)


@router.put(
    "/{style_id}/pattern",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_PATTERN_WRITE, "write")],
)
async def submit_pattern(
    style_id: UUID, payload: PatternSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S04 版师提交版型与版号（原地）。"""
    return await svc.submit_pattern(style_id, payload, user)


@router.put(
    "/{style_id}/grading",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_PATTERN_WRITE, "write")],
)
async def submit_grading(
    style_id: UUID, payload: GradingSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S05 版师放码 → 工艺录入。"""
    return await svc.submit_grading(style_id, payload, user)


@router.put(
    "/{style_id}/craft",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_CRAFT_WRITE, "write")],
)
async def submit_craft(
    style_id: UUID, payload: CraftSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S07 跟单录入工艺 → 待补全。"""
    return await svc.submit_craft(style_id, payload, user)


@router.put(
    "/{style_id}/fabric/complete",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_COSTING_WRITE, "write")],
)
async def complete_fabric(
    style_id: UUID, payload: FabricComplete, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S08 设计助理面辅料补齐（原地）。"""
    return await svc.complete_fabric(style_id, payload, user)


@router.put(
    "/{style_id}/complete",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_COSTING_WRITE, "write")],
)
async def submit_costing(
    style_id: UUID, payload: CostingSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S09 设计助理填写核价信息 → 待核价（自动核价）。"""
    return await svc.submit_costing(style_id, payload, user)


@router.put(
    "/{style_id}/tag-price",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_TAG_PRICE_WRITE, "write")],
)
async def set_tag_price(
    style_id: UUID, payload: TagPriceSubmit, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S10 跟单填写吊牌价（原地）。"""
    return await svc.set_tag_price(style_id, payload, user)


@router.put(
    "/{style_id}/confirm-price",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_CONFIRM_PRICE, "approve")],
)
async def confirm_price(
    style_id: UUID, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S11 跟单价格确认转大货。"""
    return await svc.confirm_price(style_id, user)


@router.put(
    "/{style_id}/reject",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_READ, "read")],
)
async def reject_design(
    style_id: UUID, payload: RejectRequest, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S06/S12 驳回到上一环节（service 校验角色 + 状态合法性）。"""
    return await svc.reject(style_id, payload.reason, user)


@router.put(
    "/{style_id}/cancel",
    response_model=DesignDetailResponse,
    dependencies=[require_permission(scopes.SCOPE_DESIGN_READ, "read")],
)
async def cancel_design(
    style_id: UUID, payload: CancelRequest, svc: DesignServiceDep, user: CurrentActiveUser
) -> DesignDetailResponse:
    """EP03-S13 管理员取消款式（service 校验 admin）。"""
    return await svc.cancel(style_id, payload.reason, user)


__all__ = ["router"]
