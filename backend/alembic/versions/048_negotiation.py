"""谈款审核：negotiation 表 + 权限。

PRD V1.4 模块一，之前零实现。PR 录入谈款信息 → 主管审核 → 通过后自动生成推广单。

为什么不复用 promotion 的审核字段：``promotion.reviewed_by/review_action/review_reason``
已经被**结款审核**占用（发布之后主管核查，驱动 settlement_status）。谈款审核是**建单之前**
的另一道审核，两者方向相反，挤在一组字段里会分不清。

权限用独立一级域 ``negotiation``，刻意不挂在 ``promotion.`` 下 ——
``has()`` 的前缀通配只看第一段，PR 持有 ``promotion.*:*``，
叫 ``promotion.negotiation:approve`` 的话 PR 会自动拿到主管的审核权限。
同理授给 PR 的是两条具体 scope 而不是 ``negotiation.*:*``。

Revision ID: 048_negotiation
Revises: 047_mode_flow
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "048_negotiation"
down_revision: str | Sequence[str] | None = "047_mode_flow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS: list[tuple[str, str]] = [
    ("negotiation:read", "查看谈款单"),
    ("negotiation:write", "新建 / 编辑草稿 / 提交审核"),
    ("negotiation.review:approve", "审核谈款单（通过 / 驳回）"),
]

# PR 只给 read/write（不给通配，否则会命中 negotiation.review:approve）
# 主管额外拿审核权；财务按 PRD「只读查看」
_MATRIX: dict[str, list[str]] = {
    "pr": ["negotiation:read", "negotiation:write"],
    "pr_manager": ["negotiation:read", "negotiation:write", "negotiation.review:approve"],
    "finance": ["negotiation:read"],
    "operations": ["negotiation:read"],
}


def _log(msg: str) -> None:
    print(f"[048] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "negotiation",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("goods_main_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cooperation_mode", sa.String(8), nullable=False),
        sa.Column(
            "platform", sa.String(16), nullable=False, server_default=sa.text("'小红书'")
        ),
        sa.Column("scheduled_publish_date", sa.Date(), nullable=True),
        sa.Column(
            "quote_amount", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default=sa.text("'草稿'")),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_opinion", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["goods_main_id"], ["goods_main.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="RESTRICT"),
        # 推广单软停用后谈款单的审批记录仍要留着，所以 SET NULL 而不是 RESTRICT
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status IN ('草稿', '待审核', '审核通过', '审核驳回')",
            name="ck_negotiation_status",
        ),
        sa.CheckConstraint(
            "cooperation_mode IN ('寄拍', '送拍', '置换')",
            name="ck_negotiation_cooperation_mode",
        ),
        sa.CheckConstraint("quote_amount >= 0", name="ck_negotiation_quote_amount_nonneg"),
        # 审核通过必须有推广单，没通过不该有 —— 这两个状态必须同步，
        # 否则就是「审核通过但单子没建出来」的数据异常
        sa.CheckConstraint(
            "(status = '审核通过' AND promotion_id IS NOT NULL)"
            " OR (status <> '审核通过' AND promotion_id IS NULL)",
            name="ck_negotiation_approved_has_promotion",
        ),
    )
    op.create_index("idx_negotiation_tenant_status", "negotiation", ["tenant_id", "status"])
    op.create_index("idx_negotiation_pr", "negotiation", ["tenant_id", "pr_id", "status"])
    op.create_index("idx_negotiation_blogger", "negotiation", ["tenant_id", "blogger_id"])
    op.create_index("idx_negotiation_style", "negotiation", ["tenant_id", "style_id"])
    op.create_index(
        "idx_negotiation_promotion",
        "negotiation",
        ["promotion_id"],
        postgresql_where=sa.text("promotion_id IS NOT NULL"),
    )
    op.execute(enable_rls_sql("negotiation"))
    _log("negotiation 表已创建（含 RLS 与 5 个索引）")

    for scope, name in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )

    granted = 0
    for role_code, scopes in _MATRIX.items():
        for scope in scopes:
            res = bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )
            granted += res.rowcount or 0
    _log(f"权限入册 {len(_PERMISSIONS)} 项，角色授权 {granted} 条")
    for role_code, scopes in _MATRIX.items():
        _log(f"  - {role_code}: {', '.join(scopes)}")


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [s for s, _ in _PERMISSIONS]
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
    op.execute(disable_rls_sql("negotiation"))
    op.drop_table("negotiation")
