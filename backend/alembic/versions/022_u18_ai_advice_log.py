"""U18 - 创建 ai_advice_log 表 + ai.advice scope seed

Revision ID: 022_u18_ai_advice_log
Revises: 021_u17_bundle_bi_export
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "022_u18_ai_advice_log"
down_revision: str | Sequence[str] | None = "021_u17_bundle_bi_export"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_advice_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("advice_type", sa.String(16), nullable=False),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(8), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("model", sa.String(32), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_ai_advice_log_tenant_type", "ai_advice_log",
                    ["tenant_id", "advice_type", "created_at"])

    op.execute(enable_rls_sql("ai_advice_log"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("ai_advice_log"))
    op.drop_table("ai_advice_log")


_PERMISSIONS = [
    ("ai.advice:read", "调用 AI 决策建议", "function"),
]

_MATRIX = {
    "pr": ["ai.advice:read"],
    "pr_manager": ["ai.advice:read"],
    "operations": ["ai.advice:read"],
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
