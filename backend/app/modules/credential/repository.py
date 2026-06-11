"""U12 凭据仓储层。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.credential.models import Credential


class CredentialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, credential: Credential) -> None:
        self._session.add(credential)

    async def get_by_id(self, credential_id: UUID) -> Credential | None:
        return await self._session.get(Credential, credential_id)

    async def list(
        self,
        *,
        tenant_id: UUID,
        platform: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[Credential], int]:
        stmt = select(Credential).where(Credential.tenant_id == tenant_id)
        count_stmt = (
            select(func.count())
            .select_from(Credential)
            .where(Credential.tenant_id == tenant_id)
        )
        if platform is not None:
            stmt = stmt.where(Credential.platform == platform)
            count_stmt = count_stmt.where(Credential.platform == platform)
        if status is not None:
            stmt = stmt.where(Credential.status == status)
            count_stmt = count_stmt.where(Credential.status == status)
        stmt = (
            stmt.order_by(Credential.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return items, total


__all__ = ["CredentialRepository"]
