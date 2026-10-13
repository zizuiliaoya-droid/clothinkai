"""平台指标配置仓储。"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.metric_config.models import PromotionPlatformSetting


class PromotionPlatformSettingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant(self, tenant_id: UUID) -> builtins.list[PromotionPlatformSetting]:
        # 显式 tenant 过滤：测试引擎与系统任务走 bypass 角色，RLS 不生效
        stmt = (
            select(PromotionPlatformSetting)
            .where(PromotionPlatformSetting.tenant_id == tenant_id)
            .order_by(PromotionPlatformSetting.platform)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def upsert(
        self,
        tenant_id: UUID,
        rows: Sequence[tuple[str, Decimal, int]],
        *,
        updated_by: UUID | None,
    ) -> None:
        """按 ``(tenant_id, platform)`` 写入 ``(平台, 系数, 阈值)``：没有就插，有就改。不 commit。"""
        if not rows:
            return
        stmt = pg_insert(PromotionPlatformSetting).values(
            [
                {
                    "tenant_id": tenant_id,
                    "platform": platform,
                    "like_coefficient": coef,
                    "hit_threshold": threshold,
                    "updated_by": updated_by,
                }
                for platform, coef, threshold in rows
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "platform"],
            set_={
                "like_coefficient": stmt.excluded.like_coefficient,
                "hit_threshold": stmt.excluded.hit_threshold,
                "updated_by": stmt.excluded.updated_by,
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)


__all__ = ["PromotionPlatformSettingRepository"]
