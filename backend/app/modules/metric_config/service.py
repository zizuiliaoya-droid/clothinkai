"""平台指标配置服务（7e R7）。

``get_settings`` 是所有读取点的唯一入口：每个请求 / 每个任务按租户读一次，再把
``PlatformMetricSettings`` 显式传下去。缺行回落默认，不报错（见 ``settings.py``）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.metric_config.repository import PromotionPlatformSettingRepository
from app.modules.metric_config.settings import PlatformMetricSettings


class PlatformMetricSettingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PromotionPlatformSettingRepository(session)

    async def get_settings(self, tenant_id: UUID) -> PlatformMetricSettings:
        rows = await self._repo.list_for_tenant(tenant_id)
        return PlatformMetricSettings.from_rows(
            (r.platform, r.like_coefficient, r.hit_threshold) for r in rows
        )


__all__ = ["PlatformMetricSettingService"]
