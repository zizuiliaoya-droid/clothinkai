"""报表中间汇总表的定时刷新（PRD V1.4 模块三「每小时增量刷新」）。

## 为什么是独立的 task module

``report_tasks.precompute_report_cache`` 是 U14 留的占位，至今是 noop。那个名字描述
的是「报表缓存」，而这里写的是 5 张有 schema 的汇总表 —— 不是缓存，是可查询的派生
数据。混在一起会让「noop 占位」和「真的在跑的任务」看起来是同一件事。

## 刷新窗口为什么是 31 天

汇总表的意义是服务常用区间（本月 / 上月 / 最近 30 天），这些都落在最近 31 天内。
全量回算要扫 ``promotion`` 的 5156 行 × 每日一次聚合，没有收益。

窗口之外：

- **导入**完成后自动刷新那批数据涉及的日期（``refresh_report_summaries_for_dates``，
  方案 2）—— 只刷已被覆盖的日子，没覆盖的历史本来就走实时，见 ``target_runs``；
- 其余历史改动（例如在页面上发布一张三个月前合作的推广单）按 PRD「历史数据只能页面
  手动刷新」，由手动刷新端点补。

## 单租户失败不中断其他租户

与 ``urge_tasks`` 同样的写法（P-U07-03/04）：bypass 读租户元数据，每个租户单独
``AsyncSessionApp`` + ``set_config`` 走 RLS。一个租户的数据有问题不该让其他租户
的报表也停在旧数字上。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import sentry_sdk
from celery import Task
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.exceptions import SummaryRefreshBusyError
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)

# 滚动刷新窗口。覆盖「本月 + 上月」的常用区间：月初那天往前 31 天刚好能把
# 上个月整月带上，月汇总那一行才不会只含本月已过的几天。
REFRESH_WINDOW_DAYS = 31

# 导入后刷新撞上别的刷新（每小时那次或页面手动刷新）时的重试。生产上一次完整刷新
# 约 1 秒，一分钟一次、最多十次足够等过去；再不行说明有刷新卡住了，交给 Sentry。
_BUSY_RETRY_SECONDS = 60
_BUSY_MAX_RETRIES = 10


@celery_app.task(name="app.tasks.summary_tasks.refresh_report_summaries", queue="report")
def refresh_report_summaries(window_days: int | None = None) -> dict[str, Any]:
    """刷新所有活跃租户最近 ``window_days`` 天的汇总表。"""
    return run_async_task(_refresh_all(window_days or REFRESH_WINDOW_DAYS))


async def _refresh_all(window_days: int) -> dict[str, Any]:
    today = get_today()
    date_from = today - timedelta(days=window_days - 1)
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (
                await meta.execute(
                    text("SELECT id FROM tenant WHERE status = 'active' AND deleted_at IS NULL")
                )
            )
            .scalars()
            .all()
        )

    totals: dict[str, int] = {}
    failed = 0
    busy = 0
    for tid in tenant_ids:
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context(), AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)}
                )
                counts = await SummaryRefreshService(s).refresh(
                    tenant_id=tid, date_from=date_from, date_to=today
                )
                # 5 张表 + 覆盖记录一起 commit：中间状态（商品 ROI 刷了、店铺日报没刷）
                # 会让两张报表对不上账。commit 同时释放刷新锁。
                await s.commit()
            for table, n in counts.items():
                totals[table] = totals.get(table, 0) + n
        except SummaryRefreshBusyError:
            # 有人正在手动刷新这个租户 —— 跳过本轮，下个小时自然补上。
            # 这不是故障，不报 Sentry；记一笔 info 方便排查「为什么这小时没刷」。
            busy += 1
            log.info("summary_refresh_tenant_busy", extra={"tenant_id": str(tid)})
        except Exception as exc:
            failed += 1
            log.exception("summary_refresh_tenant_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)

    return {
        "tenants": len(tenant_ids),
        "failed": failed,
        "busy": busy,
        "date_from": date_from.isoformat(),
        "date_to": today.isoformat(),
        **totals,
    }


# --------------------------------------------------------------------------- #
# 导入完成后刷新（方案 2）
# --------------------------------------------------------------------------- #


@celery_app.task(
    bind=True,
    name="app.tasks.summary_tasks.refresh_report_summaries_for_dates",
    queue="report",
    max_retries=_BUSY_MAX_RETRIES,
)
def refresh_report_summaries_for_dates(
    self: Task, tenant_id: str, date_from: str, date_to: str
) -> dict[str, Any]:
    """一批导入写完后，刷新它涉及的日期（由 ``import_tasks._enqueue_summary_refresh`` 投递）。

    撞上别的刷新时**重试而不是放弃**：每小时那次撞锁可以跳过（下个小时补），这里不行 ——
    窗口外的历史日期没有下个小时，丢了就一直是旧数字。
    """
    try:
        return run_async_task(
            _refresh_dates(
                UUID(tenant_id), date.fromisoformat(date_from), date.fromisoformat(date_to)
            )
        )
    except SummaryRefreshBusyError as exc:
        raise self.retry(exc=exc, countdown=_BUSY_RETRY_SECONDS) from exc


def target_runs(
    date_from: date,
    date_to: date,
    covered: Iterable[date],
    window_lo: date,
    today: date,
) -> list[tuple[date, date]]:
    """导入涉及的日期里，哪些要刷新 —— 按连续区间返回。

    只刷两类日子：

    - **已经被覆盖的**：读取侧会读它的汇总行，不刷就是旧数字；
    - **在每小时刷新的窗口里的**：反正一小时内也会刷，现在刷让导入立刻可见。

    **没被覆盖的历史日子不刷**。它们现在走实时聚合，数字永远是新的；刷一次反而会给它们
    记上覆盖，从此读汇总表、随历史冻结 —— 导入一份三个月前的 Excel，结果把那三个月
    从「实时」变成了「以后不手动刷新就不会变」，这是倒退。

    未来的日期（导入了排期在后面的推广单）同理不刷：没人能把未来的日子刷「完整」。
    """
    covered_set = set(covered)
    runs: list[tuple[date, date]] = []
    run_lo: date | None = None
    prev: date | None = None
    day = date_from
    while day <= date_to:
        wanted = day in covered_set or window_lo <= day <= today
        if wanted:
            if run_lo is None:
                run_lo = day
            prev = day
        elif run_lo is not None and prev is not None:
            runs.append((run_lo, prev))
            run_lo = None
        day += timedelta(days=1)
    if run_lo is not None and prev is not None:
        runs.append((run_lo, prev))
    return runs


async def _refresh_dates(tenant_id: UUID, date_from: date, date_to: date) -> dict[str, Any]:
    today = get_today()
    window_lo = today - timedelta(days=REFRESH_WINDOW_DAYS - 1)
    totals: dict[str, int] = {}
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context(), AsyncSessionApp() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)}
            )
            covered = await SummaryReadRepository(s).covered_dates(tenant_id, date_from, date_to)
            runs = target_runs(date_from, date_to, covered, window_lo, today)
            svc = SummaryRefreshService(s)
            # 多段在同一个事务里刷：第一段拿到的租户锁对同一会话可重入，后面几段直接通过；
            # 一起提交，不会出现「刷了一半」的中间状态
            for lo, hi in runs:
                counts = await svc.refresh(tenant_id=tenant_id, date_from=lo, date_to=hi)
                for table, n in counts.items():
                    totals[table] = totals.get(table, 0) + n
            await s.commit()
    finally:
        tenant_id_ctx.reset(tok)
    return {
        "tenant_id": str(tenant_id),
        "requested": [date_from.isoformat(), date_to.isoformat()],
        "runs": [[lo.isoformat(), hi.isoformat()] for lo, hi in runs],
        **totals,
    }


__all__ = [
    "REFRESH_WINDOW_DAYS",
    "refresh_report_summaries",
    "refresh_report_summaries_for_dates",
    "target_runs",
]
