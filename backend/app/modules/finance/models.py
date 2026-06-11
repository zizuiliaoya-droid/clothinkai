"""U05 ORM 模型：Settlement + SettlementExtraItem + SettlementSequence。

按 functional-design/domain-entities.md §3-§5 定义。

继承 ``TenantScopedModel``（U01）：
- 自动 ``id`` (UUID PK) + ``tenant_id`` (UUID FK + ORM 钩子)
- 自动 ``created_at`` / ``updated_at``
- 启用 RLS（migration 通过 ``rls.enable_rls_sql`` 配置）

业务键唯一约束（**FB3 永久 UNIQUE，无 partial WHERE**）：
- ``settlement``：
  - ``UNIQUE (tenant_id, settlement_no)`` 永久
  - ``UNIQUE (tenant_id, promotion_id)`` 永久（一个 promotion 一辈子只能有一条 settlement）
  - ``UNIQUE (request_event_id)`` 永久（事件重放兜底）
- ``settlement_sequence``：``UNIQUE (tenant_id, date_key)``

GIN trgm 索引（U05 强制建）：
- ``idx_settlement_no_trgm``（无 partial WHERE，所有 settlement 都活跃，FB3）

注：U05 settlement 表**不设 is_active 字段**（FB3：财务记录永久不可替换）。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


# ---------------------------------------------------------------------------
# Settlement（结算单）
# ---------------------------------------------------------------------------


class Settlement(TenantScopedModel):
    """结算单（财务核心表，FB3 永久不可替换）。

    22 业务字段（不含继承的 id / tenant_id / created_at / updated_at）。
    **不含 is_active 字段**（FB3）。
    """

    __tablename__ = "settlement"

    # --- 关联字段 ---
    promotion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="RESTRICT"),
        nullable=False,
    )
    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    paid_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_proof_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # --- 业务键 ---
    settlement_no: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- 金额字段 ---
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    # --- 业务字段 ---
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 状态字段（FB1 起点 = 待核查）---
    settlement_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'待核查'")
    )

    # --- 审核 / 驳回 ---
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 事件溯源（永久 UNIQUE）---
    request_event_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )

    __table_args__ = (
        # 业务键 + 永久幂等键（FB3 无 partial WHERE）
        Index("uq_settlement_no", "tenant_id", "settlement_no", unique=True),
        Index(
            "uq_settlement_promotion", "tenant_id", "promotion_id", unique=True
        ),
        Index(
            "uq_settlement_request_event_id", "request_event_id", unique=True
        ),
        # 列表过滤 + as_of 汇总
        Index(
            "idx_settlement_tenant_status",
            "tenant_id",
            "settlement_status",
            "created_at",
        ),
        Index("idx_settlement_blogger", "tenant_id", "blogger_id"),
        Index("idx_settlement_style", "tenant_id", "style_id"),
        Index("idx_settlement_pr", "tenant_id", "pr_id"),
        Index("idx_settlement_payment_date", "tenant_id", "payment_date"),
        Index("idx_settlement_reviewed_by", "tenant_id", "reviewed_by"),
        Index("idx_settlement_paid_by", "tenant_id", "paid_by"),
        # CHECK 约束
        CheckConstraint("amount >= 0", name="ck_settlement_amount_nonneg"),
        CheckConstraint(
            "total_amount >= 0", name="ck_settlement_total_amount_nonneg"
        ),
        CheckConstraint(
            "payment_amount IS NULL OR payment_amount >= 0",
            name="ck_settlement_payment_amount_nonneg",
        ),
        # GIN trgm 索引在 alembic migration 中通过 op.execute 创建：
        # idx_settlement_no_trgm
    )


# ---------------------------------------------------------------------------
# SettlementExtraItem（结算附加项）
# ---------------------------------------------------------------------------


class SettlementExtraItem(TenantScopedModel):
    """结算附加项（运费 / 赞奖 / 其他）。

    业务约束（service 层 BR-U05-40 强制）：
    - 仅 settlement_status="待付款" 时允许新增 / 修改
    - amount > 0
    - 修改时同事务更新 settlement.total_amount
    """

    __tablename__ = "settlement_extra_item"

    settlement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("settlement.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "idx_extra_item_settlement", "tenant_id", "settlement_id"
        ),
        CheckConstraint("amount > 0", name="ck_extra_item_amount_pos"),
    )


# ---------------------------------------------------------------------------
# SettlementSequence（settlement_no 序列号表）
# ---------------------------------------------------------------------------


class SettlementSequence(TenantScopedModel):
    """settlement_no 序列号表（按 (tenant_id, date_key) 累计，复用 U04 FB2 模式）。

    生成策略：
        INSERT ... ON CONFLICT (tenant_id, date_key) DO UPDATE
        SET last_seq = last_seq + 1 RETURNING last_seq

    单条 SQL 原子，无 race window（与 U04 PromotionSequence 完全一致）。
    """

    __tablename__ = "settlement_sequence"

    date_key: Mapped[date] = mapped_column(Date, nullable=False)
    last_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        Index(
            "uq_settlement_sequence",
            "tenant_id",
            "date_key",
            unique=True,
        ),
        CheckConstraint(
            "last_seq >= 0",
            name="ck_settlement_sequence_nonneg",
        ),
        CheckConstraint(
            "last_seq <= 9999",
            name="ck_settlement_sequence_max",
        ),
    )


__all__ = ["Settlement", "SettlementExtraItem", "SettlementSequence"]
