"""新增「仓库(warehouse)」角色：仅能操作站外推广打单（读推广列表 + PATCH 回传发货单号）。

需求 §3 + 疑点6（客户红字确认："仓库账号只能操作打单，其他都做不了"）。

warehouse 角色权限最小化：
- promotion:read  —— 读推广列表（仓库打单页用 GET /api/promotions/）
- promotion:write —— PATCH /api/promotions/{id} 回传 source_extra['发货单号']

刻意不授予 promotion 的 delete / review:approve / 发布等，
也不授予其它任何模块，实现"只能打单"。前端另做菜单/路由限制。

Revision ID: 030_warehouse_role
Revises: 029_settlement_perms
Create Date: 2026-07-12
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "030_warehouse_role"
down_revision: str | Sequence[str] | None = "029_settlement_perms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = ("warehouse", "仓库", "仓库打单：仅能查看待打单推广单并回传发货单号")

_PERMS: list[tuple[str, str]] = [
    ("promotion:read", "推广单读取(仓库打单)"),
    ("promotion:write", "推广单写入(回传发货单号)"),
]

_GRANTS: list[str] = ["promotion:read", "promotion:write"]


def upgrade() -> None:
    bind = op.get_bind()

    code, name, description = _ROLE
    bind.execute(
        sa.text(
            "INSERT INTO role (id, code, name, description, is_system, created_at, updated_at) "
            "VALUES (:id, :code, :name, :description, true, NOW(), NOW()) "
            "ON CONFLICT (code) DO NOTHING"
        ),
        {"id": str(uuid4()), "code": code, "name": name, "description": description},
    )

    for scope, pname in _PERMS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": pname},
        )

    for scope in _GRANTS:
        bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT :id, r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"id": str(uuid4()), "role_code": _ROLE[0], "scope": scope},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE role_id IN "
            "(SELECT id FROM role WHERE code = :code)"
        ),
        {"code": _ROLE[0]},
    )
    bind.execute(sa.text("DELETE FROM role WHERE code = :code"), {"code": _ROLE[0]})
    scopes = [s for s, _ in _PERMS]
    bind.execute(
        sa.text(
            "DELETE FROM permission WHERE scope = ANY(:scopes) AND scope NOT IN "
            "(SELECT scope FROM permission p JOIN role_permission rp ON rp.permission_id = p.id)"
        ),
        {"scopes": scopes},
    )
