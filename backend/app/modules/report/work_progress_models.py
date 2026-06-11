"""U14 报表配置表 ORM：TargetPlanning（爆款约篇目标）+ StoreDaily（店铺手动字段）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class TargetPlanning(TenantScopedModel):
    """爆款约篇目标（PR × 款式 × 月）。"""

    __tablename__ = "target_planning"

    pr_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_month: Mapped[str] = mapped_column(String(7), nullable=False)
    min_target: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index(
            "uq_target_planning",
            "tenant_id", "pr_id", "style_id", "period_month",
            unique=True,
        ),
        Index("idx_target_planning_month", "tenant_id", "period_month"),
        CheckConstraint("min_target >= 0", name="ck_target_planning_min"),
    )


class StoreDaily(TenantScopedModel):
    """店铺日报手动输入字段（按 date）。"""

    __tablename__ = "store_daily"

    date: Mapped[date] = mapped_column(Date, nullable=False)
    ad_spend_total: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    zhitongche_spend: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    yinli_spend: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("uq_store_daily_date", "tenant_id", "date", unique=True),
    )


__all__ = ["StoreDaily", "TargetPlanning"]
