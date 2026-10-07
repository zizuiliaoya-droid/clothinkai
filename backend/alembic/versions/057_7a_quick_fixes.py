"""7a 快修：款式下拉读权限 ``product.style:read`` + 驳回后重新提交的两列。

## 为什么（7a-1）

PR 新建推广、谈款、录入信息要选款式 / 归属商品 / 颜色及规格，这三个下拉调的
``GET /api/styles/``、``GET /api/styles/{style_id}/goods``、``GET /api/skus/by-style/{style_id}``
原来挂 ``product:read``，PR 与 PR 主管一律 403（颜色及规格的下拉不能手输，PR 根本填不了）。
现在这三个接口改挂新 scope ``product.style:read``，只授给 pr、pr_manager。

## 现在谁能读款式列表（改前 = 改后，只多了 PR 两个角色）

| scope | 持有角色 |
|---|---|
| ``*`` | admin、platform_admin |
| ``product.*:*`` | merchandiser |
| ``product.*:read`` | designer、design_assistant、operations |
| ``product:read``（精确） | 无（从没入册过）—— 下面仍镜像授权一次，预期 0 行 |
| ``product.style:read``（新） | pr、pr_manager |
| 无 | pattern_maker、finance、warehouse（仍 403） |

``has("product","read")`` 与 ``has("product.style","read")`` 只差在「精确持有 ``product:read``」的人，
所以镜像那一步保证没有任何角色因为改挂而丢权限。

## 为什么不给 PR ``product.*:read``

那会连带放开成本表 ``/api/skus/``、字典 ``/api/dict-items``、商品 / 套装等整个 product 域。
SKU 接口里的成本价 / 采购价仍由字段级权限屏蔽，与本迁移无关。

## 生效时间

权限有 Redis 缓存（``PERM_CACHE_TTL_SECONDS=300``），已登录的 PR 最多 5 分钟后生效。

## 7a-4 驳回后重新提交

``promotion`` 加 ``resubmit_note``（Text）与 ``resubmitted_at``（timestamptz），都可空、不回填，
已有单据不受影响。只留最近一轮（每次重提覆盖），完整历史随 7d-1 时间线做。downgrade 删这两列
会丢掉已写入的重提说明——这是新功能自己产生的数据，回退版本时本来就用不上。

Revision ID: 057_7a_quick_fixes
Revises: 056_goods_short_name
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "057_7a_quick_fixes"
down_revision: str | Sequence[str] | None = "056_goods_short_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STYLE_READ = "product.style:read"
_STYLE_READ_NAME = "查看款式列表与款式下的商品 / 颜色尺码（下拉用）"
_GRANT_ROLES: tuple[str, ...] = ("pr", "pr_manager")
# 改挂前能读款式列表的 scope（has() 的命中规则：*、精确、第一段通配）
_LEGACY_READERS: tuple[str, ...] = ("*", "product.*:*", "product.*:read", "product:read")


def _log(msg: str) -> None:
    print(f"[057] {msg}")


def _holders(bind: sa.engine.Connection, scopes: Sequence[str]) -> list[tuple[str, str]]:
    rows = bind.execute(
        sa.text(
            "SELECT p.scope, r.code FROM role_permission rp "
            "JOIN role r ON r.id = rp.role_id "
            "JOIN permission p ON p.id = rp.permission_id "
            "WHERE p.scope = ANY(:scopes) ORDER BY p.scope, r.code"
        ),
        {"scopes": list(scopes)},
    ).all()
    return [(row[0], row[1]) for row in rows]


def upgrade() -> None:
    bind = op.get_bind()

    # ① 改挂前谁能读款式列表
    for scope, code in _holders(bind, _LEGACY_READERS):
        _log(f"改挂前 {scope}: {code}")

    # ② 入册新 scope
    bind.execute(
        sa.text(
            "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
            "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
            "ON CONFLICT (scope) DO NOTHING"
        ),
        {"id": str(uuid4()), "scope": _STYLE_READ, "name": _STYLE_READ_NAME},
    )

    # ③ 授给 PR 与 PR 主管
    granted = 0
    for role_code in _GRANT_ROLES:
        res = bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT :id, r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"id": str(uuid4()), "role_code": role_code, "scope": _STYLE_READ},
        )
        granted += res.rowcount or 0
    _log(f"授权 {', '.join(_GRANT_ROLES)}：{granted} 条")

    # ④ 精确持有 product:read 的角色镜像授权，保证没人因改挂丢权限（预期 0 行）
    res = bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT gen_random_uuid(), rp.role_id, ps.id "
            "FROM role_permission rp "
            "JOIN permission p ON p.id = rp.permission_id AND p.scope = 'product:read' "
            "CROSS JOIN permission ps "
            "WHERE ps.scope = :scope "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"scope": _STYLE_READ},
    )
    _log(f"product:read 镜像授权：{res.rowcount or 0} 条（预期 0）")

    # ⑤ 改挂后持有新 scope 的角色
    for scope, code in _holders(bind, (_STYLE_READ,)):
        _log(f"改挂后 {scope}: {code}")

    # 7a-4 驳回后重新提交：两列可空、不回填，已有单据不受影响
    op.add_column("promotion", sa.Column("resubmit_note", sa.Text(), nullable=True))
    op.add_column(
        "promotion", sa.Column("resubmitted_at", sa.DateTime(timezone=True), nullable=True)
    )
    _log("promotion 加列 resubmit_note、resubmitted_at")


def downgrade() -> None:
    op.drop_column("promotion", "resubmitted_at")
    op.drop_column("promotion", "resubmit_note")

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _STYLE_READ},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE scope = :scope"), {"scope": _STYLE_READ})
