"""报表中间汇总表的定时刷新（PRD V1.4 模块三「每小时增量刷新」）。

## 为什么是独立的 task module

``report_tasks.precompute_report_cache`` 是 U14 留的占位，至今是 noop。那个名字描述
的是「报表缓存」，而这里写的是 5 张有 schema 的汇总表 —— 不是缓存，是可查询的派生
数据。混在一起会让「noop 占位」和「真的在跑的任务」看起来是同一件事。

## 刷新窗口为什么是 31 天

汇总表的意义是服务常用区间（本月 / 上月 / 最近 30 天），这些都落在最近 31 天内。
全量回算要扫 ``promotion`` 的 5156 行 × 每日一次聚合，没有收益。

历史区间通过手动刷新端点按需补 —— 比如导入了三个月前的 Excel。

## 单租户失败不中断其他租户

与 ``urge_tasks`` 同样的写法（P-U07-03/04）：bypass 读租户元数据，每个租户单独
``AsyncSessionApp`` + ``set_config`` 走 RLS。一个租户的数据有问题不该让其他租户
的报表也停在旧数字上。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import sentry_sdk
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)

# 滚动刷新窗口。覆盖「本月 + 上月」的常用区间：月初那天往前 31 天刚好能把
# 上个月整月带上，月汇总那一行才不会只含本月已过的几天。
REFRESH_WINDOW_DAYS = 31


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
                # 5 张表一起 commit：中间状态（商品 ROI 刷了、店铺日报没刷）
                # 会让两张报表对不上账
                await s.commit()
            for table, n in counts.items():
                totals[table] = totals.get(table, 0) + n
        except Exception as exc:
            failed += 1
            log.exception("summary_refresh_tenant_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)

    return {
        "tenants": len(tenant_ids),
        "failed": failed,
        "date_from": date_from.isoformat(),
        "date_to": today.isoformat(),
        **totals,
    }


__all__ = ["REFRESH_WINDOW_DAYS", "refresh_report_summaries"]
