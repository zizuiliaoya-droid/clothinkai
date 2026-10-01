"""商品管理权限入册，顺手清掉 product.platform 死权限。

两件事：

1. **清 ``product.platform:read/write``**。平台链接端点在 043 已改用 ``ops.platform_link``，
   这两条 scope 从此没有任何代码检查，留在权限清单里只会让人以为它还管事
   （``operations`` 持 read、``merchandiser`` 持 write）。

2. **注册 ``product.goods:read/write``**。只写 permission 行，不绑角色 ——
   ``has()`` 的前缀通配取 scope 第一段，跟单的 ``product.*:*`` 与
   运营/设计的 ``product.*:read`` 已经覆盖，再绑一次是重复数据。
   入册的目的是让管理员能在权限管理界面单独授予（例如只给 PR 看商品）。

与 043 的平台链接刻意相反：那组必须躲开 ``product.*`` 才挡得住业务角色；
商品是产品主数据，跟单本来就该维护、运营本来就该看，落在 ``product.*`` 下正好。

Revision ID: 044_goods_perm
Revises: 043_ops_platform_link
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "044_goods_perm"
down_revision: str | Sequence[str] | None = "043_ops_platform_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEAD_SCOPES: list[str] = ["product.platform:read", "product.platform:write"]

_NEW_PERMS: list[tuple[str, str]] = [
    ("product.goods:read", "查询商品 / 套装"),
    ("product.goods:write", "创建 / 编辑 / 删除商品与套装成员"),
]

# downgrade 要把死权限连角色绑定一起还原，否则回滚后权限清单与 043 之前不一致。
_DEAD_GRANTS: list[tuple[str, str, str]] = [
    ("operations", "product.platform:read", "查询平台商品映射"),
    ("merchandiser", "product.platform:write", "维护平台商品映射"),
]


def _log(msg: str) -> None:
    print(f"[044] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    for scope, name in _NEW_PERMS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
    _log(f"商品权限入册 {len(_NEW_PERMS)} 项（不绑角色，靠 product.*:* / product.*:read 通配生效）")

    holders = bind.execute(
        sa.text(
            """
            SELECT r.code, p.scope
            FROM role_permission rp
            JOIN role r ON r.id = rp.role_id
            JOIN permission p ON p.id = rp.permission_id
            WHERE p.scope = ANY(:scopes)
            ORDER BY r.code, p.scope
            """
        ),
        {"scopes": _DEAD_SCOPES},
    ).all()
    for code, scope in holders:
        _log(f"  解除绑定：{code} → {scope}")

    unbound = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": _DEAD_SCOPES},
    )
    dropped = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": _DEAD_SCOPES},
    )
    _log(
        f"清理死权限：解绑 {unbound.rowcount or 0} 条，删除 permission {dropped.rowcount or 0} 条"
        "（platform 链接端点自 043 起走 ops.platform_link）"
    )


def downgrade() -> None:
    bind = op.get_bind()

    for role_code, scope, name in _DEAD_GRANTS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT :id, r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"id": str(uuid4()), "role_code": role_code, "scope": scope},
        )

    scopes = [s for s, _ in _NEW_PERMS]
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
