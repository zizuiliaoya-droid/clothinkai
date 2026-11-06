"""同人关系与具体账号标价；既有认领及字段权限仍逐项生效。"""

from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import FieldPermissionDenied, ResourceNotFoundError, ValidationError
from app.core.security.field_permissions import (
    FieldPermissionContext,
    can_read_field,
    can_write_field,
)
from app.modules.auth.models import User
from app.modules.blogger.account_models import (
    BloggerAccountGroup,
    BloggerAccountMember,
    BloggerAccountQuote,
)
from app.modules.blogger.account_schemas import (
    AccountGroupInput,
    AccountGroupResponse,
    AccountGroupView,
    AccountIdentity,
    AccountOption,
    AccountQuoteInput,
    AccountQuoteResponse,
    AccountSnapshot,
)
from app.modules.blogger.claim_access import (
    ensure_new_cooperation_allowed,
    load_blogger_access,
    row_contact_allowed,
)
from app.modules.blogger.claim_service import BloggerClaimService
from app.modules.blogger.models import Blogger


def account_key(ids: list[UUID]) -> str:
    return ",".join(sorted({str(value) for value in ids}))


def visible_snapshot(snapshot: dict | None, ctx: FieldPermissionContext) -> dict | None:
    if snapshot is None:
        return None
    result = dict(snapshot)
    if not (
        can_read_field("blogger", "quote", ctx) and can_read_field("promotion", "quote_amount", ctx)
    ):
        result["reference_amount"] = None
    return result


def apply_deal_amount(
    snapshot: dict, amount: Decimal | None, *, barter: bool
) -> tuple[dict, Decimal | None]:
    result = dict(snapshot)
    if barter:
        result["price_source"] = "exchange"
        return result, Decimal("0")
    if amount is not None:
        result["price_source"] = "manual"
        return result, amount
    reference = result["reference_amount"]
    result["price_source"] = (
        "group_quote"
        if result.get("quote_id")
        else (
            "blogger_quote"
            if reference is not None and result.get("group_id") is None
            else "unknown"
        )
    )
    return result, Decimal(reference) if reference is not None else None


class AccountGroupService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _group(
        self, group_id: UUID, tenant_id: UUID, *, lock: bool = False
    ) -> BloggerAccountGroup:
        stmt = select(BloggerAccountGroup).where(
            BloggerAccountGroup.id == group_id, BloggerAccountGroup.tenant_id == tenant_id
        )
        if lock:
            stmt = stmt.with_for_update()
        group = await self.session.scalar(stmt)
        if group is None:
            raise ResourceNotFoundError("账号关联组不存在")
        return group

    async def _member_group(self, blogger_id: UUID, tenant_id: UUID) -> UUID | None:
        group_id: UUID | None = await self.session.scalar(
            select(BloggerAccountMember.group_id).where(
                BloggerAccountMember.tenant_id == tenant_id,
                BloggerAccountMember.blogger_id == blogger_id,
                BloggerAccountMember.is_active.is_(True),
            )
        )
        return group_id

    async def _accounts(
        self, tenant_id: UUID, ids: list[UUID], *, lock: bool = False
    ) -> list[Blogger]:
        stmt = (
            select(Blogger)
            .where(
                Blogger.tenant_id == tenant_id, Blogger.id.in_(ids), Blogger.is_deleted.is_(False)
            )
            .order_by(Blogger.id)
        )
        if lock:
            stmt = stmt.with_for_update()
        rows = list((await self.session.scalars(stmt)).all())
        if len(rows) != len(set(ids)):
            raise ResourceNotFoundError("部分账号不存在或已删除")
        return rows

    async def members(self, group_id: UUID, tenant_id: UUID) -> list[UUID]:
        return list(
            (
                await self.session.scalars(
                    select(BloggerAccountMember.blogger_id)
                    .where(
                        BloggerAccountMember.tenant_id == tenant_id,
                        BloggerAccountMember.group_id == group_id,
                        BloggerAccountMember.is_active.is_(True),
                    )
                    .order_by(BloggerAccountMember.blogger_id)
                )
            ).all()
        )

    async def view(self, blogger_id: UUID, user: User) -> AccountGroupView:
        await self._accounts(user.tenant_id, [blogger_id])
        group_id = await self._member_group(blogger_id, user.tenant_id)
        ids = await self.members(group_id, user.tenant_id) if group_id else [blogger_id]
        rows = await self._accounts(user.tenant_id, ids)
        access = await load_blogger_access(self.session, user, ids)
        read_quote = can_read_field("blogger", "quote", access.field_ctx)
        group_response = None
        if group_id:
            group = await self._group(group_id, user.tenant_id)
            quotes = (
                await self.session.scalars(
                    select(BloggerAccountQuote)
                    .where(
                        BloggerAccountQuote.tenant_id == user.tenant_id,
                        BloggerAccountQuote.group_id == group_id,
                    )
                    .order_by(BloggerAccountQuote.account_key)
                )
            ).all()
            valid = {str(value) for value in ids}
            group_response = AccountGroupResponse(
                id=group.id,
                name=group.name,
                account_ids=ids,
                quotes=[
                    AccountQuoteResponse(
                        id=q.id,
                        account_ids=[UUID(value) for value in q.account_ids],
                        amount=q.amount if read_quote else None,
                    )
                    for q in quotes
                    if set(q.account_ids) <= valid
                ],
            )
        return AccountGroupView(
            group=group_response,
            accounts=[
                AccountOption(
                    id=b.id,
                    platform=b.platform,
                    account=b.xiaohongshu_id,
                    nickname=b.nickname,
                    available_for_new_cooperation=b.is_active
                    and row_contact_allowed(access, b.id)
                    and not access.readonly,
                )
                for b in rows
            ],
            can_manage=access.can_manage,
            can_read_quote=read_quote,
            can_write_quote=access.can_manage
            and can_write_field("blogger", "quote", access.field_ctx),
            can_write_amount=not access.readonly
            and can_write_field("promotion", "quote_amount", access.field_ctx),
        )

    async def save_group(
        self, payload: AccountGroupInput, user: User, group_id: UUID | None = None
    ) -> AccountGroupView:
        await BloggerClaimService(self.session).require_manager(user)
        group = (
            await self._group(group_id, user.tenant_id, lock=True)
            if group_id
            else BloggerAccountGroup(tenant_id=user.tenant_id, name=payload.name)
        )
        rows = await self._accounts(user.tenant_id, payload.account_ids, lock=True)
        if any(not row.is_active for row in rows):
            raise ValidationError("停用账号不能加入新的关联组")
        for row in rows:
            assigned = await self._member_group(row.id, user.tenant_id)
            if assigned is not None and assigned != group_id:
                raise ValidationError("账号已属于其他关联组，请先从原组移除")
        if group_id is None:
            self.session.add(group)
            await self.session.flush()
        group.name = payload.name
        members = (
            await self.session.scalars(
                select(BloggerAccountMember).where(
                    BloggerAccountMember.tenant_id == user.tenant_id,
                    BloggerAccountMember.group_id == group.id,
                )
            )
        ).all()
        ids = set(payload.account_ids)
        existing = {member.blogger_id for member in members}
        for member in members:
            member.is_active = member.blogger_id in ids
        for member_id in ids - existing:
            self.session.add(
                BloggerAccountMember(
                    tenant_id=user.tenant_id,
                    group_id=group.id,
                    blogger_id=member_id,
                    is_active=True,
                )
            )
        await self.session.flush()
        await AuditService(self.session).log(
            action="blogger.account_group.save",
            resource="blogger_account_group",
            resource_id=group.id,
            after={"name": group.name, "account_ids": sorted(str(v) for v in ids)},
            user_id=user.id,
        )
        return await self.view(payload.account_ids[0], user)

    async def save_quote(
        self, group_id: UUID, payload: AccountQuoteInput, user: User
    ) -> AccountGroupView:
        await BloggerClaimService(self.session).require_manager(user)
        access = await load_blogger_access(self.session, user, [])
        if not can_write_field("blogger", "quote", access.field_ctx):
            raise FieldPermissionDenied(field="quote", entity="blogger")
        await self._group(group_id, user.tenant_id, lock=True)
        members = await self.members(group_id, user.tenant_id)
        if not set(payload.account_ids) <= set(members):
            raise ValidationError("报价账号必须是该组的有效成员")
        rows = await self._accounts(user.tenant_id, payload.account_ids)
        if any(not row.is_active for row in rows):
            raise ValidationError("停用账号不能设置新的组合报价")
        key = account_key(payload.account_ids)
        quote = await self.session.scalar(
            select(BloggerAccountQuote).where(
                BloggerAccountQuote.tenant_id == user.tenant_id,
                BloggerAccountQuote.group_id == group_id,
                BloggerAccountQuote.account_key == key,
            )
        )
        if quote is None:
            quote = BloggerAccountQuote(
                tenant_id=user.tenant_id,
                group_id=group_id,
                account_key=key,
                account_ids=key.split(","),
                amount=payload.amount,
            )
            self.session.add(quote)
        else:
            quote.amount = payload.amount
        await self.session.flush()
        await AuditService(self.session).log(
            action="blogger.account_quote.save",
            resource="blogger_account_group",
            resource_id=group_id,
            after={"account_ids": key.split(","), "quote_changed": True},
            user_id=user.id,
        )
        return await self.view(payload.account_ids[0], user)

    async def resolve_snapshot(
        self, blogger_id: UUID, account_ids: list[UUID] | None, user: User
    ) -> dict:
        ids = sorted(set(account_ids if account_ids is not None else [blogger_id]), key=str)
        if not ids or blogger_id not in ids or len(ids) > 10:
            raise ValidationError("合作账号必须包含主账号，最多10个")
        group_id = await self._member_group(blogger_id, user.tenant_id)
        if group_id is not None:
            await self._group(group_id, user.tenant_id, lock=True)
            if not set(ids) <= set(await self.members(group_id, user.tenant_id)):
                raise ValidationError("所选账号必须属于同一有效关联组")
        elif ids != [blogger_id]:
            raise ValidationError("请先由主管关联这些账号")
        rows = await self._accounts(user.tenant_id, ids)
        for row in rows:
            if not row.is_active:
                raise ValidationError("停用账号不能发起新合作")
            await ensure_new_cooperation_allowed(self.session, user, row.id)
        quote = None
        if group_id:
            quote = await self.session.scalar(
                select(BloggerAccountQuote).where(
                    BloggerAccountQuote.tenant_id == user.tenant_id,
                    BloggerAccountQuote.group_id == group_id,
                    BloggerAccountQuote.account_key == account_key(ids),
                )
            )
        amount = quote.amount if quote else (rows[0].quote if group_id is None else None)
        source: Literal["group_quote", "blogger_quote", "unknown"] = (
            "group_quote"
            if quote
            else ("blogger_quote" if group_id is None and amount is not None else "unknown")
        )
        return AccountSnapshot(
            primary_account_id=blogger_id,
            group_id=group_id,
            accounts=[
                AccountIdentity(
                    account_id=b.id,
                    platform=b.platform,
                    account=b.xiaohongshu_id,
                    nickname=b.nickname,
                )
                for b in rows
            ],
            quote_id=quote.id if quote else None,
            reference_amount=amount,
            price_source=source,
        ).model_dump(mode="json")
