"""报表下钻 API ``GET /api/reports/drill/promotions``（7e R1，报表视图设计 2.1、5.1）。

判权：路由 ``require_any_permission(工作进度 read, read_own, report.bi:read)``；
持工作进度 read 或 ``report.bi:read`` 看全部（按请求的 ``pr_id``），只持 ``read_own`` 时服务端
强制本人（传了别人的 id 也忽略，不报错）—— ``resolve_pr_scope`` 一处判定。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.modules.auth.deps import (
    CurrentActiveUser,
    CurrentPerms,
    SessionDep,
    require_any_permission,
)
from app.modules.report.advanced_permissions import (
    REPORT_BI_READ,
    REPORT_WORK_PROGRESS_READ_OWN,
)
from app.modules.report.advanced_schemas import DrillPromotionPage
from app.modules.report.deps import DrillServiceDep
from app.modules.report.field_mask import build_field_perm_context
from app.modules.report.pr_scope import parse_pr_param, resolve_pr_scope

router = APIRouter(prefix="/api/reports/drill", tags=["report"])

_WP_READ = ("report.work_progress", "read")


@router.get(
    "/promotions",
    response_model=DrillPromotionPage,
    dependencies=[require_any_permission(_WP_READ, REPORT_WORK_PROGRESS_READ_OWN, REPORT_BI_READ)],
)
async def list_drill_promotions(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: SessionDep,
    service: DrillServiceDep,
    date_from: Annotated[date, Query(description="合作日期起（含）")],
    date_to: Annotated[date, Query(description="合作日期止（含）；跨度 ≤ 366 天")],
    pr_id: Annotated[str | None, Query(description="PR 的 UUID 或 unassigned")] = None,
    goods_id: Annotated[UUID | None, Query(description="归属商品")] = None,
    season: Annotated[list[str] | None, Query(description="季节多选，≤ 20 个")] = None,
    publish_status: Annotated[Literal["已发布", "未发布", "已取消"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DrillPromotionPage:
    """区间内（按合作日期）的推广单明细，合作日期倒序。字段白名单、金额按 F2 逐行裁剪。"""
    scope = resolve_pr_scope(
        perms,
        user.id,
        parse_pr_param(pr_id),
        _WP_READ[0],
        also_full=(REPORT_BI_READ,),
    )
    return await service.list_promotions(
        user.tenant_id,
        date_from,
        date_to,
        pr_scope=scope,
        goods_id=goods_id,
        seasons=season,
        publish_status=publish_status,
        page=page,
        page_size=page_size,
        field_ctx=await build_field_perm_context(user.id, session),
    )


__all__ = ["router"]
