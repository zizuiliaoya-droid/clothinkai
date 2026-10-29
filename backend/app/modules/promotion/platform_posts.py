"""其他平台仅录入展示；不被指标与报表依赖。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.modules.promotion.models import PromotionPlatformPost
from app.modules.promotion.schemas import PlatformPostIn, PlatformPostResponse


def validate_platforms(rows: Sequence[PlatformPostIn | PlatformPostResponse], primary: str) -> None:
    names = [r.platform for r in rows]
    if len(names) > 5 or len(set(names)) != len(names) or primary in names:
        raise ValidationError("其他平台最多5项，不能重复或与主平台相同")


async def posts_by_promotion(
    session: AsyncSession, ids: Sequence[UUID], *, tenant_id: UUID
) -> dict[UUID, list[PlatformPostResponse]]:
    if not ids:
        return {}
    rows = (
        await session.scalars(
            select(PromotionPlatformPost)
            .where(
                PromotionPlatformPost.tenant_id == tenant_id,
                PromotionPlatformPost.promotion_id.in_(ids),
            )
            .order_by(
                PromotionPlatformPost.promotion_id,
                PromotionPlatformPost.sort_order,
                PromotionPlatformPost.id,
            )
        )
    ).all()
    result: dict[UUID, list[PlatformPostResponse]] = {}
    for row in rows:
        result.setdefault(row.promotion_id, []).append(PlatformPostResponse.model_validate(row))
    return result


async def replace_posts(
    session: AsyncSession,
    *,
    promotion_id: UUID,
    tenant_id: UUID,
    actor_id: UUID,
    rows: Sequence[PlatformPostIn],
) -> None:
    """调用者必须先锁住同租户父推广单；本函数不提交事务。"""
    await session.execute(
        delete(PromotionPlatformPost).where(
            PromotionPlatformPost.promotion_id == promotion_id,
            PromotionPlatformPost.tenant_id == tenant_id,
        )
    )
    for order, row in enumerate(rows):
        session.add(
            PromotionPlatformPost(
                tenant_id=tenant_id,
                promotion_id=promotion_id,
                created_by=actor_id,
                sort_order=order,
                **row.model_dump(),
            )
        )
    await session.flush()
