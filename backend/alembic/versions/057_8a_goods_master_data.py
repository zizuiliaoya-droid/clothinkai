"""8a 商品资料线：运营可维护商品资料、商品资料导入的来源级权限（及后续 8a 各项的数据变更）。

## 为什么
业务方 10-07 定：跟单与运营都能改商品资料，靠审计留痕；运营可看可改成本价；商品资料导入
从 PR / 主管收回，交给跟单与运营（设计 §10、§4.6，补充二 Q4 / Q5）。

## 本迁移按区段组织
``upgrade()`` 依次调 ``_upgrade_style`` → ``_upgrade_import_tables`` → ``_upgrade_permissions``
→ ``_report``；``downgrade()`` 依次调 ``_downgrade_permissions`` → ``_downgrade_import_tables``
→ ``_downgrade_style``。各区段随 8a 的各项改动分步填写（设计 §24.1）：

- 权限（8a-7）：入册 ``product.import:write``（商品资料导入、映射、冲突裁决只认这一个 scope，
  跟单 / 运营靠 ``product.*:*``、管理员靠 ``*`` 命中；PR / 主管的角色数据不动，因此收回）；
  给 ``operations`` 授 ``product.*:*``（``permission`` 里已有这一行，跟单在用，仍兜底入册）
- 款式表（8a-1 / 8a-4）、导入表（8a-6）：见对应函数的说明

## 不删数据、不清汇总覆盖
upgrade 方向只加表、加列、放宽约束、插权限行：不删任何数据、不删列、不改任何外键；
汇总表的列与口径不变，**不清 ``report_summary_coverage``**（设计 §3.4）。
downgrade 撤回授权时只删运营那一条 ``product.*:*`` 的 ``role_permission``（``permission``
里的 ``product.*:*`` 跟单在用，不删）；删 ``product.import:write`` 之前先删引用它的
``user_permission_override``（外键 ``RESTRICT``，不先删会让 downgrade 中止；迁移用
BYPASSRLS 角色连库，FORCE RLS 挡不住这条 DELETE）。

Revision ID: 057_8a_goods_master_data
Revises: 056_goods_short_name
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "057_8a_goods_master_data"
down_revision: str | Sequence[str] | None = "056_goods_short_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 新入册的商品资料导入权限
_IMPORT_SCOPE = "product.import:write"
_IMPORT_SCOPE_NAME = "导入商品资料、配置其字段映射、处理其导入冲突"
# 运营获得与跟单相同的商品权限（兜底入册：库里本来就有，跟单在用）
_PRODUCT_ALL_SCOPE = "product.*:*"
_PRODUCT_ALL_NAME = "商品模块全部权限"
_GRANT_ROLE = "operations"


def _log(msg: str) -> None:
    print(f"[057] {msg}")


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def _upgrade_style(bind: Connection) -> None:
    """款式表：``style.category`` 放开 NOT NULL（8a-1）、加 ``external_image_url``（8a-4）。

    由后续步骤填写；本步骤（8a-7）不改款式表。
    """


def _upgrade_import_tables(bind: Connection) -> None:
    """导入表：``import_batch`` 计数列、``import_job`` 状态与备注、``import_conflict`` 新表（8a-6）。

    由后续步骤填写；本步骤（8a-7）不改导入表。
    """


def _upgrade_permissions(bind: Connection) -> None:
    """入册 ``product.import:write``；给 operations 授 ``product.*:*``。幂等。"""
    for scope, name in (
        (_IMPORT_SCOPE, _IMPORT_SCOPE_NAME),
        (_PRODUCT_ALL_SCOPE, _PRODUCT_ALL_NAME),
    ):
        res = bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
        _log(f"权限 {scope}：{'新入册' if res.rowcount else '已存在，跳过'}")

    res = bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT :id, r.id, p.id FROM role r, permission p "
            "WHERE r.code = :role_code AND p.scope = :scope "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"id": str(uuid4()), "role_code": _GRANT_ROLE, "scope": _PRODUCT_ALL_SCOPE},
    )
    _log(f"授予 {_GRANT_ROLE} 角色 {_PRODUCT_ALL_SCOPE}：{res.rowcount or 0} 项")


def _report(bind: Connection) -> None:
    """打印迁移后的权限状态（同 043 末尾）；后续步骤在这里追加各自的统计。"""
    scopes = (
        bind.execute(
            sa.text(
                "SELECT p.scope FROM role r "
                "JOIN role_permission rp ON rp.role_id = r.id "
                "JOIN permission p ON p.id = rp.permission_id "
                "WHERE r.code = :role_code AND p.scope LIKE 'product%' "
                "ORDER BY p.scope"
            ),
            {"role_code": _GRANT_ROLE},
        )
        .scalars()
        .all()
    )
    _log(f"{_GRANT_ROLE} 持有的 product 相关权限：{', '.join(scopes) or '（无）'}")

    holders = bind.execute(
        sa.text(
            """
            SELECT r.code, COUNT(u.id) AS users
            FROM role r
            JOIN role_permission rp ON rp.role_id = r.id
            JOIN permission p ON p.id = rp.permission_id
            LEFT JOIN user_role ur ON ur.role_id = r.id
            LEFT JOIN "user" u ON u.id = ur.user_id AND u.deleted_at IS NULL
            WHERE p.scope = :scope
            GROUP BY r.code
            ORDER BY r.code
            """
        ),
        {"scope": _PRODUCT_ALL_SCOPE},
    ).all()
    _log(f"持有 {_PRODUCT_ALL_SCOPE} 的角色与人数：")
    for code, users in holders:
        _log(f"  - {code}: {users} 人")


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_style(bind)
    _upgrade_import_tables(bind)
    _upgrade_permissions(bind)
    _report(bind)


# ---------------------------------------------------------------------------
# downgrade
# ---------------------------------------------------------------------------


def _downgrade_permissions(bind: Connection) -> None:
    """撤回运营的 ``product.*:*``；删除 ``product.import:write``（先删引用它的个人权限覆盖）。"""
    res = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE "
            "role_id IN (SELECT id FROM role WHERE code = :role_code) AND "
            "permission_id IN (SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"role_code": _GRANT_ROLE, "scope": _PRODUCT_ALL_SCOPE},
    )
    _log(f"撤回 {_GRANT_ROLE} 角色 {_PRODUCT_ALL_SCOPE}：{res.rowcount or 0} 项")

    # user_permission_override.permission_id 是 ON DELETE RESTRICT：不先删会挡住下面删 permission
    res = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除 {res.rowcount or 0} 条个人权限覆盖（{_IMPORT_SCOPE}）")

    res = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除 {_IMPORT_SCOPE} 的角色授权 {res.rowcount or 0} 项")

    res = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = :scope"),
        {"scope": _IMPORT_SCOPE},
    )
    _log(f"删除权限 {_IMPORT_SCOPE}：{res.rowcount or 0} 项")


def _downgrade_import_tables(bind: Connection) -> None:
    """撤回导入表的变更（8a-6）。由后续步骤填写。"""


def _downgrade_style(bind: Connection) -> None:
    """撤回款式表的变更（8a-1 / 8a-4）。由后续步骤填写。"""


def downgrade() -> None:
    bind = op.get_bind()
    _downgrade_permissions(bind)
    _downgrade_import_tables(bind)
    _downgrade_style(bind)
