"""导入沿用实际操作人的认领和字段权限；资料结果与认领提示分开。"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import can_write_field
from app.modules.auth.models import Tenant
from app.modules.blogger.claim_access import (
    can_write_contact,
    load_blogger_access,
    load_claim_actor,
)
from app.modules.blogger.claim_service import BloggerClaimService
from app.modules.blogger.models import Blogger
from app.modules.importer.models import ImportBatch
from app.modules.importer.outcome import ImportRowContext


async def prepare_claim_import(
    session: AsyncSession,
    ctx: ImportRowContext,
    parsed: dict[str, Any],
    blogger: Blogger | None,
    warnings: list[str],
) -> dict[str, Any]:
    enabled = await session.scalar(
        select(Tenant.blogger_claims_enabled_at).where(Tenant.id == ctx.tenant_id)
    )
    if ctx.actor_id is None and not enabled:
        # 仅兼容没有操作人的旧批次；当前上传始终记录实际用户。
        # 已知操作人即使在准备期，也必须经过原有来源权限和个人字段 deny。
        return parsed
    user = await load_claim_actor(session, ctx.tenant_id, ctx.actor_id)
    access = await load_blogger_access(session, user, [blogger.id] if blogger else [])
    from app.modules.importer import access as import_access

    import_access.require_write(access.permissions, ctx.source)
    if enabled and blogger and access.can_self_claim and not access.manager:
        batch = await session.get(ImportBatch, ctx.batch_id) if ctx.batch_id else None
        if blogger.id in access.claimed_ids:
            warnings.append("认领：已认领")
        elif parsed.get("wechat") and batch and batch.claim_work_wechat_id:
            result = await BloggerClaimService(session).self_claim(
                blogger.id, str(parsed["wechat"]), batch.claim_work_wechat_id, user
            )
            warnings.append("认领：成功" if result == "claimed" else "认领：未通过，请联系主管分配")
            access = await load_blogger_access(session, user, [blogger.id])
        else:
            warnings.append("认领：待单独认领或主管分配")
    prepared = dict(parsed)
    for field in ("wechat", "phone"):
        allowed = (
            can_write_contact(access, blogger.id, field)
            if blogger
            else can_write_field("blogger", field, access.field_ctx)
        )
        if not allowed and field in prepared:
            if prepared[field] not in (None, ""):
                warnings.append(f"{field} 无写入权限，未修改")
            prepared.pop(field)
    return prepared


async def claim_import_created(
    session: AsyncSession, ctx: ImportRowContext, blogger: Blogger, warnings: list[str]
) -> None:
    if ctx.actor_id is None:
        return  # 旧批次没有可归属的操作人，不猜认领
    user = await load_claim_actor(session, ctx.tenant_id, ctx.actor_id)
    service = BloggerClaimService(session)
    access = await service.access(user)
    if not access.can_self_claim or access.manager:
        return
    batch = await session.get(ImportBatch, ctx.batch_id) if ctx.batch_id else None
    work_id = batch.claim_work_wechat_id if batch else None
    await service.auto_claim_new(blogger, user, work_id, old_batch=work_id is None)
    warnings.append("认领：成功" + ("，工作微信待补" if work_id is None else ""))
