"""TASK 15 BI 看板 API（/api/reports/bi、/bi/layout，7e R6 加 /bi/goods/{id}/trend、/notes）。

这些端点挂 ``report.bi:read``（7e R1 从 ``report.production:read`` 拆出：给财务 BI 不连带放开投产页；
主管 / 运营由 ``report.*:read`` 覆盖）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Query

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.report.advanced_permissions import REPORT_BI_READ
from app.modules.report.advanced_schemas import BiDashboard, BiGoodsNotes, ProductionTrend
from app.modules.report.bi_service import DEFAULT_BI_LAYOUT
from app.modules.report.deps import BiServiceDep, UserPreferenceServiceDep
from app.modules.report.domain import resolve_time_range
from app.modules.report.field_mask import build_field_perm_context

router = APIRouter(prefix="/api/reports", tags=["report"])

_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]

_BI_LAYOUT_KEY = "bi_layout"


@router.get(
    "/bi",
    response_model=BiDashboard,
    dependencies=[require_permission(*REPORT_BI_READ)],
)
async def get_bi_dashboard(
    user: CurrentActiveUser,
    session: SessionDep,
    service: BiServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    granularity: Annotated[
        str,
        Query(pattern=r"^(day|week|month|year)$", description="趋势粒度"),
    ] = "day",
    exclude_brushing: Annotated[
        bool, Query(description="单品块与单品趋势的 ROI 是否扣刷单（7e R6）")
    ] = True,
) -> BiDashboard:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_dashboard(
        user.tenant_id,
        tr,
        granularity=granularity,
        exclude_brushing=exclude_brushing,
        field_ctx=await build_field_perm_context(user.id, session),
    )


@router.get(
    "/bi/goods/{goods_id}/trend",
    response_model=ProductionTrend,
    dependencies=[require_permission(*REPORT_BI_READ)],
)
async def get_bi_goods_trend(
    user: CurrentActiveUser,
    service: BiServiceDep,
    goods_id: UUID,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    granularity: Annotated[
        str,
        Query(pattern=r"^(day|week|month|year)$", description="趋势粒度"),
    ] = "day",
    exclude_brushing: bool = True,
) -> ProductionTrend:
    """Tab2 单品日趋势（7e R6）：参数与响应同 ``/production/trend``，判权挂 ``report.bi:read``。

    财务只有 BI 读权限，不能借 ``/production/trend``（挂 ``report.production:read``）。
    """
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_goods_trend(
        user.tenant_id,
        goods_id,
        tr,
        granularity=granularity,
        exclude_brushing=exclude_brushing,
    )


@router.get(
    "/bi/goods/{goods_id}/notes",
    response_model=BiGoodsNotes,
    dependencies=[require_permission(*REPORT_BI_READ)],
)
async def get_bi_goods_notes(
    user: CurrentActiveUser,
    session: SessionDep,
    service: BiServiceDep,
    goods_id: UUID,
    date_from: Annotated[date, Query(description="合作日期起（含）")],
    date_to: Annotated[date, Query(description="合作日期止（含）；跨度 ≤ 366 天")],
) -> BiGoodsNotes:
    """Tab2 笔记散点（7e R6）：≤ 500 点，超出取最近 500 并标 ``truncated``；逐单金额按 F2。"""
    return await service.get_goods_notes(
        user.tenant_id,
        goods_id,
        date_from,
        date_to,
        field_ctx=await build_field_perm_context(user.id, session),
    )


@router.get(
    "/bi/layout",
    dependencies=[require_permission(*REPORT_BI_READ)],
)
async def get_bi_layout(
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
) -> dict:
    return await service.get_or_default(user.id, _BI_LAYOUT_KEY, DEFAULT_BI_LAYOUT)


@router.put(
    "/bi/layout",
    dependencies=[require_permission(*REPORT_BI_READ)],
)
async def save_bi_layout(
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
    layout: Annotated[dict, Body(...)],
) -> dict:
    await service.upsert(user, _BI_LAYOUT_KEY, layout)
    return {"ok": True}


__all__ = ["router"]
