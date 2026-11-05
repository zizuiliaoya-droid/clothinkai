"""账号约定履约；只检查新快照单，独立寄回运费不调用此模块。"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.modules.blogger.account_schemas import AccountSnapshot, FulfillmentSummary
from app.modules.promotion.enums import PublishStatus, SettlementStatus
from app.modules.promotion.models import Promotion, PromotionPlatformPost
from app.modules.promotion.schemas import PlatformPostIn, PlatformPostResponse, _check_publish_url
from app.modules.promotion.urge_calculator import get_today


def post_complete(
    row: PlatformPostIn | PlatformPostResponse | PromotionPlatformPost, today: date
) -> bool:
    if not row.publish_url or row.actual_publish_date is None or row.actual_publish_date > today:
        return False
    try:
        _check_publish_url(row.publish_url)
    except ValueError:
        return False
    return True


def fulfillment_summary(
    snapshot: dict | None,
    rows: Sequence[PlatformPostResponse | PromotionPlatformPost],
    *,
    today: date | None = None,
) -> FulfillmentSummary | None:
    if snapshot is None:
        return None
    agreement = AccountSnapshot.model_validate(snapshot)
    posts = {row.account_id: row for row in rows}
    day = today or get_today()
    missing = [
        a
        for a in agreement.accounts
        if a.account_id not in posts or not post_complete(posts[a.account_id], day)
    ]
    exact = len(posts) == len(rows) == len(agreement.accounts) and set(posts) == {
        a.account_id for a in agreement.accounts
    }
    return FulfillmentSummary(
        total=len(agreement.accounts),
        completed=len(agreement.accounts) - len(missing),
        missing_accounts=missing,
        complete=exact and not missing,
    )


async def account_posts(session: AsyncSession, promotion: Promotion) -> list[PromotionPlatformPost]:
    return list(
        (
            await session.scalars(
                select(PromotionPlatformPost)
                .where(
                    PromotionPlatformPost.tenant_id == promotion.tenant_id,
                    PromotionPlatformPost.promotion_id == promotion.id,
                )
                .order_by(PromotionPlatformPost.sort_order)
            )
        ).all()
    )


async def initialize_account_posts(
    session: AsyncSession, promotion: Promotion, actor_id: UUID | None
) -> None:
    if promotion.account_snapshot is None:
        return
    agreement = AccountSnapshot.model_validate(promotion.account_snapshot)
    for order, account in enumerate(agreement.accounts):
        session.add(
            PromotionPlatformPost(
                tenant_id=promotion.tenant_id,
                promotion_id=promotion.id,
                account_id=account.account_id,
                platform=account.platform,
                account=account.account,
                nickname=account.nickname,
                is_primary=account.account_id == agreement.primary_account_id,
                sort_order=order,
                created_by=actor_id,
            )
        )
    await session.flush()


async def sync_primary_post(session: AsyncSession, promotion: Promotion) -> None:
    if promotion.account_snapshot is None:
        return
    rows = await account_posts(session, promotion)
    primary = next((row for row in rows if row.account_id == promotion.blogger_id), None)
    if primary is None:
        raise ValidationError("主账号履约项缺失，请联系管理员排查")
    primary.publish_url = promotion.publish_url
    primary.actual_publish_date = promotion.actual_publish_date
    await session.flush()


async def lock_promotion(session: AsyncSession, promotion_id: UUID, tenant_id: UUID) -> Promotion:
    """业务费相关写入统一先锁主单；驳回只需要锁，不要求履约完成。"""
    promotion = await session.scalar(
        select(Promotion)
        .where(Promotion.id == promotion_id, Promotion.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if promotion is None:
        raise ResourceNotFoundError("推广单不存在")
    return promotion


async def require_fulfilled(session: AsyncSession, promotion_id: UUID, tenant_id: UUID) -> None:
    """所有业务费入口统一先锁主单；调用者不得先持有结款行锁。"""
    promotion = await lock_promotion(session, promotion_id, tenant_id)
    if promotion.account_snapshot is None:
        return
    summary = fulfillment_summary(
        promotion.account_snapshot, await account_posts(session, promotion)
    )
    if summary is not None and (
        not summary.complete or promotion.publish_status != PublishStatus.PUBLISHED.value
    ):
        raise ValidationError(
            "全部约定账号完成发布后才可结业务费",
            code="ACCOUNT_FULFILLMENT_REQUIRED",
            details={"fulfillment": summary.model_dump(mode="json")},
        )


async def update_account_posts(
    session: AsyncSession, promotion: Promotion, inputs: Sequence[PlatformPostIn]
) -> None:
    """已由调用方锁主单。义务集合不变；只修事实，不重建子行。"""
    agreement = AccountSnapshot.model_validate(promotion.account_snapshot)
    expected = {account.account_id: account for account in agreement.accounts}
    supplied = {row.account_id: row for row in inputs}
    if len(supplied) != len(inputs) or set(supplied) != set(expected):
        raise ValidationError("必须保留全部约定账号，不能删除、遗漏或重复履约项")
    rows = await account_posts(session, promotion)
    if {row.account_id for row in rows} != set(expected):
        raise ValidationError("账号履约项缺失，请联系管理员排查")
    settling = promotion.settlement_status in {
        SettlementStatus.PENDING_PAYMENT.value,
        SettlementStatus.PAID.value,
        SettlementStatus.SETTLED.value,
        SettlementStatus.SETTLEMENT_REJECTED.value,
    }
    for row in rows:
        item = supplied[row.account_id]
        if item.platform != row.platform:
            raise ValidationError("约定账号的平台不可修改")
        if (
            row.is_primary
            and promotion.publish_status != PublishStatus.PUBLISHED.value
            and (
                item.publish_url != row.publish_url
                or item.actual_publish_date != row.actual_publish_date
            )
        ):
            raise ValidationError("请通过主账号的发布操作登记首次发布")
        if settling and not post_complete(item, get_today()):
            raise ValidationError("结款流程中不能清空必要发布事实，可更正有效链接和日期")
    for row in rows:
        item = supplied[row.account_id]
        row.publish_url, row.actual_publish_date, row.like_count = (
            item.publish_url,
            item.actual_publish_date,
            item.like_count,
        )
        if row.is_primary and promotion.publish_status == PublishStatus.PUBLISHED.value:
            promotion.publish_url, promotion.actual_publish_date = (
                item.publish_url,
                item.actual_publish_date,
            )
    await session.flush()
