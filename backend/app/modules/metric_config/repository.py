"""平台指标配置仓储。"""

from __future__ import annotations

import builtins
from uuid import UUID

from sqlalchemy import select
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


__all__ = ["PromotionPlatformSettingRepository"]
