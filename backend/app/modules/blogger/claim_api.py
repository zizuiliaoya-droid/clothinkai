"""认领及工作微信管理；所有写操作仍由支持会话的只读门拦截。"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.db import get_session
from app.core.exceptions import ResourceNotFoundError
from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.auth.models import Role, User, UserRole
from app.modules.blogger.claim_schemas import (
    ClaimAssignRequest,
    ClaimResponse,
    ClaimSelfRequest,
    ClaimSelfResult,
    ClaimSettings,
    ClaimTransferRequest,
    ClaimWorkWechatUpdate,
    WorkWechatCreate,
    WorkWechatItem,
    WorkWechatPage,
    WorkWechatUpdate,
)
from app.modules.blogger.claim_service import WORK_WECHAT_TYPE, BloggerClaimService
from app.modules.product.dict_models import DictItem

router = APIRouter(prefix="/bloggers", dependencies=[require_permission("blogger", "read")])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/claim-settings", response_model=ClaimSettings)
async def claim_settings(user: CurrentActiveUser, session: Session) -> ClaimSettings:
    return await BloggerClaimService(session).settings(user)


@router.post("/claim-settings/enable", response_model=ClaimSettings)
async def enable_claims(user: CurrentActiveUser, session: Session) -> ClaimSettings:
    result = await BloggerClaimService(session).enable(user)
    await session.commit()
    return result


@router.get("/claim-pr-options")
async def claim_pr_options(user: CurrentActiveUser, session: Session) -> list[dict[str, Any]]:
    await BloggerClaimService(session).require_manager(user)
    rows = (
        await session.scalars(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.tenant_id == user.tenant_id,
                UserRole.tenant_id == user.tenant_id,
                Role.code == "pr",
                User.status == "active",
                User.deleted_at.is_(None),
                User.locked_at.is_(None),
            )
            .order_by(User.username)
        )
    ).all()
    return [{"id": row.id, "name": row.display_name or row.username} for row in rows]


@router.post("/claims/self", response_model=list[ClaimSelfResult])
async def self_claims(
    payload: ClaimSelfRequest, user: CurrentActiveUser, session: Session
) -> list[ClaimSelfResult]:
    service = BloggerClaimService(session)
    results = [
        ClaimSelfResult(
            blogger_id=item.blogger_id,
            status=await service.self_claim(
                item.blogger_id, item.wechat, item.work_wechat_id, user
            ),
        )
        for item in payload.items
    ]
    await session.commit()
    return results


@router.post("/claims/assign")
async def assign_claims(
    payload: ClaimAssignRequest, user: CurrentActiveUser, session: Session
) -> dict[str, int]:
    service = BloggerClaimService(session)
    added = 0
    for item in payload.items:
        added += await service.assign(item.blogger_id, item.pr_id, item.work_wechat_id, user)
    await session.commit()
    return {"assigned": added, "existing": len(payload.items) - added}


@router.get("/work-wechats", response_model=WorkWechatPage)
async def work_wechats(user: CurrentActiveUser, session: Session) -> WorkWechatPage:
    rows = (
        await session.scalars(
            select(DictItem)
            .where(DictItem.tenant_id == user.tenant_id, DictItem.dict_type == WORK_WECHAT_TYPE)
            .order_by(DictItem.sort_order, DictItem.value)
        )
    ).all()
    return WorkWechatPage(
        items=[WorkWechatItem(id=r.id, value=r.value, is_active=r.is_active) for r in rows],
        can_manage=(await BloggerClaimService(session).access(user)).can_manage,
    )


@router.post("/work-wechats", response_model=WorkWechatItem)
async def create_work_wechat(
    payload: WorkWechatCreate, user: CurrentActiveUser, session: Session
) -> WorkWechatItem:
    await BloggerClaimService(session).require_manager(user)
    row = await session.scalar(
        pg_insert(DictItem)
        .values(tenant_id=user.tenant_id, dict_type=WORK_WECHAT_TYPE, value=payload.value)
        .on_conflict_do_nothing(index_elements=["tenant_id", "dict_type", "value"])
        .returning(DictItem)
    )
    if row is None:
        row = (
            await session.scalars(
                select(DictItem).where(
                    DictItem.tenant_id == user.tenant_id,
                    DictItem.dict_type == WORK_WECHAT_TYPE,
                    DictItem.value == payload.value,
                )
            )
        ).one()
    else:
        await AuditService(session).log(
            action="blogger.work_wechat_create",
            resource="dict_item",
            resource_id=row.id,
            after={"value": row.value},
            user_id=user.id,
        )
    result = WorkWechatItem(id=row.id, value=row.value, is_active=row.is_active)
    await session.commit()
    return result


@router.patch("/work-wechats/{work_id}", response_model=WorkWechatItem)
async def update_work_wechat(
    work_id: UUID, payload: WorkWechatUpdate, user: CurrentActiveUser, session: Session
) -> WorkWechatItem:
    await BloggerClaimService(session).require_manager(user)
    row = await session.scalar(
        select(DictItem)
        .where(
            DictItem.id == work_id,
            DictItem.tenant_id == user.tenant_id,
            DictItem.dict_type == WORK_WECHAT_TYPE,
        )
        .with_for_update()
    )
    if row is None:
        raise ResourceNotFoundError("工作微信不存在")
    if row.is_active != payload.is_active:
        row.is_active = payload.is_active
        await AuditService(session).log(
            action="blogger.work_wechat_status",
            resource="dict_item",
            resource_id=row.id,
            after={"is_active": row.is_active},
            user_id=user.id,
        )
    result = WorkWechatItem(id=row.id, value=row.value, is_active=row.is_active)
    await session.commit()
    return result


@router.get("/{blogger_id}/claims", response_model=list[ClaimResponse])
async def list_claims(
    blogger_id: UUID, user: CurrentActiveUser, session: Session
) -> list[ClaimResponse]:
    return await BloggerClaimService(session).list_claims(blogger_id, user)


@router.delete("/{blogger_id}/claims/{pr_id}", status_code=204)
async def revoke_claim(
    blogger_id: UUID, pr_id: UUID, user: CurrentActiveUser, session: Session
) -> Response:
    await BloggerClaimService(session).revoke(blogger_id, pr_id, user)
    await session.commit()
    return Response(status_code=204)


@router.patch("/{blogger_id}/claims/{pr_id}", status_code=204)
async def switch_work_wechat(
    blogger_id: UUID,
    pr_id: UUID,
    payload: ClaimWorkWechatUpdate,
    user: CurrentActiveUser,
    session: Session,
) -> Response:
    await BloggerClaimService(session).switch_work_wechat(
        blogger_id, pr_id, payload.work_wechat_id, user
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/{blogger_id}/claims/{pr_id}/transfer", status_code=204)
async def transfer_claim(
    blogger_id: UUID,
    pr_id: UUID,
    payload: ClaimTransferRequest,
    user: CurrentActiveUser,
    session: Session,
) -> Response:
    await BloggerClaimService(session).transfer(
        blogger_id, pr_id, payload.to_pr_id, payload.work_wechat_id, user
    )
    await session.commit()
    return Response(status_code=204)
