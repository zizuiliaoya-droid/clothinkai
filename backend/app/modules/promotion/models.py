"""U04 ORM 模型：Promotion + PromotionSequence。

按 functional-design/domain-entities.md §3-§5 定义。

继承 ``TenantScopedModel``：
- 自动 ``id`` (UUID PK) + ``tenant_id`` (UUID FK + ORM 钩子)
- 自动 ``created_at`` / ``updated_at``
- 启用 RLS（migration 通过 ``rls.enable_rls_sql`` 配置）

业务键唯一约束：
- ``promotion``：``(tenant_id, internal_code) WHERE is_active=true``
- ``promotion_sequence``：``(tenant_id, date_key)``（无 is_active；表本身不软删）

GIN trgm 索引（U04 强制建，对应 NFR §5 模糊搜索路径）：
- ``idx_promotion_internal_code_trgm``
- ``idx_promotion_style_code_snapshot_trgm``
- ``idx_promotion_short_name_trgm``

注：U04 不设 ``is_deleted`` 字段；删除走 ``publish_status="已删除"`` 状态机路径。
``is_active`` 用于软停用（与状态机正交）。
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


# ---------------------------------------------------------------------------
# Promotion（推广合作）
# ---------------------------------------------------------------------------


class Promotion(TenantScopedModel):
    """推广合作（业务核心表）。

    28 业务字段（不含继承的 id / tenant_id / created_at / updated_at）。
    """

    __tablename__ = "promotion"

    # --- 关联字段 ---
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sku.id", ondelete="RESTRICT"),
        nullable=True,
    )
    blogger_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("blogger.id", ondelete="RESTRICT"),
        nullable=False,
    )
    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- 业务键 ---
    internal_code: Mapped[str] = mapped_column(String(64), nullable=False)

    # --- 快照字段（创建时一次性写入，不再重算）---
    style_code_snapshot: Mapped[str] = mapped_column(String(64), nullable=False)
    style_short_name_snapshot: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    quote_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    cost_snapshot: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )

    # --- 业务字段 ---
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    cooperation_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_publish_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    actual_publish_date: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    publish_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recall_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    like_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 人工源列扩展（对齐 final.xlsx：颜色及规格/打单地址/发货单号/订单号/寄回单号/合作方式/合作形式/收藏数/评论数/博主风格 等）
    source_extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # --- U16 拍单 ---
    in_store_order: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    # --- 三个状态字段 ---
    publish_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未发布'")
    )
    recall_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未召回'")
    )
    settlement_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'未核查'")
    )

    # --- 审核相关 ---
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 通用 ---
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )

    __table_args__ = (
        # 业务键唯一（部分索引：仅 active 行参与唯一性）
        Index(
            "uq_promotion_internal_code",
            "tenant_id",
            "internal_code",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
        # 列表过滤
        Index(
            "idx_promotion_tenant_active",
            "tenant_id",
            "is_active",
            "publish_status",
        ),
        Index("idx_promotion_pr", "tenant_id", "pr_id"),
        # 重复检测 + 按博主 / 款式查
        Index(
            "idx_promotion_blogger",
            "tenant_id",
            "blogger_id",
            "publish_status",
        ),
        Index(
            "idx_promotion_style",
            "tenant_id",
            "style_id",
            "publish_status",
        ),
        # 排序 + urge 计算
        Index(
            "idx_promotion_cooperation_date",
            "tenant_id",
            "cooperation_date",
        ),
        Index(
            "idx_promotion_scheduled_date",
            "tenant_id",
            "scheduled_publish_date",
        ),
        Index(
            "idx_promotion_publish_dates",
            "tenant_id",
            "publish_status",
            "scheduled_publish_date",
        ),
        # 财务 / 召回查询
        Index(
            "idx_promotion_settlement_status",
            "tenant_id",
            "settlement_status",
        ),
        Index(
            "idx_promotion_recall_status",
            "tenant_id",
            "recall_status",
        ),
        # CHECK 约束
        CheckConstraint(
            "like_count IS NULL OR like_count >= 0",
            name="ck_promotion_like_count_nonneg",
        ),
        CheckConstraint(
            "quote_amount >= 0",
            name="ck_promotion_quote_amount_nonneg",
        ),
        CheckConstraint(
            "cost_snapshot IS NULL OR cost_snapshot >= 0",
            name="ck_promotion_cost_snapshot_nonneg",
        ),
        # GIN trgm 索引在 alembic migration 中通过 op.execute 创建：
        # idx_promotion_internal_code_trgm
        # idx_promotion_style_code_snapshot_trgm
        # idx_promotion_short_name_trgm
    )


# ---------------------------------------------------------------------------
# PromotionSequence（internal_code 序列号表）
# ---------------------------------------------------------------------------


class PromotionSequence(TenantScopedModel):
    """internal_code 序列号表（按 (tenant_id, date_key) 累计）。

    生成策略（FB2 修正）：
        INSERT ... ON CONFLICT (tenant_id, date_key) DO UPDATE
        SET last_seq = last_seq + 1 RETURNING last_seq

    单条 SQL 原子，无 race window。
    """

    __tablename__ = "promotion_sequence"

    date_key: Mapped[date] = mapped_column(Date, nullable=False)
    last_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        Index(
            "uq_promotion_sequence",
            "tenant_id",
            "date_key",
            unique=True,
        ),
        CheckConstraint(
            "last_seq >= 0",
            name="ck_promotion_sequence_nonneg",
        ),
        CheckConstraint(
            "last_seq <= 9999",
            name="ck_promotion_sequence_max",
        ),
    )


__all__ = ["Promotion", "PromotionSequence"]
