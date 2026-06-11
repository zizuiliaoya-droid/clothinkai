"""U10a design 模块 ORM 模型（4 表，均 TenantScopedModel + RLS）。

- style_fabric / style_pattern / style_craft：1:1 with style（UNIQUE(style_id)）
- design_workflow_log：N:1 with style（状态变迁业务时间线，append-only 语义）
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class StyleFabric(TenantScopedModel):
    """面辅料（设计师初填 + 设计助理补齐）。"""

    __tablename__ = "style_fabric"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="CASCADE"), nullable=False
    )
    fabrics: Mapped[list] = mapped_column(  # type: ignore[type-arg]
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    accessories: Mapped[list] = mapped_column(  # type: ignore[type-arg]
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    is_completed: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("uq_style_fabric_style", "style_id", unique=True),
    )


class StylePattern(TenantScopedModel):
    """版型（版号 + 文件 + 放码）。"""

    __tablename__ = "style_pattern"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="CASCADE"), nullable=False
    )
    pattern_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pattern_file_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    grading_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # type: ignore[type-arg]

    __table_args__ = (
        Index("uq_style_pattern_style", "style_id", unique=True),
    )


class StyleCraft(TenantScopedModel):
    """工艺信息（跟单录入）。"""

    __tablename__ = "style_craft"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="CASCADE"), nullable=False
    )
    craft_info: Mapped[dict] = mapped_column(  # type: ignore[type-arg]
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("uq_style_craft_style", "style_id", unique=True),
    )


class DesignWorkflowLog(TenantScopedModel):
    """状态变迁业务时间线（前端展示流程历史）。"""

    __tablename__ = "design_workflow_log"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    driven_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_design_wf_log_style", "tenant_id", "style_id", "created_at"),
    )


__all__ = [
    "DesignWorkflowLog",
    "StyleCraft",
    "StyleFabric",
    "StylePattern",
]
