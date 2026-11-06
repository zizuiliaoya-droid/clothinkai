# ruff: noqa: S608 -- SQL片段仅来自代码常量，所有用户值均使用绑定参数。
"""每日一次待跟进站内汇总，复用既有Beat与Notification，不调用外部发送。"""

import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.celery_app import celery_app
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.tenancy import system_context, tenant_id_ctx
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.engagement import engagement_joins, engagement_params, needs_follow_up_sql
from app.modules.wecom.models import Notification
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)


async def summarize_tenant(session: Any, tenant_id: Any, now: datetime) -> int:
    today = now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    users = (
        (
            await session.execute(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.status == "active",
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for user in users:
        actor = await load_flow_actor(
            user.id, RoleRepository(session), PermissionRepository(session)
        )
        if "platform_admin" in actor.field_ctx.role_codes:
            continue
        if not actor.perms.has("notification", "read"):
            continue
        params = {**engagement_params(actor, now=now), "tenant_id": tenant_id}
        found = False
        for kind, alias in (("negotiation", "n"), ("promotion", "p")):
            if not actor.perms.has(kind, "read"):
                continue
            owner = f"({alias}.pr_id=CAST(:engagement_viewer AS uuid) OR :engagement_see_all)"
            if kind == "negotiation":
                owner = f"({alias}.pr_id=CAST(:engagement_viewer AS uuid) OR (:engagement_see_all AND {alias}.status<>'草稿'))"
            found = bool(
                (
                    await session.execute(
                        text(f"""SELECT EXISTS (
              SELECT 1 FROM {kind} {alias} {engagement_joins(alias)}
              WHERE {alias}.tenant_id=CAST(:tenant_id AS uuid) AND {owner}
                AND {needs_follow_up_sql(alias, kind)}
            )"""),
                        params,
                    )
                ).scalar_one()
            )
            if found:
                break
        if not found:
            continue
        stmt = (
            insert(Notification)
            .values(
                tenant_id=tenant_id,
                user_id=user.id,
                type="follow_up_summary",
                summary_date=today,
                content="有合作事项超过120小时未跟进，请查看待跟进列表。",
                link="/promotions?needs_follow_up=true",
            )
            .on_conflict_do_nothing(
                index_elements=["tenant_id", "user_id", "type", "summary_date"],
                index_where=text("summary_date IS NOT NULL"),
            )
            .returning(Notification.id)
        )
        if (await session.execute(stmt)).scalar_one_or_none() is not None:
            created += 1
    return created


@celery_app.task(name="app.tasks.follow_up_tasks.summarize_follow_up", queue="default")
def summarize_follow_up() -> dict[str, int]:
    return run_async_task(_summarize_all())


async def _summarize_all() -> dict[str, int]:
    now = datetime.now(UTC)
    async with AsyncSessionBypass() as metadata:
        tenant_ids = list(
            (
                await metadata.execute(
                    text("SELECT id FROM tenant WHERE status='active' AND deleted_at IS NULL")
                )
            ).scalars()
        )
    created = 0
    for tenant_id in tenant_ids:
        token = tenant_id_ctx.set(tenant_id)
        try:
            async with system_context():
                async with AsyncSessionApp() as session:
                    await session.execute(
                        text("SELECT set_config('app.tenant_id',:t,true)"), {"t": str(tenant_id)}
                    )
                    created += await summarize_tenant(session, tenant_id, now)
                    await session.commit()
        except Exception:
            log.exception("follow_up_summary_failed", extra={"tenant_id": str(tenant_id)})
        finally:
            tenant_id_ctx.reset(token)
    return {"tenants": len(tenant_ids), "notifications": created}
