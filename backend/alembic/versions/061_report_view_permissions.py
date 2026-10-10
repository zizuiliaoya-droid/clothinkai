"""7e R1：报表视图权限（报表视图设计第 4 节）。

- 新 scope 4 个：``report.work_progress:read_own``、``report.publish_progress:read_own``、
  ``report.bi:read``、``report.bi:read_own``。「只看本人」的 action 叫 ``read_own``：主管 / 运营的
  ``report.*:read`` 捞不到它；BI 从 ``report.production:read`` 拆出来，``report.*:read`` 仍覆盖。
- 角色授权（照设计的表，全部 ``ON CONFLICT DO NOTHING``）：pr ← 两个 ``read_own`` + ``bi:read_own``；
  finance ← ``work_progress:read``、``export:read``、``bi:read``；operations ← ``store_daily:write``、
  ``summary:refresh``；pr_manager ← ``summary:refresh``。
- 收回 pr 的 ``report.publish_progress:read``（发文进度也只看本人）。

``_PREEXISTING_GRANTS`` 是本迁移之前就在库里的 report 授权（来源见常量注释）。上行碰到它们是空操作，
下行**不撤**：operations 的 ``report.store_daily:write`` 由 018 授，回到 060 时它应当还在。

下行：删 4 个新 scope 上的个人授权（``user_permission_override`` → ``permission`` 是 RESTRICT）→
撤回本迁移真正新增的角色授权 → 删 4 个 scope → 补回 pr 的 ``report.publish_progress:read``。

Revision ID: 061_report_view_permissions
Revises: 060_promotion_shipping
Create Date: 2026-10-10
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.report.advanced_permissions import REPORT_VIEW_PERMISSIONS

revision: str = "061_report_view_permissions"
down_revision: str | Sequence[str] | None = "060_promotion_shipping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 打印前缀。改迁移编号时与 revision、docstring 一起改
_LOG_PREFIX = "061"

_NEW_SCOPES: list[tuple[str, str]] = [(f"{s}:{a}", name) for s, a, name in REPORT_VIEW_PERMISSIONS]

# 设计第 4 节的授权表：(角色, scope)
_MATRIX: list[tuple[str, str]] = [
    ("pr", "report.work_progress:read_own"),
    ("pr", "report.publish_progress:read_own"),
    ("pr", "report.bi:read_own"),
    ("finance", "report.work_progress:read"),
    ("finance", "report.export:read"),
    ("finance", "report.bi:read"),
    ("operations", "report.store_daily:write"),
    ("operations", "report.summary:refresh"),
    ("pr_manager", "report.summary:refresh"),
]

# 本迁移之前就在库里的 report 授权：下行不撤。DEFAULT_ROLES 同步写明（代码追平生产）
_PREEXISTING_GRANTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("operations", "report.store_daily:write"),  # 018
        ("pr_manager", "report.export:read"),  # 021
        ("operations", "report.export:read"),  # 021
        ("admin", "report.summary:refresh"),  # 052
    }
)

_REVOKED_PR_SCOPE = "report.publish_progress:read"


def _log(msg: str) -> None:
    print(f"[{_LOG_PREFIX}] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    for scope, name in _NEW_SCOPES:
        res = bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"scope": scope, "name": name},
        )
        _log(f"权限 {scope} 入册 {res.rowcount or 0} 条")

    for role_code, scope in _MATRIX:
        res = bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT gen_random_uuid(), r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"role_code": role_code, "scope": scope},
        )
        _log(f"授权 {role_code} ← {scope} {res.rowcount or 0} 条")

    revoked = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE "
            "role_id IN (SELECT id FROM role WHERE code = 'pr') AND "
            "permission_id IN (SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _REVOKED_PR_SCOPE},
    )
    _log(f"收回 pr 的 {_REVOKED_PR_SCOPE} {revoked.rowcount or 0} 条")


def downgrade() -> None:
    bind = op.get_bind()
    new_scopes = [s for s, _ in _NEW_SCOPES]

    overrides = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": new_scopes},
    )
    _log(f"删除 {len(new_scopes)} 个新 scope 上的个人授权 {overrides.rowcount or 0} 条")

    for role_code, scope in _MATRIX:
        if (role_code, scope) in _PREEXISTING_GRANTS:
            _log(f"保留 {role_code} ← {scope}（本迁移之前已有）")
            continue
        res = bind.execute(
            sa.text(
                "DELETE FROM role_permission WHERE "
                "role_id IN (SELECT id FROM role WHERE code = :role_code) AND "
                "permission_id IN (SELECT id FROM permission WHERE scope = :scope)"
            ),
            {"role_code": role_code, "scope": scope},
        )
        _log(f"撤回 {role_code} ← {scope} {res.rowcount or 0} 条")

    # 新 scope 上矩阵外的角色授权（上线后手工授的）也随 scope 一起走
    others = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": new_scopes},
    )
    _log(f"删除新 scope 上其余角色授权 {others.rowcount or 0} 条")
    removed = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": new_scopes},
    )
    _log(f"删除 scope {removed.rowcount or 0} 个")

    restored = bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT gen_random_uuid(), r.id, p.id FROM role r, permission p "
            "WHERE r.code = 'pr' AND p.scope = :scope "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"scope": _REVOKED_PR_SCOPE},
    )
    _log(f"补回 pr 的 {_REVOKED_PR_SCOPE} {restored.rowcount or 0} 条")
