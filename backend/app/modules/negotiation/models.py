"""谈款单 ORM（PRD V1.4 模块一）。

一张谈款单对应「PR 和某个博主谈好了推某个商品」这件事。主管审核通过后系统自动建一张
推广单，两者用 ``promotion_id`` 关联 —— 之后所有执行（寄样、催发、召回、结款）都在
推广单上走，谈款单只留作审批记录。
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
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class Negotiation(TenantScopedModel):
    """谈款单。"""

    __tablename__ = "negotiation"

    # --- 关联 ---
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
    goods_main_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """谈的是哪个商品。不传则建推广单时由服务端推定主商品。"""

    pr_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """对接 PR。建单人，也是审核驳回后要去改的人。"""

    promotion_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="SET NULL"),
        nullable=True,
    )
    """审核通过后生成的推广单。审核通过前为空。

    ``SET NULL`` 而不是 ``RESTRICT``：推广单软停用后谈款单的审批记录仍要留着。
    """

    # --- 业务字段 ---
    cooperation_mode: Mapped[str] = mapped_column(String(8), nullable=False)
    """寄拍 / 送拍 / 置换。草稿阶段可改，生成推广单后由推广单那边锁死。"""

    platform: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'小红书'")
    )
    scheduled_publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    """约定发布时间。"""

    quote_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, server_default=text("0")
    )
    """博主服务费。置换模式强制 0（PRD：置换无博主服务费，接口也要校验防绕过前端）。"""

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- 状态与审核 ---
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'草稿'"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_opinion: Mapped[str | None] = mapped_column(Text, nullable=True)
    """审核意见。驳回时必填，通过时可选。"""

    __table_args__ = (
        Index("idx_negotiation_tenant_status", "tenant_id", "status"),
        Index("idx_negotiation_pr", "tenant_id", "pr_id", "status"),
        Index("idx_negotiation_blogger", "tenant_id", "blogger_id"),
        Index("idx_negotiation_style", "tenant_id", "style_id"),
        Index(
            "idx_negotiation_promotion",
            "promotion_id",
            postgresql_where=text("promotion_id IS NOT NULL"),
        ),
        CheckConstraint(
            "status IN ('草稿', '待审核', '审核通过', '审核驳回')",
            name="ck_negotiation_status",
        ),
        CheckConstraint(
            "cooperation_mode IN ('寄拍', '送拍', '置换')",
            name="ck_negotiation_cooperation_mode",
        ),
        CheckConstraint("quote_amount >= 0", name="ck_negotiation_quote_amount_nonneg"),
        # 审核通过必须有推广单，没通过不该有 —— 两者必须同步，否则是数据异常
        CheckConstraint(
            "(status = '审核通过' AND promotion_id IS NOT NULL)"
            " OR (status <> '审核通过' AND promotion_id IS NULL)",
            name="ck_negotiation_approved_has_promotion",
        ),
    )


__all__ = ["Negotiation"]
