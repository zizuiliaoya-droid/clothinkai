"""联系方式行权限集中计算；不改变共用字段权限的 deny 优先规则。"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import FieldPermissionDenied, PermissionDeniedError, ResourceNotFoundError
from app.core.security.field_permissions import (
    FieldPermissionContext,
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import support_readonly_ctx
from app.modules.auth.models import Tenant, User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.claim_models import BloggerClaim
from app.modules.blogger.models import Blogger

CONTACT_FIELDS = frozenset({"wechat", "phone"})


@dataclass(frozen=True)
class BloggerAccessContext:
    user_id: UUID
    tenant_id: UUID
    field_ctx: FieldPermissionContext
    permissions: EffectivePermissions
    enabled: bool
    manager: bool
    readonly: bool
    loaded_ids: frozenset[UUID]
    claimed_ids: frozenset[UUID]
    claim_counts: dict[UUID, int]

    @property
    def can_manage(self) -> bool:
        return self.manager and self.permissions.has("blogger", "write") and not self.readonly

    @property
    def can_self_claim(self) -> bool:
        return (
            "pr" in self.field_ctx.role_codes
            and self.permissions.has("blogger", "write")
            and not self.readonly
        )


async def load_blogger_access(
    session: AsyncSession, user: User, blogger_ids: Iterable[UUID]
) -> BloggerAccessContext:
    ids = frozenset(blogger_ids)
    roles = RoleRepository(session)
    perms = PermissionRepository(session)
    field_ctx = await build_field_perm_context(user.id, roles, perms)
    role_scopes, grants, revokes = await perms.list_scopes_for_user(user.id)
    permissions = EffectivePermissions(
        str(user.id), frozenset(role_scopes | grants), frozenset(revokes)
    )
    enabled_at = await session.scalar(
        select(Tenant.blogger_claims_enabled_at).where(Tenant.id == user.tenant_id)
    )
    rows = (
        (
            await session.execute(
                select(BloggerClaim.blogger_id, BloggerClaim.pr_id).where(
                    BloggerClaim.tenant_id == user.tenant_id, BloggerClaim.blogger_id.in_(ids)
                )
            )
        ).all()
        if ids
        else []
    )
    counts: dict[UUID, int] = {}
    for blogger_id, _ in rows:
        counts[blogger_id] = counts.get(blogger_id, 0) + 1
    return BloggerAccessContext(
        user.id,
        user.tenant_id,
        field_ctx,
        permissions,
        enabled_at is not None,
        bool(field_ctx.role_codes & {"admin", "pr_manager"}),
        support_readonly_ctx.get(),
        ids,
        frozenset(bid for bid, pid in rows if pid == user.id),
        counts,
    )


def row_contact_allowed(ctx: BloggerAccessContext, blogger_id: UUID) -> bool:
    return blogger_id in ctx.loaded_ids and (
        not ctx.enabled or ctx.manager or blogger_id in ctx.claimed_ids
    )


def can_read_contact(ctx: BloggerAccessContext, blogger_id: UUID, field: str) -> bool:
    return row_contact_allowed(ctx, blogger_id) and can_read_field("blogger", field, ctx.field_ctx)


def can_write_contact(ctx: BloggerAccessContext, blogger_id: UUID, field: str) -> bool:
    return (
        not ctx.readonly
        and row_contact_allowed(ctx, blogger_id)
        and can_write_field("blogger", field, ctx.field_ctx)
    )


def ensure_contact_write(
    ctx: BloggerAccessContext, blogger_id: UUID, submitted_fields: Iterable[str]
) -> None:
    denied = sorted(
        f
        for f in set(submitted_fields) & CONTACT_FIELDS
        if not can_write_contact(ctx, blogger_id, f)
    )
    if denied:
        raise FieldPermissionDenied(fields=denied, entity="blogger")


def contact_field_context(
    ctx: BloggerAccessContext, blogger_id: UUID | None
) -> FieldPermissionContext:
    """供既有导入投影使用；没有已解析对象时不能按缺省行上下文放行。"""
    if blogger_id is not None and row_contact_allowed(ctx, blogger_id):
        return ctx.field_ctx
    if blogger_id is None and (not ctx.enabled or ctx.manager):
        return ctx.field_ctx
    denied = frozenset(
        f"field.blogger.{field}:{action}"
        for field in CONTACT_FIELDS
        for action in ("read", "write")
    )
    return replace(ctx.field_ctx, revokes=ctx.field_ctx.revokes | denied)


async def load_claim_actor(session: AsyncSession, tenant_id: UUID, actor_id: UUID | None) -> User:
    actor = await session.scalar(
        select(User).where(
            User.id == actor_id,
            User.tenant_id == tenant_id,
            User.status == "active",
            User.deleted_at.is_(None),
            User.locked_at.is_(None),
        )
    )
    if actor is None:
        raise PermissionDeniedError("导入操作人不可用，请由有效账号重新操作")
    return actor


async def ensure_new_cooperation_allowed(
    session: AsyncSession, user: User, blogger_id: UUID
) -> None:
    blogger = await session.scalar(
        select(Blogger).where(
            Blogger.id == blogger_id,
            Blogger.tenant_id == user.tenant_id,
            Blogger.is_deleted.is_(False),
        )
    )
    if blogger is None:
        raise ResourceNotFoundError("博主不存在")
    ctx = await load_blogger_access(session, user, [blogger_id])
    if ctx.readonly or not row_contact_allowed(ctx, blogger_id):
        raise PermissionDeniedError("请先认领该博主，再发起新合作", code="BLOGGER_CLAIM_REQUIRED")
