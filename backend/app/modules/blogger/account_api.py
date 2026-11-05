"""同人账号/组合报价，沿用博主管理和金额字段权限。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security.field_permissions import can_read_field, can_write_field
from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.blogger.account_schemas import (
    AccountGroupInput,
    AccountGroupView,
    AccountQuoteInput,
    AccountQuotePreview,
    AccountQuoteResolve,
    AccountSnapshot,
)
from app.modules.blogger.account_service import AccountGroupService, visible_snapshot
from app.modules.blogger.claim_access import load_blogger_access

router = APIRouter(prefix="/bloggers", dependencies=[require_permission("blogger", "read")])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{blogger_id}/account-group", response_model=AccountGroupView)
async def account_group(
    blogger_id: UUID, user: CurrentActiveUser, session: Session
) -> AccountGroupView:
    return await AccountGroupService(session).view(blogger_id, user)


@router.post("/account-groups", response_model=AccountGroupView)
async def create_group(
    payload: AccountGroupInput, user: CurrentActiveUser, session: Session
) -> AccountGroupView:
    result = await AccountGroupService(session).save_group(payload, user)
    await session.commit()
    return result


@router.put("/account-groups/{group_id}", response_model=AccountGroupView)
async def update_group(
    group_id: UUID, payload: AccountGroupInput, user: CurrentActiveUser, session: Session
) -> AccountGroupView:
    result = await AccountGroupService(session).save_group(payload, user, group_id)
    await session.commit()
    return result


@router.put("/account-groups/{group_id}/quotes", response_model=AccountGroupView)
async def save_quote(
    group_id: UUID, payload: AccountQuoteInput, user: CurrentActiveUser, session: Session
) -> AccountGroupView:
    result = await AccountGroupService(session).save_quote(group_id, payload, user)
    await session.commit()
    return result


@router.post("/account-quotes/resolve", response_model=AccountQuotePreview)
async def quote_preview(
    payload: AccountQuoteResolve, user: CurrentActiveUser, session: Session
) -> AccountQuotePreview:
    snapshot = await AccountGroupService(session).resolve_snapshot(
        payload.blogger_id, payload.account_ids, user
    )
    access = await load_blogger_access(session, user, [])
    safe = visible_snapshot(snapshot, access.field_ctx)
    assert safe is not None
    return AccountQuotePreview(
        account_snapshot=AccountSnapshot.model_validate(safe),
        reference_amount=safe["reference_amount"],
        matched=snapshot["reference_amount"] is not None,
        can_write_amount=not access.readonly
        and can_write_field("promotion", "quote_amount", access.field_ctx),
        can_read_quote=can_read_field("blogger", "quote", access.field_ctx)
        and can_read_field("promotion", "quote_amount", access.field_ctx),
    )
