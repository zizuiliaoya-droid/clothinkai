"""U07 企微 Celery 任务（EP08-S05/S06/S07）。

- scan_and_dispatch_urge：Beat 09:00 逐租户扫描催发候选 → 建 pending message → delay 执行
- execute_wecom_message：每消息独立事务 + 频控降级

按 P-U07-03/04：bypass 读元数据 + AsyncSessionApp set_config（NF-1）+ system_context audit。
Celery 任务入口用 asyncio.run（同 U06a runner）。
"""

from __future__ import annotations

import asyncio
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
from app.modules.promotion.urge_calculator import get_today
from app.modules.wecom.anomaly_service import AnomalyAlertService
from app.modules.wecom.group_notify_service import GroupNotifyService
from app.modules.wecom.scan_service import WecomScanService
from app.modules.wecom.send_service import WecomSendService

log = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.wecom_tasks.scan_and_dispatch_urge", queue="default")
def scan_and_dispatch_urge() -> dict[str, Any]:
    return asyncio.run(_scan_and_dispatch())


async def _scan_and_dispatch() -> dict[str, Any]:
    today = get_today()
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (
                await meta.execute(
                    text("SELECT tenant_id FROM wecom_config WHERE is_active = true")
                )
            ).scalars().all()
        )

    total = 0
    for tid in tenant_ids:
        created: list[UUID] = []
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context():
                async with AsyncSessionApp() as s:
                    await s.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"),
                        {"t": str(tid)},
                    )
                    created = await WecomScanService(s).scan_tenant(today)
                    await s.commit()
        except Exception as exc:  # noqa: BLE001
            log.exception("wecom_scan_tenant_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)
        # commit 后再投递执行任务（worker 才能读到 message 行）
        for mid in created:
            execute_wecom_message.delay(str(mid), str(tid))
        total += len(created)

    return {"dispatched": total, "tenants": len(tenant_ids)}


@celery_app.task(
    bind=True,
    name="app.tasks.wecom_tasks.execute_wecom_message",
    queue="default",
    autoretry_for=(OperationalError,),
    max_retries=1,
    default_retry_delay=5,
)
def execute_wecom_message(
    self: Task, message_id: str, tenant_id: str
) -> dict[str, Any]:
    return asyncio.run(_execute_one(UUID(message_id), UUID(tenant_id)))


async def _execute_one(message_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context():
            async with AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                result = await WecomSendService(s).send(message_id, tenant_id)
                await s.commit()
        return result
    finally:
        tenant_id_ctx.reset(tok)


# --------------------------------------------------------------------------- #
# U15 企微进阶：S09 发文通知控评 + S10 异常预警
# --------------------------------------------------------------------------- #


@celery_app.task(
    bind=True,
    name="app.tasks.wecom_tasks.notify_control_group",
    queue="default",
    autoretry_for=(OperationalError,),
    max_retries=1,
    default_retry_delay=5,
)
def notify_control_group(
    self: Task, promotion_id: str, tenant_id: str
) -> dict[str, Any]:
    return asyncio.run(_notify_control_group(UUID(promotion_id), UUID(tenant_id)))


async def _notify_control_group(promotion_id: UUID, tenant_id: UUID) -> dict[str, Any]:
    tok = tenant_id_ctx.set(tenant_id)
    try:
        async with system_context():
            async with AsyncSessionApp() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :t, true)"),
                    {"t": str(tenant_id)},
                )
                result = await GroupNotifyService(s).notify_publish(
                    promotion_id, tenant_id
                )
                await s.commit()
        return result
    finally:
        tenant_id_ctx.reset(tok)


@celery_app.task(
    name="app.tasks.wecom_tasks.check_anomaly_and_alert", queue="default"
)
def check_anomaly_and_alert() -> dict[str, Any]:
    return asyncio.run(_check_anomaly_all())


async def _check_anomaly_all() -> dict[str, Any]:
    async with AsyncSessionBypass() as meta:
        tenant_ids = list(
            (
                await meta.execute(
                    text(
                        "SELECT tenant_id FROM wecom_alert_config "
                        "WHERE is_enabled = true"
                    )
                )
            ).scalars().all()
        )

    total = 0
    for tid in tenant_ids:
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context():
                async with AsyncSessionApp() as s:
                    await s.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"),
                        {"t": str(tid)},
                    )
                    total += await AnomalyAlertService(s).check_and_alert(tid)
                    await s.commit()
        except Exception as exc:  # noqa: BLE001 — 单租户失败不中止其余
            log.exception("anomaly_check_failed", extra={"tenant_id": str(tid)})
            sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)

    return {"tenants": len(tenant_ids), "alerts": total}


__all__ = [
    "check_anomaly_and_alert",
    "execute_wecom_message",
    "notify_control_group",
    "scan_and_dispatch_urge",
]
