"""催发任务扫描（PRD V1.4 改动 2「自动按临期触发」）。

和既有的 ``wecom_tasks.scan_and_dispatch_urge`` 是两件事，刻意分开：

- 本任务建**催发任务与留痕**（urge_task / urge_record），不依赖企微。
  生产上 ``wecom_config`` 一直是 0 行，企微那条链路从未发出过消息；催发任务
  不能跟着一起哑掉。
- ``wecom_tasks`` 那个负责**企微投递**，租户列表从 ``wecom_config`` 来，
  没配企微就跳过。

所以本任务的租户列表取自 ``tenant`` 而不是 ``wecom_config``。

按 P-U07-03/04 的多租户写法：bypass 读元数据 + AsyncSessionApp set_config（NF-1）
+ system_context 让审计拿到 system actor。单租户失败只记 log 不中断其他租户。
"""

from __future__ import annotations

import logging
from typing import Any

import sentry_sdk
from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.service import UrgeService
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.urge_tasks.scan_urge_tasks", queue="default")
def scan_urge_tasks() -> dict[str, Any]:
    return run_async_task(_scan_all())


async def _scan_all() -> dict[str, Any]:
    today = get_today()
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (
                await meta.execute(
                    text("SELECT id FROM tenant " "WHERE status = 'active' AND deleted_at IS NULL")
                )
            )
            .scalars()
            .all()
        )

    totals = {
        "tasks_created": 0,
        "records_created": 0,
        "skipped_same_day": 0,
        "tasks_closed": 0,
    }
    for tid in tenant_ids:
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context():
                async with AsyncSessionApp() as s:
                    await s.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"),
                        {"t": str(tid)},
                    )
                    result = await UrgeService(s).scan_tenant(tenant_id=tid, today=today)
                    await s.commit()
            totals["tasks_created"] += result.tasks_created
            totals["records_created"] += result.records_created
            totals["skipped_same_day"] += result.skipped_same_day
            totals["tasks_closed"] += result.tasks_closed
        except Exception as exc:
            log.exception("urge_scan_tenant_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)

    return {**totals, "tenants": len(tenant_ids)}


__all__ = ["scan_urge_tasks"]
