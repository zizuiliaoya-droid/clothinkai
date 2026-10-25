"""推广详情单条 / 报表批量读取共用的权限与数据范围；不采用 Timeline 授权。"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.modules.promotion.models import Promotion


def can_read_promotion(perms: EffectivePermissions) -> bool:
    return perms.has("promotion", "read")


def _read_conditions(tenant_id: UUID) -> tuple[ColumnElement[bool], ...]:
    return Promotion.tenant_id == tenant_id, Promotion.is_active.is_(True)


class PromotionReadAccess:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, promotion_id: UUID, *, tenant_id: UUID, perms: EffectivePermissions
    ) -> Promotion | None:
        if not can_read_promotion(perms):
            raise PermissionDeniedError(
                details={"required_scope": "promotion", "required_action": "read"}
            )
        stmt = select(Promotion).where(*_read_conditions(tenant_id), Promotion.id == promotion_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def visible_ids(
        self, ids: Sequence[UUID], *, tenant_id: UUID, perms: EffectivePermissions
    ) -> set[UUID]:
        if not ids or not can_read_promotion(perms):
            return set()
        stmt = select(Promotion.id).where(*_read_conditions(tenant_id), Promotion.id.in_(ids))
        return set((await self._session.execute(stmt)).scalars().all())
