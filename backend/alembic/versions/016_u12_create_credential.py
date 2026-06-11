"""U12 - 创建 credential 表 + credential:read/write/delete scope seed

Revision ID: 016_u12_create_credential
Revises: 015_u11_add_audience_profile
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "016_u12_create_credential"
down_revision: str | Sequence[str] | None = "015_u11_add_audience_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'paused'")),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_credential_tenant"),
        sa.CheckConstraint("status IN ('active','paused','disabled')",
                           name="ck_credential_status"),
        sa.CheckConstraint("consecutive_failures >= 0",
                           name="ck_credential_failures_nonneg"),
    )
    op.create_index(
        "uq_credential_tenant_plat_user",
        "credential", ["tenant_id", "platform", "username"], unique=True,
    )
    op.create_index(
        "idx_credential_tenant_status",
        "credential", ["tenant_id", "status"],
    )
    op.execute(enable_rls_sql("credential"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("credential"))
    op.drop_table("credential")


_PERMISSIONS = [
    ("credential:read", "查看平台凭据（不含密码）", "function"),
    ("credential:write", "创建/编辑/暂停/恢复平台凭据", "function"),
    ("credential:delete", "删除平台凭据", "function"),
]

_MATRIX = {
    "admin": ["credential:read", "credential:write", "credential:delete"],
    "operations": ["credential:read"],
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
