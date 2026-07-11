"""修复财务结款权限：settlement 模块 API 用的是 settlement:* / settlement.review:approve /
settlement.pay:upload_proof 作用域，但种子只授予了 finance.settlement:*，二者从未匹配，
导致除 admin(通配*)外无人能读/操作结款单（财务账号列表 403、页面空白）。

本迁移幂等地：
- 补建 4 个真实作用域 permission：settlement:read / settlement:write /
  settlement.review:approve / settlement.pay:upload_proof
- 授予 finance：以上 4 个（小团队财务账号需操作整条结款流程）
- 授予 pr_manager：settlement:read / settlement:write / settlement.review:approve

Revision ID: 029_settlement_perms
Revises: 028_blogger_level
Create Date: 2026-07-11
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "029_settlement_perms"
down_revision: str | Sequence[str] | None = "028_blogger_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMS: list[tuple[str, str]] = [
    ("settlement:read", "结款单读取"),
    ("settlement:write", "结款单写入(填付款金额/增加结算项)"),
    ("settlement.review:approve", "结款核查/驳回"),
    ("settlement.pay:upload_proof", "财务上传付款凭证"),
]

_GRANTS: dict[str, list[str]] = {
    "finance": [
        "settlement:read",
        "settlement:write",
        "settlement.review:approve",
        "settlement.pay:upload_proof",
    ],
    "pr_manager": [
        "settlement:read",
        "settlement:write",
        "settlement.review:approve",
    ],
}


def upgrade() -> None:
    bind = op.get_bind()

    for scope, name in _PERMS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )

    for role_code, scopes in _GRANTS.items():
        for scope in scopes:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [s for s, _ in _PERMS]
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
