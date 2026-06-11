"""U13 采集调度 Celery 任务（schedule_daily_tasks）。

Beat 每天 02:00（crawler 队列）逐租户生成 pending crawler_task。
逐租户 system_context + set_config，单租户失败 catch+log+Sentry 不中止。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import sentry_sdk
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.collect.crawler_task_service import CrawlerTaskService

log = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.crawler_tasks.schedule_daily_tasks",
    queue="crawler",
    autoretry_for=(OperationalError,),
    max_retries=2,
    default_retry_delay=10,
)
def schedule_daily_tasks(self) -> dict[str, Any]:  # noqa: ANN001
    return asyncio.run(_schedule_impl())


async def _schedule_impl() -> dict[str, Any]:
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (await meta.execute(text("SELECT id FROM tenant"))).scalars().all()
        )

    total = 0
    for tid in tenant_ids:
        total += await _schedule_one_tenant(tid)
    return {"tenants": len(tenant_ids), "scheduled": total}


async def _schedule_one_tenant(tenant_id: UUID) -> int:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context():
            async with AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                count = await CrawlerTaskService(s).schedule_for_tenant(tenant_id)
                await s.commit()
                return count
    except Exception as exc:  # noqa: BLE001 单租户失败不中止
        log.exception("crawler_schedule_tenant_failed tenant=%s", str(tenant_id))
        sentry_sdk.capture_exception(exc)
        return 0
    finally:
        tenant_id_ctx.reset(tok)


__all__ = ["schedule_daily_tasks"]
