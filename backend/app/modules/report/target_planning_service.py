"""U14 TargetPlanningService（爆款约篇目标 set + 达标跟踪）。"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import report_query_duration_seconds
from app.modules.auth.models import User
from app.modules.report.advanced_repository import TargetPlanningRepository
from app.modules.report.advanced_schemas import (
    TargetCreate,
    TargetWithActual,
)
from app.modules.report.work_progress_models import TargetPlanning


class TargetPlanningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = TargetPlanningRepository(session)
        self._audit = AuditService(session)

    async def set_target(
        self, payload: TargetCreate, user: User
    ) -> TargetPlanning:
        stmt = (
            pg_insert(TargetPlanning)
            .values(
                tenant_id=user.tenant_id,
                pr_id=payload.pr_id,
                style_id=payload.style_id,
                period_month=payload.period_month,
                min_target=payload.min_target,
            )
            .on_conflict_do_update(
                index_elements=[
                    "tenant_id", "pr_id", "style_id", "period_month",
                ],
                set_={"min_target": payload.min_target, "updated_at": func.now()},
            )
            .returning(TargetPlanning)
        )
        result = await self._session.execute(stmt)
        target = result.scalar_one()
        await self._audit.log(
            action="report.target.set",
            resource="target_planning",
            resource_id=target.id,
            after={
                "pr_id": str(payload.pr_id),
                "style_id": str(payload.style_id),
                "period_month": payload.period_month,
                "min_target": payload.min_target,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return target

    async def list_with_actuals(
        self, tenant_id: UUID, month: str
    ) -> list[TargetWithActual]:
        with report_query_duration_seconds.labels("target").time():
            rows = await self._repo.list_with_actuals(
                tenant_id=tenant_id, month=month
            )
        return [self._to_row(r) for r in rows]

    @staticmethod
    def _to_row(r: Mapping[str, Any]) -> TargetWithActual:
        actual = int(r["actual_count"])
        min_t = int(r["min_target"])
        return TargetWithActual(
            id=r["id"],
            pr_id=r["pr_id"],
            pr_name=r["pr_name"],
            style_id=r["style_id"],
            style_code=r["style_code"],
            style_name=r["style_name"],
            period_month=r["period_month"],
            min_target=min_t,
            actual_count=actual,
            status="达标" if actual >= min_t else "未达标",
            gap=actual - min_t,
        )


__all__ = ["TargetPlanningService"]
