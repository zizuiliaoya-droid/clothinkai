"""U11 博主智能标签 Celery 任务（EP04-S05）。

``recompute_all_blogger_tags``：逐租户批量重算 blogger_type / 假号嫌疑 / 质量标签。
``recompute_blogger_quality_tags``：只重算一个租户的质量标签（7e R7，平台指标保存后投递）。

按 P-U11-01：
- 逐 tenant（system_context + set_config）独立事务。
- 单 tenant 失败 catch+log+继续；单 blogger 失败在 BloggerTagService 内部已 catch。
- autoretry_for=(OperationalError,) max_retries=2（DB 抖动重试）。

Celery 任务入口统一用 ``app.tasks.runner.run_async_task``（新事件循环 + 引擎回收）。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import sentry_sdk
from celery import Task
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.blogger.tag_service import BloggerTagService
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="app.tasks.blogger_tasks.recompute_all_blogger_tags",
    queue="default",
    autoretry_for=(OperationalError,),
    max_retries=2,
    default_retry_delay=10,
)
def recompute_all_blogger_tags(self: Task) -> dict[str, Any]:
    return run_async_task(_recompute_impl())


async def _recompute_impl() -> dict[str, Any]:
    async with AsyncSessionBypass() as meta:
        tenant_ids = list((await meta.execute(text("SELECT id FROM tenant"))).scalars().all())

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
    except Exception as exc:
        log.exception("recompute_tenant_failed tenant=%s", str(tenant_id))
        sentry_sdk.capture_exception(exc)
        return {"updated": 0, "failed": 0}
    finally:
        tenant_id_ctx.reset(tok)


# --------------------------------------------------------------------------- #
# 只重算质量标签（7e R7：保存平台指标配置后投递）
# --------------------------------------------------------------------------- #
@celery_app.task(
    bind=True,
    name="app.tasks.blogger_tasks.recompute_blogger_quality_tags",
    queue="default",
    autoretry_for=(OperationalError,),
    max_retries=3,
    default_retry_delay=10,
)
def recompute_blogger_quality_tags(self: Task, tenant_id: str) -> dict[str, int]:
    """只重算一个租户的质量标签（「高性价比」「带货型」）。

    由 ``PUT /api/settings/promotion-platforms`` 提交后投递，参数只有保存配置的那个租户
    （JSON 序列化，传字符串）。不复用 ``recompute_all_blogger_tags``：那条会重写博主类型与
    ``is_suspected_fake``。DB 抖动（``OperationalError``）重试，最后一次仍失败记 error +
    Sentry；其他异常不重试，记 error + Sentry。已保存的配置不受影响。
    """
    return run_async_task(_recompute_quality_impl(self, UUID(tenant_id)))


async def _recompute_quality_impl(task: Task, tenant_id: UUID) -> dict[str, int]:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context():
            async with AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', CAST(:t AS text), true)"),
                    {"t": str(tenant_id)},
                )
                result = await BloggerTagService(s).recompute_quality_for_tenant(tenant_id)
                await s.commit()
                log.info(
                    "recompute_quality_done tenant=%s updated=%s unchanged=%s failed=%s",
                    str(tenant_id),
                    result["updated"],
                    result["unchanged"],
                    result["failed"],
                )
                return result
    except OperationalError as exc:
        # 交给 autoretry_for 重试；重试用完的那一次先记下来
        if task.request.retries >= task.max_retries:
            log.exception("recompute_quality_failed_terminal tenant=%s", str(tenant_id))
            sentry_sdk.capture_exception(exc)
        raise
    except Exception as exc:
        log.exception("recompute_quality_failed tenant=%s", str(tenant_id))
        sentry_sdk.capture_exception(exc)
        return {"updated": 0, "unchanged": 0, "failed": 0}
    finally:
        tenant_id_ctx.reset(tok)


__all__ = ["recompute_all_blogger_tags", "recompute_blogger_quality_tags"]
