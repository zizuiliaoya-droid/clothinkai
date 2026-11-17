"""复用 Celery/租户上下文，每 5 分钟取消驳回满 7 天未重提的谈款。"""

import logging
from datetime import UTC, datetime

from sqlalchemy import text

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import actor_type_ctx, tenant_id_ctx
from app.modules.negotiation.rejection_cancel import cancel_expired_rejections
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.negotiation_tasks.cancel_rejected_negotiations", queue="default")
def cancel_rejected_negotiations() -> dict[str, int]:
    return run_async_task(_cancel_all())


async def _cancel_all() -> dict[str, int]:
    now = datetime.now(UTC)
    async with AsyncSessionBypass() as metadata:
        tenant_ids = list(
            (
                await metadata.execute(
                    text("SELECT id FROM tenant WHERE status='active' AND deleted_at IS NULL")
                )
            ).scalars()
        )
    cancelled, failed = 0, 0
    for tenant_id in tenant_ids:
        token = tenant_id_ctx.set(tenant_id)
        actor_token = actor_type_ctx.set("system")
        try:
            async with AsyncSessionApp() as session:
                await session.execute(
                    text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(tenant_id)}
                )
                count = await cancel_expired_rejections(session, tenant_id, now)
                await session.commit()
                cancelled += count
        except Exception:
            failed += 1
            log.exception("negotiation_expiry_failed", extra={"tenant_id": str(tenant_id)})
        finally:
            actor_type_ctx.reset(actor_token)
            tenant_id_ctx.reset(token)
    return {"tenants": len(tenant_ids), "cancelled": cancelled, "failed": failed}
