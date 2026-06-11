"""U10a design 仓储层。

- 子表 upsert（fabric/pattern/craft，1:1）
- update_design_status：乐观并发 UPDATE WHERE design_status=:from RETURNING（P-U10a-01）
- add_workflow_log
- bulk_update_sku_cost_price / bulk_update_sku_tag_price（系统口径，P-U10a-02）
- list_grouped / get_detail 聚合
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.design.models import (
    DesignWorkflowLog,
    StyleCraft,
    StyleFabric,
    StylePattern,
)
from app.modules.product.models import Sku, Style


class DesignRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- style ----------------------- #

    async def get_style(self, style_id: UUID) -> Style | None:
        style = await self._session.get(Style, style_id)
        if style is None or style.is_deleted:
            return None
        return style

    async def style_code_exists(self, style_code: str) -> bool:
        stmt = select(func.count()).select_from(Style).where(
            Style.style_code == style_code, Style.is_deleted.is_(False)
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    def add_style(self, style: Style) -> None:
        self._session.add(style)

    async def update_design_status(
        self, style_id: UUID, from_status: str, to_status: str
    ) -> bool:
        """乐观并发推进：仅当 design_status 仍为 from_status 时更新。

        返回 True 表示推进成功；False 表示并发冲突（状态已变）。
        """
        stmt = (
            update(Style)
            .where(Style.id == style_id, Style.design_status == from_status)
            .values(design_status=to_status)
            .returning(Style.id)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return row is not None

    # ----------------------- 子表 upsert ----------------------- #

    async def get_fabric(self, style_id: UUID) -> StyleFabric | None:
        stmt = select(StyleFabric).where(StyleFabric.style_id == style_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_fabric(
        self,
        style_id: UUID,
        *,
        fabrics: list | None = None,
        accessories: list | None = None,
        remark: str | None = None,
        is_completed: bool | None = None,
    ) -> StyleFabric:
        row = await self.get_fabric(style_id)
        if row is None:
            row = StyleFabric(style_id=style_id)
            self._session.add(row)
        if fabrics is not None:
            row.fabrics = fabrics
        if accessories is not None:
            row.accessories = accessories
        if remark is not None:
            row.remark = remark
        if is_completed is not None:
            row.is_completed = is_completed
        await self._session.flush()
        return row

    async def get_pattern(self, style_id: UUID) -> StylePattern | None:
        stmt = select(StylePattern).where(StylePattern.style_id == style_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_pattern(
        self,
        style_id: UUID,
        *,
        pattern_no: str | None = None,
        pattern_file_key: str | None = None,
        grading_data: dict | None = None,
    ) -> StylePattern:
        row = await self.get_pattern(style_id)
        if row is None:
            row = StylePattern(style_id=style_id)
            self._session.add(row)
        if pattern_no is not None:
            row.pattern_no = pattern_no
        if pattern_file_key is not None:
            row.pattern_file_key = pattern_file_key
        if grading_data is not None:
            row.grading_data = grading_data
        await self._session.flush()
        return row

    async def get_craft(self, style_id: UUID) -> StyleCraft | None:
        stmt = select(StyleCraft).where(StyleCraft.style_id == style_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_craft(self, style_id: UUID, *, craft_info: dict) -> StyleCraft:
        row = await self.get_craft(style_id)
        if row is None:
            row = StyleCraft(style_id=style_id, craft_info=craft_info)
            self._session.add(row)
        else:
            row.craft_info = craft_info
        await self._session.flush()
        return row

    # ----------------------- workflow log ----------------------- #

    def add_workflow_log(
        self,
        style_id: UUID,
        from_status: str | None,
        to_status: str,
        *,
        action: str,
        actor_id: UUID | None = None,
        driven_by: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            DesignWorkflowLog(
                style_id=style_id,
                from_status=from_status,
                to_status=to_status,
                action=action,
                actor_id=actor_id,
                driven_by=driven_by,
                reason=reason,
            )
        )

    async def list_workflow_log(self, style_id: UUID) -> Sequence[DesignWorkflowLog]:
        stmt = (
            select(DesignWorkflowLog)
            .where(DesignWorkflowLog.style_id == style_id)
            .order_by(DesignWorkflowLog.created_at.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    # ----------------------- SKU 批量（系统口径） ----------------------- #

    async def bulk_update_sku_cost_price(
        self, style_id: UUID, value: Decimal
    ) -> int:
        stmt = (
            update(Sku)
            .where(
                Sku.style_id == style_id,
                Sku.is_active.is_(True),
                Sku.is_deleted.is_(False),
            )
            .values(cost_price=value)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    async def bulk_update_sku_tag_price(
        self, style_id: UUID, value: Decimal
    ) -> int:
        stmt = (
            update(Sku)
            .where(
                Sku.style_id == style_id,
                Sku.is_active.is_(True),
                Sku.is_deleted.is_(False),
            )
            .values(tag_price=value)
        )
        result = await self._session.execute(stmt)
        return int(result.rowcount or 0)

    # ----------------------- 列表 / 详情聚合 ----------------------- #

    async def list_grouped(self, *, tenant_id: UUID) -> list[tuple[str, int]]:
        """按 design_status 分组计数（显式 tenant 过滤，防御 + 测试确定性）。"""
        stmt = (
            select(Style.design_status, func.count())
            .where(Style.tenant_id == tenant_id, Style.is_deleted.is_(False))
            .group_by(Style.design_status)
        )
        return [(r[0], int(r[1])) for r in (await self._session.execute(stmt)).all()]

    async def list_by_status(
        self, *, tenant_id: UUID, design_status: str, limit: int = 50
    ) -> Sequence[Style]:
        stmt = (
            select(Style)
            .where(
                Style.tenant_id == tenant_id,
                Style.is_deleted.is_(False),
                Style.design_status == design_status,
            )
            .order_by(Style.updated_at.desc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()


__all__ = ["DesignRepository"]
