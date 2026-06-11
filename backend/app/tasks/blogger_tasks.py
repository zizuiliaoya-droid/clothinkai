"""U11 博主智能标签 Celery 任务（EP04-S05）。

``recompute_all_blogger_tags``：逐租户批量重算 blogger_type / 假号嫌疑 / 质量标签。

按 P-U11-01：
- 逐 tenant（system_context + set_config）独立事务。
- 单 tenant 失败 catch+log+继续；单 blogger 失败在 BloggerTagService 内部已 catch。
- autoretry_for=(OperationalError,) max_retries=2（DB 抖动重试）。

Celery 任务入口用 asyncio.run（同 U06a/U07 runner）。
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
from app.modules.blogger.tag_service import BloggerTagService

log = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.blogger_tasks.recompute_all_blogger_tags",
    queue="default",
    autoretry_for=(OperationalError,),
    max_retries=2,
    default_retry_delay=10,
)
def recompute_all_blogger_tags(self) -> dict[str, Any]:  # noqa: ANN001
    return asyncio.run(_recompute_impl())


async def _recompute_impl() -> dict[str, Any]:
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (await meta.execute(text("SELECT id FROM tenant"))).scalars().all()
        )

    total_updated = 0
    total_failed = 0
    for tid in tenant_ids:
        result = await _recompute_one_tenant(tid)
        total_updated += result.get("updated", 0)
        total_failed += result.get("failed", 0)

    return {
        "tenants": len(tenant_ids),
        "updated": total_updated,
        "failed": total_failed,
    }


async def _recompute_one_tenant(tenant_id: UUID) -> dict[str, int]:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context():
            async with AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                result = await BloggerTagService(s).recompute_for_tenant(tenant_id)
                await s.commit()
                log.info(
                    "recompute_done tenant=%s updated=%s failed=%s",
                    str(tenant_id),
                    result["updated"],
                    result["failed"],
                )
                return result
    except Exception as exc:  # noqa: BLE001 单 tenant 失败不影响其余
        log.exception("recompute_tenant_failed tenant=%s", str(tenant_id))
        sentry_sdk.capture_exception(exc)
        return {"updated": 0, "failed": 0}
    finally:
        tenant_id_ctx.reset(tok)


__all__ = ["recompute_all_blogger_tags"]
