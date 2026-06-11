"""U18 AI 留痕仓储 + 数据准备读取（promotion 历史 / alert / blogger 候选）。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.models import AiAdviceLog
from app.modules.blogger.models import Blogger
from app.modules.product.models import Style
from app.modules.wecom.alert_models import WecomAlertLog


class AiAdviceLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, row: AiAdviceLog) -> None:
        self._s.add(row)


class AiDataRepository:
    """跨单元只读数据准备（显式 tenant，RLS 兜底）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def promotion_months(self, tenant_id: UUID) -> int:
        """租户推广数据覆盖的不同自然月数（用于策略数据充足性判定）。"""
        stmt = text(
            "SELECT COUNT(DISTINCT to_char(cooperation_date, 'YYYY-MM')) "
            "FROM promotion WHERE tenant_id = :t AND is_active = true"
        )
        return int((await self._s.execute(stmt, {"t": str(tenant_id)})).scalar_one())

    async def get_alert(
        self, alert_id: UUID, tenant_id: UUID
    ) -> WecomAlertLog | None:
        stmt = select(WecomAlertLog).where(
            WecomAlertLog.id == alert_id,
            WecomAlertLog.tenant_id == tenant_id,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_style(self, style_id: UUID, tenant_id: UUID) -> Style | None:
        stmt = select(Style).where(
            Style.id == style_id, Style.tenant_id == tenant_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def candidate_bloggers(
        self, tenant_id: UUID, *, limit: int = 20
    ) -> Sequence[Blogger]:
        """候选博主（V1 规则预筛：活跃博主按粉丝量 Top；后续可增强匹配）。"""
        stmt = (
            select(Blogger)
            .where(
                Blogger.tenant_id == tenant_id,
                Blogger.is_active.is_(True),
                Blogger.is_deleted.is_(False),
            )
            .order_by(func.coalesce(Blogger.follower_count, 0).desc())
            .limit(limit)
        )
        return (await self._s.execute(stmt)).scalars().all()


__all__ = ["AiAdviceLogRepository", "AiDataRepository"]
