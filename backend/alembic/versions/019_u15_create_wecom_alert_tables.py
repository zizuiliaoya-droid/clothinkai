"""U15 - 创建 wecom_alert_config + wecom_alert_log 表 + wecom.alert_config scope seed

Revision ID: 019_u15_wecom_alert_tables
Revises: 018_u14_create_report_tables
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "019_u15_wecom_alert_tables"
down_revision: str | Sequence[str] | None = "018_u14_create_report_tables"
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
        "wecom_alert_config",
        *_base_cols(),
        sa.Column("control_group_webhook", sa.Text(), nullable=True),
        sa.Column("return_rate_threshold", sa.Numeric(5, 4), nullable=False,
                  server_default=sa.text("0.4000")),
        sa.Column("low_roi_threshold", sa.Numeric(8, 4), nullable=True),
        sa.Column("low_conversion_threshold", sa.Numeric(5, 4), nullable=True),
        sa.Column("alert_recipients", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    op.create_index("uq_wecom_alert_config_tenant", "wecom_alert_config",
                    ["tenant_id"], unique=True)

    op.create_table(
        "wecom_alert_log",
        *_base_cols(),
        sa.Column("alert_type", sa.String(24), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=True),
        sa.Column("entity_ref", sa.String(64), nullable=True),
        sa.Column("period_key", sa.String(10), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("uq_wecom_alert_log", "wecom_alert_log",
                    ["tenant_id", "alert_type", "entity_ref", "period_key"],
                    unique=True)
    op.create_index("idx_wecom_alert_log_fired", "wecom_alert_log",
                    ["tenant_id", "fired_at"])

    op.execute(enable_rls_sql("wecom_alert_config"))
    op.execute(enable_rls_sql("wecom_alert_log"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("wecom_alert_log"))
    op.execute(disable_rls_sql("wecom_alert_config"))
    op.drop_table("wecom_alert_log")
    op.drop_table("wecom_alert_config")


_PERMISSIONS = [
    ("wecom.alert_config:read", "查看企微预警配置", "function"),
    ("wecom.alert_config:write", "编辑企微预警配置", "function"),
]

_MATRIX = {
    "operations": ["wecom.alert_config:read", "wecom.alert_config:write"],
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
