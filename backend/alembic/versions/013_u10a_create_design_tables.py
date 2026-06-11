"""U10a - 创建设计制版表（style_fabric / style_pattern / style_craft /
design_workflow_log）+ sku.tag_price 列 + design.* scope seed

Revision ID: 013_u10a_create_design_tables
Revises: 012_u09_seed_field_permissions
Create Date: 2026-06-07

4 张表（均 TenantScopedModel → tenant_id + RLS）：
- style_fabric / style_pattern / style_craft：1:1 with style（UNIQUE(style_id)），FK CASCADE
- design_workflow_log：N:1 with style，idx(tenant_id, style_id, created_at)
+ ALTER sku ADD COLUMN tag_price NUMERIC(10,2)（U10a 吊牌价，nullable，ck ≥0）
+ design.* 细分 scope seed（绑角色，幂等）
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "013_u10a_create_design_tables"
down_revision: str | Sequence[str] | None = "012_u09_seed_field_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    _upgrade_tables()
    _alter_sku_tag_price()
    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.drop_column("sku", "tag_price")
    _downgrade_tables()


def _upgrade_tables() -> None:
    op.create_table(
        "style_fabric",
        *_ts_columns(),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fabrics", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("accessories", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_completed", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_style_fabric_tenant"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE",
                                name="fk_style_fabric_style"),
    )
    op.create_index("uq_style_fabric_style", "style_fabric", ["style_id"], unique=True)

    op.create_table(
        "style_pattern",
        *_ts_columns(),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pattern_no", sa.String(64), nullable=True),
        sa.Column("pattern_file_key", sa.String(512), nullable=True),
        sa.Column("grading_data", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_style_pattern_tenant"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE",
                                name="fk_style_pattern_style"),
    )
    op.create_index("uq_style_pattern_style", "style_pattern", ["style_id"], unique=True)

    op.create_table(
        "style_craft",
        *_ts_columns(),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("craft_info", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_style_craft_tenant"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE",
                                name="fk_style_craft_style"),
    )
    op.create_index("uq_style_craft_style", "style_craft", ["style_id"], unique=True)

    op.create_table(
        "design_workflow_log",
        *_ts_columns(),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("driven_by", sa.String(32), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_design_wf_log_tenant"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="CASCADE",
                                name="fk_design_wf_log_style"),
    )
    op.create_index("idx_design_wf_log_style", "design_workflow_log",
                    ["tenant_id", "style_id", "created_at"])

    for tbl in ("style_fabric", "style_pattern", "style_craft", "design_workflow_log"):
        op.execute(enable_rls_sql(tbl))


def _alter_sku_tag_price() -> None:
    op.add_column("sku", sa.Column("tag_price", sa.Numeric(10, 2), nullable=True))
    op.create_check_constraint(
        "ck_sku_tag_price_nonneg", "sku", "tag_price IS NULL OR tag_price >= 0"
    )


def _downgrade_tables() -> None:
    for tbl in ("design_workflow_log", "style_craft", "style_pattern", "style_fabric"):
        op.execute(disable_rls_sql(tbl))
    op.drop_table("design_workflow_log")
    op.drop_table("style_craft")
    op.drop_table("style_pattern")
    op.drop_table("style_fabric")


# ---------------------------------------------------------------------------
# permission seed
# ---------------------------------------------------------------------------

_PERMISSIONS = [
    ("design.design:read", "查看设计制版", "function"),
    ("design.design:write", "创建/提交设计", "function"),
    ("design.pattern:read", "查看版型", "function"),
    ("design.pattern:write", "提交版型/放码", "function"),
    ("design.craft:write", "录入工艺", "function"),
    ("design.costing:write", "填写核价信息", "function"),
    ("design.tag_price:write", "填写吊牌价", "function"),
    ("design.confirm_price:approve", "价格确认转大货", "function"),
]

_MATRIX = {
    "designer": ["design.design:read", "design.design:write"],
    "pattern_maker": ["design.design:read", "design.pattern:read", "design.pattern:write"],
    "merchandiser": ["design.design:read", "design.craft:write",
                     "design.tag_price:write", "design.confirm_price:approve"],
    "design_assistant": ["design.design:read", "design.costing:write"],
    "operations": ["design.design:read"],
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
