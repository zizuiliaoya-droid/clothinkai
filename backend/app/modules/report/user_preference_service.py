"""U17 用户偏好服务（get_or_default / upsert）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.report.user_preference_models import UserPreference


class UserPreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_or_default(
        self, user_id: UUID, key: str, default: dict
    ) -> dict:
        stmt = select(UserPreference).where(
            UserPreference.user_id == user_id,
            UserPreference.pref_key == key,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return row.pref_value if row is not None else default

    async def upsert(self, user: Any, key: str, value: dict) -> None:
        stmt = (
            pg_insert(UserPreference)
            .values(
                tenant_id=user.tenant_id,
                user_id=user.id,
                pref_key=key,
                pref_value=value,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "pref_key"],
                set_={"pref_value": value, "updated_at": func.now()},
            )
        )
        await self._s.execute(stmt)
        await self._s.commit()


__all__ = ["UserPreferenceService"]
