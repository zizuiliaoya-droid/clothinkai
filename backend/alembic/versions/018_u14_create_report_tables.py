"""U14 - 创建 target_planning + store_daily 表 + report.* scope seed

Revision ID: 018_u14_create_report_tables
Revises: 017_u13_create_crawler_tables
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "018_u14_create_report_tables"
down_revision: str | Sequence[str] | None = "017_u13_create_crawler_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_cols() -> list:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    op.create_table(
        "target_planning",
        *_base_cols(),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_month", sa.String(7), nullable=False),
        sa.Column("min_target", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="RESTRICT",
                                name="fk_target_planning_pr"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE",
                                name="fk_target_planning_style"),
        sa.CheckConstraint("min_target >= 0", name="ck_target_planning_min"),
    )
    op.create_index("uq_target_planning", "target_planning",
                    ["tenant_id", "pr_id", "style_id", "period_month"], unique=True)
    op.create_index("idx_target_planning_month", "target_planning",
                    ["tenant_id", "period_month"])

    op.create_table(
        "store_daily",
        *_base_cols(),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ad_spend_total", sa.Numeric(12, 2), nullable=True),
        sa.Column("zhitongche_spend", sa.Numeric(12, 2), nullable=True),
        sa.Column("yinli_spend", sa.Numeric(12, 2), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
    )
    op.create_index("uq_store_daily_date", "store_daily",
                    ["tenant_id", "date"], unique=True)

    op.execute(enable_rls_sql("target_planning"))
    op.execute(enable_rls_sql("store_daily"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("store_daily"))
    op.execute(disable_rls_sql("target_planning"))
    op.drop_table("store_daily")
    op.drop_table("target_planning")


_PERMISSIONS = [
    ("report.work_progress:read", "查看工作进度表", "function"),
    ("report.production:read", "查看投产报表", "function"),
    ("report.target:read", "查看爆款约篇目标", "function"),
    ("report.target:write", "设置爆款约篇目标", "function"),
    ("report.store_daily:read", "查看店铺数据看板", "function"),
    ("report.store_daily:write", "编辑店铺数据手动字段", "function"),
]

_MATRIX = {
    "pr_manager": ["report.target:write"],
    "operations": ["report.store_daily:write"],
}


def _seed_permissions() -> None:
    bind = op.get_bind()
    for scope, name, category in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, :category, NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name, "category": category},
        )
    for role_code, scope_list in _MATRIX.items():
        for scope in scope_list:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )


def _downgrade_permissions() -> None:
    bind = op.get_bind()
    scopes = [s for s, _, _ in _PERMISSIONS]
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
