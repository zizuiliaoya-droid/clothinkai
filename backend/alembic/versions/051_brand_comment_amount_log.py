"""品牌词评论截图必传 + 金额变更时间线。

两件事合在一批，因为都是「补上之前刻意推迟的东西」。

**1. 品牌词评论截图（PRD 改动 5）**

原文：「品牌词评论截图（PR 提交发布审核时必传）」。改动 5 整节标题是「业务方明确保留
不变」，所以这条业务方已经确认过，不需要再问。「提交发布审核」就是 ``publish()`` ——
现有链路是 publish → 自动推进待核查 → 主管 review approve，publish 那一步是 PR 把单据
交给主管。

门槛放在 service 而不是 DB CHECK：生产有 5154 条未发布的历史单，加 CHECK 会把它们
全卡住；已发布的 2 条历史单也不回溯要求补图。

注意这是**会挡业务的硬约束** —— 上线后 PR 不传截图就发不了单。与寄拍的寄回单号同一个
形状（独立上传端点 + 推进时校验），所以前端交互可以照抄那套。

**2. 金额变更时间线（从批次 2a 挪来）**

批次 2a 当时的决定是「成本留痕只记 ``*_changed`` 不记金额」，理由写在
``promotion/domain.py`` 的 ``PROMOTION_SENSITIVE_VALUE_FIELDS`` 文档里：audit_log 的
读取面（``GET /auth/audit-logs``）是单一粗粒度闸门 ``auth.audit:read``，而金额受字段级
权限保护（``field.promotion.quote_amount:read`` 只给 admin/pr/pr_manager/finance）。
把金额写进 audit 等于绕过字段级门控。

所以金额级回溯用专表，读取时套同一层字段级判定。

**为什么不给这张表新建一个 ``promotion.amount_log:read`` scope**：``has()`` 的前缀通配
只看第一段，运营持 ``promotion.*:read``，任何 ``promotion.xxx:read`` 都会被命中 ——
于是运营能读到金额历史，而他们看不到金额本身。读权限必须走
``can_read_field("promotion", "quote_amount", ctx)``，和推广响应里过滤金额用的是同一个闸门。

Revision ID: 051_brand_comment_amount_log
Revises: 050_retrospective
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "051_brand_comment_amount_log"
down_revision: str | Sequence[str] | None = "050_retrospective"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AMOUNT_FIELDS = ("quote_amount", "cost_snapshot", "return_shipping_fee")
_SOURCES = ("手动编辑", "模式初始化", "模式兜底")


def _log(msg: str) -> None:
    print(f"[051] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 品牌词评论截图
    # ------------------------------------------------------------------ #
    op.add_column(
        "promotion",
        sa.Column("brand_comment_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_promotion_brand_comment_attachment",
        "promotion",
        "attachment",
        ["brand_comment_attachment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_promotion_brand_comment_attachment",
        "promotion",
        ["brand_comment_attachment_id"],
        postgresql_where=sa.text("brand_comment_attachment_id IS NOT NULL"),
    )

    unpublished = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM promotion "
            "WHERE is_active = true AND publish_status = '未发布'"
        )
    ).scalar_one()
    published = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM promotion "
            "WHERE is_active = true AND publish_status = '已发布'"
        )
    ).scalar_one()
    _log("promotion.brand_comment_attachment_id 已添加")
    _log(f"  已发布 {published} 条不回溯要求补图；{unpublished} 条未发布单今后发布时必须带图")

    # ------------------------------------------------------------------ #
    # promotion_amount_log
    # ------------------------------------------------------------------ #
    op.create_table(
        "promotion_amount_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(32), nullable=False),
        sa.Column("before_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("after_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("change_source", sa.String(16), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "field_name IN ('quote_amount', 'cost_snapshot', 'return_shipping_fee')",
            name="ck_amount_log_field_name",
        ),
        sa.CheckConstraint(
            "change_source IN ('手动编辑', '模式初始化', '模式兜底')",
            name="ck_amount_log_change_source",
        ),
        # 没变就不该留一行。IS DISTINCT FROM 顺带处理 NULL（cost_snapshot 可空）
        sa.CheckConstraint(
            "before_value IS DISTINCT FROM after_value",
            name="ck_amount_log_actually_changed",
        ),
        sa.CheckConstraint(
            "before_value IS NULL OR before_value >= 0", name="ck_amount_log_before_nonneg"
        ),
        sa.CheckConstraint(
            "after_value IS NULL OR after_value >= 0", name="ck_amount_log_after_nonneg"
        ),
    )
    # 单据时间线：按单据倒序取
    op.create_index(
        "idx_amount_log_promotion",
        "promotion_amount_log",
        ["tenant_id", "promotion_id", sa.text("created_at DESC")],
    )
    op.execute(enable_rls_sql("promotion_amount_log"))
    _log("promotion_amount_log 表已创建（含 RLS）")
    _log(f"  覆盖字段：{', '.join(_AMOUNT_FIELDS)}")
    _log(f"  变更来源：{', '.join(_SOURCES)}")
    _log("  读权限走 can_read_field(promotion, quote_amount)，不新建 scope")
    _log("  （新建 promotion.xxx:read 会被运营的 promotion.*:read 通配命中）")


def downgrade() -> None:
    op.execute(disable_rls_sql("promotion_amount_log"))
    op.drop_table("promotion_amount_log")

    op.drop_index("idx_promotion_brand_comment_attachment", table_name="promotion")
    op.drop_constraint("fk_promotion_brand_comment_attachment", "promotion", type_="foreignkey")
    op.drop_column("promotion", "brand_comment_attachment_id")
