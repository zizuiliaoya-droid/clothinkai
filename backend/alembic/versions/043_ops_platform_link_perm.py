"""平台链接改用 ops.platform_link 权限，授给运营角色。

平台链接（千牛商品ID / 万相台主体ID 与商品、渠道的绑定）是运维职责 —— 一条链接绑错款式，
整条销售数据就算到别的商品头上。业务人员不需要也不应该碰。

原来这组端点声明的是 ``product.platform``，但 ``EffectivePermissions.has`` 的前缀通配
只看 scope 第一段：跟单角色持 ``product.*:*``、运营与设计持 ``product.*:read``，全都能命中
``product.platform``，等于没设门槛。换成 ``ops.`` 开头后只有显式授权的角色和持 ``*``
的管理员能进。

授权范围：admin / platform_admin 通过 ``*`` 自动拥有；operations 显式授予读写。
其余角色（跟单、设计、PR、财务、仓库）一律没有。

Revision ID: 043_ops_platform_link
Revises: 042_promo_goods
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "043_ops_platform_link"
down_revision: str | Sequence[str] | None = "042_promo_goods"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMS: list[tuple[str, str]] = [
    ("ops.platform_link:read", "查看平台链接映射(运维)"),
    ("ops.platform_link:write", "维护平台链接与商品归属、渠道(运维)"),
]
_GRANT_ROLE = "operations"


def _log(msg: str) -> None:
    print(f"[043] {msg}")


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

    granted = 0
    for scope, _ in _PERMS:
        res = bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT :id, r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"id": str(uuid4()), "role_code": _GRANT_ROLE, "scope": scope},
        )
        granted += res.rowcount or 0

    _log(f"新增权限 {len(_PERMS)} 项，授予 {_GRANT_ROLE} 角色 {granted} 项")

    holders = bind.execute(
        sa.text(
            """
            SELECT r.code, COUNT(u.id) AS users
            FROM role r
            LEFT JOIN user_role ur ON ur.role_id = r.id
            LEFT JOIN "user" u ON u.id = ur.user_id
            WHERE r.code IN ('admin', 'platform_admin', :role_code)
            GROUP BY r.code
            ORDER BY r.code
            """
        ),
        {"role_code": _GRANT_ROLE},
    ).all()
    _log("可访问平台链接运维视图的角色与人数：")
    for code, users in holders:
        _log(f"  - {code}: {users} 人")


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
