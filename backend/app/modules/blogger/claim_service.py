"""认领、批量分配、工作微信及准备期。事务由服务调用方提交。"""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import PermissionDeniedError, ResourceNotFoundError, ValidationError
from app.modules.auth.models import Tenant, User
from app.modules.auth.repository import RoleRepository
from app.modules.blogger.claim_access import BloggerAccessContext, load_blogger_access
from app.modules.blogger.claim_models import BloggerClaim
from app.modules.blogger.claim_schemas import ClaimResponse, ClaimSettings
from app.modules.blogger.models import Blogger
from app.modules.product.dict_models import DictItem

WORK_WECHAT_TYPE = "blogger_work_wechat"


class BloggerClaimService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.audit = AuditService(session)

    async def access(self, user: User) -> BloggerAccessContext:
        return await load_blogger_access(self.session, user, [])

    async def require_manager(self, user: User) -> None:
        if not (await self.access(user)).can_manage:
            raise PermissionDeniedError("仅有权限的主管或租户管理员可管理认领")

    async def blogger(self, blogger_id: UUID, user: User, *, lock: bool = False) -> Blogger:
        stmt = select(Blogger).where(
            Blogger.id == blogger_id,
            Blogger.tenant_id == user.tenant_id,
            Blogger.is_deleted.is_(False),
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise ResourceNotFoundError("博主不存在")
        return row

    async def validate_pr(self, pr_id: UUID, user: User) -> User:
        pr = await self.session.scalar(
            select(User).where(
                User.id == pr_id,
                User.tenant_id == user.tenant_id,
                User.deleted_at.is_(None),
                User.status == "active",
                User.locked_at.is_(None),
            )
        )
        if pr is None or "pr" not in await RoleRepository(self.session).list_codes_for_user(pr_id):
            raise ValidationError("请选择本租户有效 PR", code="BLOGGER_CLAIM_PR_INVALID")
        return pr

    async def validate_work_wechat(
        self, work_id: UUID | None, user: User, *, allow_missing: bool = False
    ) -> DictItem | None:
        if work_id is None and allow_missing:
            return None
        row = await self.session.scalar(
            select(DictItem).where(
                DictItem.id == work_id,
                DictItem.tenant_id == user.tenant_id,
                DictItem.dict_type == WORK_WECHAT_TYPE,
                DictItem.is_active.is_(True),
            )
        )
        if row is None:
            raise ValidationError("请选择本租户启用的工作微信", code="WORK_WECHAT_REQUIRED")
        return row

    async def add(
        self, blogger_id: UUID, pr_id: UUID, user: User, work_id: UUID | None, method: str
    ) -> bool:
        """调用方已验证对象和操作者；唯一键保证重复认领不重复审计。"""
        stmt = (
            pg_insert(BloggerClaim)
            .values(
                id=uuid4(),
                tenant_id=user.tenant_id,
                blogger_id=blogger_id,
                pr_id=pr_id,
                method=method,
                operated_by=user.id,
                work_wechat_id=work_id,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "blogger_id", "pr_id"])
            .returning(BloggerClaim.id)
        )
        created_id = await self.session.scalar(stmt)
        if created_id is not None:
            await self.audit.log(
                action="blogger.claim",
                resource="blogger",
                resource_id=blogger_id,
                after={
                    "pr_id": str(pr_id),
                    "method": method,
                    "work_wechat_id": str(work_id) if work_id else None,
                },
                user_id=user.id,
            )
        return created_id is not None

    async def self_claim(
        self, blogger_id: UUID, wechat: str, work_id: UUID, user: User
    ) -> Literal["claimed", "already_claimed", "not_verified"]:
        ctx = await self.access(user)
        if not ctx.can_self_claim:
            raise PermissionDeniedError("没有本人认领权限")
        blogger = await self.blogger(blogger_id, user, lock=True)
        existing = await self.session.scalar(
            select(BloggerClaim.id).where(
                BloggerClaim.tenant_id == user.tenant_id,
                BloggerClaim.blogger_id == blogger_id,
                BloggerClaim.pr_id == user.id,
            )
        )
        if existing:
            return "already_claimed"
        await self.validate_work_wechat(work_id, user)
        if not (blogger.wechat or "").strip() or (blogger.wechat or "").strip() != wechat.strip():
            return "not_verified"
        created = await self.add(blogger_id, user.id, user, work_id, "wechat_match")
        return "claimed" if created else "already_claimed"

    async def auto_claim_new(
        self, blogger: Blogger, user: User, work_id: UUID | None, *, old_batch: bool = False
    ) -> None:
        ctx = await self.access(user)
        if not ctx.can_self_claim:
            return
        await self.validate_work_wechat(work_id, user, allow_missing=not ctx.enabled or old_batch)
        await self.add(blogger.id, user.id, user, work_id, "created")

    async def assign(self, blogger_id: UUID, pr_id: UUID, work_id: UUID, user: User) -> bool:
        await self.require_manager(user)
        await self.blogger(blogger_id, user)
        await self.validate_pr(pr_id, user)
        await self.validate_work_wechat(work_id, user)
        return await self.add(blogger_id, pr_id, user, work_id, "assigned")

    async def revoke(self, blogger_id: UUID, pr_id: UUID, user: User) -> None:
        await self.require_manager(user)
        await self.blogger(blogger_id, user)
        removed = await self.session.scalar(
            delete(BloggerClaim)
            .where(
                BloggerClaim.tenant_id == user.tenant_id,
                BloggerClaim.blogger_id == blogger_id,
                BloggerClaim.pr_id == pr_id,
            )
            .returning(BloggerClaim.id)
        )
        if removed:
            await self.audit.log(
                action="blogger.claim_revoke",
                resource="blogger",
                resource_id=blogger_id,
                before={"pr_id": str(pr_id)},
                user_id=user.id,
            )

    async def switch_work_wechat(
        self, blogger_id: UUID, pr_id: UUID, work_id: UUID, user: User
    ) -> None:
        ctx = await self.access(user)
        if not (ctx.can_manage or (ctx.can_self_claim and pr_id == user.id)):
            raise PermissionDeniedError("不能修改该认领")
        await self.validate_work_wechat(work_id, user)
        row = await self.session.scalar(
            select(BloggerClaim)
            .where(
                BloggerClaim.tenant_id == user.tenant_id,
                BloggerClaim.blogger_id == blogger_id,
                BloggerClaim.pr_id == pr_id,
            )
            .with_for_update()
        )
        if row is None:
            raise ResourceNotFoundError("认领不存在")
        if row.work_wechat_id != work_id:
            old = row.work_wechat_id
            row.work_wechat_id = work_id
            await self.audit.log(
                action="blogger.claim_work_wechat",
                resource="blogger",
                resource_id=blogger_id,
                before={"work_wechat_id": str(old) if old else None},
                after={"work_wechat_id": str(work_id), "pr_id": str(pr_id)},
                user_id=user.id,
            )

    async def transfer(
        self, blogger_id: UUID, from_pr: UUID, to_pr: UUID, work_id: UUID, user: User
    ) -> None:
        if from_pr == to_pr:
            raise ValidationError("请选择另一位 PR")
        await self.require_manager(user)
        old = await self.session.scalar(
            select(BloggerClaim)
            .where(
                BloggerClaim.tenant_id == user.tenant_id,
                BloggerClaim.blogger_id == blogger_id,
                BloggerClaim.pr_id == from_pr,
            )
            .with_for_update()
        )
        if old is None:
            raise ResourceNotFoundError("原认领不存在")
        await self.assign(blogger_id, to_pr, work_id, user)
        await self.switch_work_wechat(blogger_id, to_pr, work_id, user)
        await self.revoke(blogger_id, from_pr, user)

    async def list_claims(self, blogger_id: UUID, user: User) -> list[ClaimResponse]:
        await self.blogger(blogger_id, user)
        rows = (
            await self.session.execute(
                select(BloggerClaim, User, DictItem)
                .join(User, User.id == BloggerClaim.pr_id)
                .outerjoin(DictItem, DictItem.id == BloggerClaim.work_wechat_id)
                .where(
                    BloggerClaim.tenant_id == user.tenant_id, BloggerClaim.blogger_id == blogger_id
                )
                .order_by(BloggerClaim.created_at, BloggerClaim.id)
            )
        ).all()
        return [
            ClaimResponse(
                id=c.id,
                blogger_id=c.blogger_id,
                pr_id=c.pr_id,
                pr_name=pr.display_name or pr.username,
                method=c.method,
                operated_by=c.operated_by,
                created_at=c.created_at,
                work_wechat_id=c.work_wechat_id,
                work_wechat_name=w.value if w else None,
                work_wechat_active=w.is_active if w else None,
            )
            for c, pr, w in rows
        ]

    async def settings(self, user: User) -> ClaimSettings:
        enabled = await self.session.scalar(
            select(Tenant.blogger_claims_enabled_at).where(Tenant.id == user.tenant_id)
        )
        claimed = (
            select(BloggerClaim.id)
            .where(BloggerClaim.tenant_id == user.tenant_id, BloggerClaim.blogger_id == Blogger.id)
            .exists()
        )
        count = await self.session.scalar(
            select(func.count())
            .select_from(Blogger)
            .where(
                Blogger.tenant_id == user.tenant_id,
                Blogger.is_deleted.is_(False),
                Blogger.is_active.is_(True),
                ~claimed,
            )
        )
        pending = await self.session.scalar(
            select(func.count())
            .select_from(BloggerClaim)
            .where(BloggerClaim.tenant_id == user.tenant_id, BloggerClaim.work_wechat_id.is_(None))
        )
        return ClaimSettings(
            enabled_at=enabled,
            unclaimed_count=count or 0,
            pending_work_wechat_count=pending or 0,
            can_enable=(await self.access(user)).can_manage,
        )

    async def enable(self, user: User) -> ClaimSettings:
        await self.require_manager(user)
        tenant = await self.session.scalar(
            select(Tenant).where(Tenant.id == user.tenant_id).with_for_update()
        )
        if tenant is None:
            raise ResourceNotFoundError("租户不存在")
        if tenant.blogger_claims_enabled_at is None:
            tenant.blogger_claims_enabled_at = datetime.now(UTC)
            await self.audit.log(
                action="blogger.claims_enable",
                resource="tenant",
                resource_id=tenant.id,
                after={"enabled": True},
                user_id=user.id,
            )
            await self.session.flush()
        return await self.settings(user)
