"""U11 - blogger 增加 audience_profile JSONB 列 + blogger.tag:recompute scope seed

Revision ID: 015_u11_add_audience_profile
Revises: 014_u10b_create_platform_product
Create Date: 2026-06-09

仅 ALTER ADD COLUMN（nullable，不锁表无回填）+ seed 1 个权限 scope。
audience_profile 由 U13 采集 Worker 写入，U11 仅读展示 read_like_ratio。
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_u11_add_audience_profile"
down_revision: str | Sequence[str] | None = "014_u10b_create_platform_product"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blogger",
        sa.Column("audience_profile", postgresql.JSONB(), nullable=True),
    )
    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.drop_column("blogger", "audience_profile")


_PERMISSIONS = [
    ("blogger.tag:recompute", "重算博主智能标签", "function"),
]

# admin 已持 "*" 通配；显式绑定保证可单独授予/可发现
_MATRIX = {
    "admin": ["blogger.tag:recompute"],
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
