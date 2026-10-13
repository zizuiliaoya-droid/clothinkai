"""U14 报表进阶 API（工作进度/爆款约篇/店铺数据/投产报表，6 端点）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, status

from app.modules.auth.deps import (
    CurrentActiveUser,
    CurrentPerms,
    SessionDep,
    require_any_permission,
    require_permission,
)
from app.modules.product.goods_schemas import SeasonOptionsResponse
from app.modules.product.season_options import list_season_options
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_permissions import REPORT_WORK_PROGRESS_READ_OWN
from app.modules.report.advanced_schemas import (
    ProductionReport,
    ProductionTrend,
    PrWorkProgress,
    ReportFreshnessOut,
    StoreDailyManualUpdate,
    StoreDailyRow,
    StoreReport,
    TargetCreate,
    TargetWithActual,
)
from app.modules.report.deps import (
    ProductionServiceDep,
    StoreDailyServiceDep,
    SummaryRefreshServiceDep,
    TargetPlanningServiceDep,
    WorkProgressServiceDep,
)
from app.modules.report.domain import resolve_time_range
from app.modules.report.field_mask import build_field_perm_context, production_margin_visible
from app.modules.report.pr_scope import resolve_pr_scope
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.work_progress_service import _month_range

router = APIRouter(prefix="/api/reports", tags=["report"])

_WP_READ = ("report.work_progress", "read")

_MonthQ = Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]
_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]


# ----------------------------- 工作进度 ----------------------------- #


@router.get(
    "/work-progress",
    response_model=list[PrWorkProgress],
    dependencies=[require_any_permission(_WP_READ, REPORT_WORK_PROGRESS_READ_OWN)],
)
async def get_work_progress(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: SessionDep,
    service: WorkProgressServiceDep,
    month: Annotated[
        str | None, Query(pattern=r"^\d{4}-\d{2}$", description="旧参数：整月（留一个版本）")
    ] = None,
    preset: _PresetQ = "this_month",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    season: Annotated[list[str] | None, Query(description="季节多选，≤ 20 个")] = None,
) -> list[PrWorkProgress]:
    """工作进度（区间 × PR）。传了 ``month`` 就按整月，否则按 ``preset`` / 自定义区间。

    只持 ``read_own`` 的 PR 只看到本人那一行（service 层过滤，``resolve_pr_scope``）。
    ``season``（7e R5）：只统计归属商品季节命中的单。
    """
    if month is not None:
        lo, hi = _month_range(month)
    else:
        lo, hi = resolve_time_range(preset, date_from, date_to)
    scope = resolve_pr_scope(perms, user.id, None, _WP_READ[0])
    return await service.get_for_range(
        user.tenant_id,
        lo,
        hi,
        seasons=season,
        scope=scope,
        field_ctx=await build_field_perm_context(user.id, session),
    )


# ----------------------------- 爆款约篇 ----------------------------- #


@router.post(
    "/targets",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("report.target", "write")],
)
async def set_target(
    payload: TargetCreate,
    user: CurrentActiveUser,
    service: TargetPlanningServiceDep,
) -> dict:
    await service.set_target(payload, user)
    return {"ok": True}


@router.get(
    "/targets",
    response_model=list[TargetWithActual],
    dependencies=[require_permission("report.target", "read")],
)
async def list_targets(
    user: CurrentActiveUser,
    service: TargetPlanningServiceDep,
    month: _MonthQ,
) -> list[TargetWithActual]:
    return await service.list_with_actuals(user.tenant_id, month)


# ----------------------------- 店铺数据 ----------------------------- #


_STORE_READ = [require_permission("report.store_daily", "read")]


@router.get("/store/daily", response_model=StoreReport, dependencies=_STORE_READ)
async def get_store_report_daily(
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> StoreReport:
    """店铺日明细（7e R4）：一天一行，``date_from`` = ``date_to``。"""
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_store_report(user.tenant_id, tr, "day")


@router.get("/store/weekly", response_model=StoreReport, dependencies=_STORE_READ)
async def get_store_report_weekly(
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> StoreReport:
    """店铺周汇总：ISO 周 ∩ 请求区间一行，行上是实际起止。"""
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_store_report(user.tenant_id, tr, "week")


@router.get("/store/monthly", response_model=StoreReport, dependencies=_STORE_READ)
async def get_store_report_monthly(
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> StoreReport:
    """店铺月度汇总：自然月 ∩ 请求区间一行，行上是实际起止。"""
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_store_report(user.tenant_id, tr, "month")


@router.get(
    "/store-daily",
    response_model=list[StoreDailyRow],
    dependencies=_STORE_READ,
    deprecated=True,
)
async def get_store_daily(
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    granularity: Annotated[
        str,
        Query(pattern=r"^(day|week|month|year)$", description="统计粒度"),
    ] = "day",
) -> list[StoreDailyRow]:
    """店铺数据；按周 / 月 / 年时 date 是桶首日（不传 = 按日，旧调用不变）。

    7e R4 起由 ``/store/{daily,weekly,monthly}`` 取代，保留一个版本，返回结构不变。
    """
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_dashboard(user.tenant_id, tr, granularity=granularity)


@router.put(
    "/store-daily/{day}",
    dependencies=[require_permission("report.store_daily", "write")],
)
async def update_store_daily(
    day: Annotated[date, Path()],
    payload: StoreDailyManualUpdate,
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
) -> dict:
    """手填三项、仅退款与备注：只写传了的字段，传 ``null`` 清空；``day`` 晚于今天 → 422。"""
    row = await service.upsert_manual(user.tenant_id, day, payload, user)
    amounts = ("ad_spend_total", "zhitongche_spend", "yinli_spend", "refund_only_amount")
    return {
        "ok": True,
        "date": str(row.date),
        **{k: None if getattr(row, k) is None else str(getattr(row, k)) for k in amounts},
        "remark": row.remark,
    }


# ----------------------------- 投产报表 ----------------------------- #


@router.get(
    "/production",
    response_model=ProductionReport,
    dependencies=[require_permission("report.production", "read")],
)
async def get_production(
    user: CurrentActiveUser,
    service: ProductionServiceDep,
    session: SessionDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    exclude_brushing: bool = True,
    season: Annotated[list[str] | None, Query(description="季节多选")] = None,
) -> ProductionReport:
    # 类目筛选已下线（8a-3，J12）：旧客户端带 category 被 FastAPI 忽略，结果与不带一致
    tr = resolve_time_range(preset, date_from, date_to)
    field_ctx = await build_field_perm_context(user.id, session)
    return await service.get_report(
        user.tenant_id,
        tr,
        exclude_brushing=exclude_brushing,
        seasons=season,
        show_margin=production_margin_visible(field_ctx),
    )


@router.get(
    "/production/trend",
    response_model=ProductionTrend,
    dependencies=[require_permission("report.production", "read")],
)
async def get_production_trend(
    user: CurrentActiveUser,
    service: ProductionServiceDep,
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
    """单个商品的投产趋势；date 表示日或周/月/年桶的起始日期。"""
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_trend(
        user.tenant_id,
        goods_id,
        tr,
        granularity=granularity,
        exclude_brushing=exclude_brushing,
    )


# --------------------------- 汇总表手动刷新 --------------------------- #


@router.get("/summaries/freshness", response_model=ReportFreshnessOut)
async def get_summary_freshness(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    session: SessionDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> ReportFreshnessOut:
    """报表区间的数据新鲜度：读的是汇总表还是实时、截至几点、能否手动刷新。

    与报表接口用同一个 ``resolve_time_range`` 与同一个覆盖判断，前端拿到的结论与
    报表实际走的路径一致（两次请求之间恰好有刷新提交的话可能差一次，下次请求即一致）。

    只要求登录：返回的是刷新时间与数据来源，不含业务数字；``can_refresh`` 让前端
    按真实权限显示刷新按钮 —— ``/me`` 只返回角色，前端没法自己判断 scope。
    """
    lo, hi = resolve_time_range(preset, date_from, date_to)
    fresh = await SummaryReadRepository(session).freshness(user.tenant_id, lo, hi)
    return ReportFreshnessOut(
        date_from=lo,
        date_to=hi,
        source=fresh.source,
        data_as_of=fresh.data_as_of,
        can_refresh=perms.has("report.summary", "refresh"),
    )


@router.post(
    "/summaries/refresh",
    dependencies=[require_permission("report.summary", "refresh")],
)
async def refresh_summaries(
    user: CurrentActiveUser,
    service: SummaryRefreshServiceDep,
    session: SessionDep,
    date_from: Annotated[date, Query(description="刷新区间起（含）")],
    date_to: Annotated[date, Query(description="刷新区间止（含）")],
) -> dict:
    """按需补算历史区间的汇总表（PRD：「历史数据只能页面手动刷新」）。

    定时任务只滚动刷新最近 31 天（见 ``summary_tasks.REFRESH_WINDOW_DAYS``），导入完成后
    会自动刷新那批数据涉及的已覆盖日期。剩下的历史改动（例如在页面上发布一张几个月前
    合作的推广单、改了商品与链接的对应关系）由这里补。

    并发：同租户已有刷新在进行中（定时任务或另一个手动刷新）时返回 409
    ``REPORT_SUMMARY_REFRESH_BUSY``。「区间删 + 批量插」不能并发 —— 见
    ``summary_refresh_service`` 模块 docstring「为什么要加锁」。

    区间走 ``resolve_time_range("custom", ...)`` 校验而不是另写一套：这个端点会删掉
    区间内所有汇总行再重建，不限长度一次调用就能触发全库重算，而「date_from ≤ date_to
    且跨度 ≤ 366 天」这条规则其他报表端点已经在用，没理由在这里定义第二份。

    **上沿截到今天**：刷新会给区间内每一天记覆盖，读取侧据此改读汇总表。未来的日子
    刷不「完整」—— 合作日期填在未来的推广单之后才录入的话，汇总表会一直是 0，而实时
    路径能看到它。与导入触发的刷新（``target_runs``）同一条规则。
    """
    lo, hi = resolve_time_range("custom", date_from, date_to)
    hi = min(hi, get_today())
    if lo > hi:
        # 整段都在未来：没有能刷的日子
        return {"ok": True, "date_from": str(lo), "date_to": str(lo), "skipped": "future"}
    counts = await service.refresh(tenant_id=user.tenant_id, date_from=lo, date_to=hi)
    # service 不 commit：5 张表要么一起生效要么一起回滚
    await session.commit()
    return {"ok": True, "date_from": str(lo), "date_to": str(hi), **counts}


# ----------------------------- 季节选项 ----------------------------- #


@router.get(
    "/season-options",
    response_model=SeasonOptionsResponse,
    dependencies=[
        require_any_permission(
            ("report.production", "read"), _WP_READ, REPORT_WORK_PROGRESS_READ_OWN
        )
    ],
)
async def get_report_season_options(
    user: CurrentActiveUser,
    session: SessionDep,
) -> SeasonOptionsResponse:
    """投产页 / 工作进度页季节下拉（8a-3，§8.3，C-02；7e R5）：字典 season 启用值 + 商品上出现过的值。

    只要求投产或工作进度读权限——主管没有 ``product:read``，原来调 ``/api/dict-items`` 拿到的是
    空下拉；只回季节名，PR（``read_own``）与财务也能拿到。
    """
    return SeasonOptionsResponse(items=await list_season_options(session, user.tenant_id))


__all__ = ["router"]
