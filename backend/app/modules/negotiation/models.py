"""谈款单 ORM（PRD V1.4 模块一；流程线 M2 起两级审核）。

一张谈款单对应「PR 和某个博主谈好了推某个商品」这件事。主管审核、老板定稿、PR 确认收货信息后系统自动建一张
推广单，两者用 ``promotion_id`` 关联、共用 ``line_id``（主线号）—— 之后所有执行（寄样、催发、召回、结款）都在
推广单上走，谈款单只留作审批记录。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel

# FK 目标表 style / sku / user：Celery 路径不一定经过 product / auth 模块，
# 不显式 import 的话 flush NegotiationItem 时 NoReferencedTableError（台账）
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.product import models as _product_models  # noqa: F401


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
    """对接 PR。建单人，也是老板退回后要去改的人。"""

    promotion_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="SET NULL"),
        nullable=True,
    )
    """定稿后生成的推广单。定稿前为空。

    ``SET NULL`` 而不是 ``RESTRICT``：推广单软停用后谈款单的审批记录仍要留着。
    """

    line_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    """主线号：定稿建的推广单继承它，时间线按它串谈款与推广单（流程线设计 6.1）。不建 FK。"""

    copied_from_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("negotiation.id", ondelete="SET NULL"),
        nullable=True,
    )
    """「复制为新谈款」的来源（审核驳回后重谈）。"""

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
    """第一级（PR 主管）审核人。"""
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_opinion: Mapped[str | None] = mapped_column(Text, nullable=True)
    """主管审核意见。驳回时必填，通过时可选。"""

    final_reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """第二级（老板 = 管理员）审核人。"""
    final_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    final_review_opinion: Mapped[str | None] = mapped_column(Text, nullable=True)
    """老板意见。退回时必填（service）。"""

    # --- 收货信息（只在「待补收货地址」可写；定稿时复制进推广单，与推广单同宽） ---
    receiver_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receiver_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    receiver_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    receiver_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    receiver_confirmed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    """实际确认收货信息（= 定稿）的人：本人，或 PR 不在时的主管 / 管理员。"""

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
        Index("uq_negotiation_line", "tenant_id", "line_id", unique=True),
        CheckConstraint(
            "status IN ('草稿', '主管待审', '审核驳回', '待老板审核', '老板退回', "
            "'待补收货地址', '已定稿')",
            name="ck_negotiation_status",
        ),
        CheckConstraint(
            "cooperation_mode IN ('寄拍', '送拍', '置换')",
            name="ck_negotiation_cooperation_mode",
        ),
        CheckConstraint("quote_amount >= 0", name="ck_negotiation_quote_amount_nonneg"),
        # 已定稿必须有推广单，没定稿不该有 —— 两者必须同步，否则是数据异常
        CheckConstraint(
            "(status = '已定稿' AND promotion_id IS NOT NULL)"
            " OR (status <> '已定稿' AND promotion_id IS NULL)",
            name="ck_negotiation_finalized_has_promotion",
        ),
    )


class NegotiationItem(TenantScopedModel):
    """谈款选的颜色尺码（7c-11），定稿时逐行复制成 ``promotion_item``。

    行的集合由谈款选的商品决定：单品 1 行，套装按 ``goods_style_item`` 成员各 1 行；草稿 / 老板退回时改商品，整组重建。
    ``sku_id`` 可空：草稿可以先不选，提交时 service 要求每行都有。
    """

    __tablename__ = "negotiation_item"

    negotiation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("negotiation.id", ondelete="RESTRICT"),
        nullable=False,
    )
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
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))

    __table_args__ = (
        Index(
            "uq_negotiation_item_style",
            "tenant_id",
            "negotiation_id",
            "style_id",
            unique=True,
        ),
        # SKU 删除前的引用校验用
        Index(
            "idx_negotiation_item_sku",
            "tenant_id",
            "sku_id",
            postgresql_where=text("sku_id IS NOT NULL"),
        ),
    )


__all__ = ["Negotiation", "NegotiationItem"]
