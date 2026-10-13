"""7e R7：平台指标配置表（报表视图设计 2.7 / 2.8 / 3.4）。

- 新表 ``promotion_platform_setting``：每租户每平台一行，``like_coefficient``（点赞折算系数，numeric(6,3)，
  CHECK > 0 且 <= 10）、``hit_threshold``（爆文阈值，原始点赞，CHECK 1 ~ 10000000）、``updated_by``（→ user SET NULL）；
  唯一 ``(tenant_id, platform)``；RLS ENABLE + FORCE。
- 每个现有租户插 5 行默认值：系数 = 原来写死在代码里的折算常量（小红书 1、抖音 0.1、快手 0.1、B站 1、得物 1），
  上线时折算点赞数字不变；阈值小红书 / 快手 / B站 / 得物 1000、抖音 10000。缺行时读取侧回落默认、不报错。
- 权限：``metric_config:read``（pr_manager、operations）、``metric_config:write``（pr_manager），管理员靠 ``*``。
  独立一级域：挂在 ``promotion.`` 下会被 PR 的 ``promotion.*:*`` 命中，挂在 ``report.`` 下会被 ``report.*:read`` 捞走。
- 工作进度爆文数从 >= 500 改按平台阈值，汇总口径变了：清空 ``report_summary_coverage``（全部租户），
  读取回退实时，下一次每小时刷新重写最近 31 天。

下行：先逐条打印将删的个人授权（用户名、scope、effect），删个人授权 / 角色授权 / scope，删表，清覆盖。
只回滚代码不跑 downgrade 时要另清一次覆盖（设计 3.4）。

Revision ID: 066_platform_metric_setting
Revises: 064_urge_complete
Create Date: 2026-10-13
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "066_platform_metric_setting"
# 流程线 PR-6 的 065 合并后由编排方改成 065_settlement_flow（brief-rep7e7 第 1 节）
down_revision: str | Sequence[str] | None = "064_urge_complete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移编号只出现在这里（打印前缀）、文件名、revision 与 test_migration_066_state.py
_REV = "066"

_TABLE = "promotion_platform_setting"
_APP_ROLE = "clothing_app"

# (平台, 点赞折算系数, 爆文阈值)。写死在迁移里：迁移是历史快照，不跟着代码里的默认值走
_DEFAULTS: list[tuple[str, str, int]] = [
    ("小红书", "1.000", 1000),
    ("抖音", "0.100", 10000),
    ("快手", "0.100", 1000),
    ("B站", "1.000", 1000),
    ("得物", "1.000", 1000),
]

_PERMISSIONS: list[tuple[str, str]] = [
    ("metric_config:read", "查看平台指标（爆文阈值 / 点赞折算系数）"),
    ("metric_config:write", "修改平台指标（爆文阈值 / 点赞折算系数）"),
]

_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["metric_config:read", "metric_config:write"],
    "operations": ["metric_config:read"],
}


def _log(msg: str) -> None:
    print(f"[{_REV}] {msg}")


def _create_table() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("like_coefficient", sa.Numeric(6, 3), nullable=False),
        sa.Column("hit_threshold", sa.Integer(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"], ondelete="SET NULL"),
        # 命名约定套前缀：实名 ck_promotion_platform_setting_like_coefficient / _hit_threshold
        sa.CheckConstraint(
            "like_coefficient > 0 AND like_coefficient <= 10", name="like_coefficient"
        ),
        sa.CheckConstraint(
            "hit_threshold >= 1 AND hit_threshold <= 10000000", name="hit_threshold"
        ),
    )
    op.create_index(
        "uq_promotion_platform_setting_tenant_platform",
        _TABLE,
        ["tenant_id", "platform"],
        unique=True,
    )
    op.execute(enable_rls_sql(_TABLE))
    # 生产以 clothing_bypass 跑迁移，025 的 DEFAULT PRIVILEGES 自动授；测试库与 CI 以 postgres 跑、
    # 默认权限不生效，所以显式授（同 063 的 line_event），生产幂等无害
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO {_APP_ROLE}")
    _log(f"{_TABLE} 表已创建（CHECK 2 个、唯一 (tenant_id, platform)、RLS、{_APP_ROLE} 读写）")


def _seed_defaults(bind: sa.Connection) -> None:
    values = ", ".join(
        f"('{platform}', CAST('{coef}' AS numeric), {threshold})"
        for platform, coef, threshold in _DEFAULTS
    )
    inserted = bind.execute(
        sa.text(
            f"INSERT INTO {_TABLE} (id, tenant_id, platform, like_coefficient, hit_threshold) "
            "SELECT gen_random_uuid(), t.id, v.platform, v.coef, v.threshold "
            f"FROM tenant t CROSS JOIN (VALUES {values}) AS v(platform, coef, threshold) "
            "ON CONFLICT (tenant_id, platform) DO NOTHING"
        )
    ).rowcount
    rows = bind.execute(
        sa.text(
            f"SELECT t.code, count(s.id) AS n FROM tenant t LEFT JOIN {_TABLE} s "
            "ON s.tenant_id = t.id GROUP BY t.code ORDER BY t.code"
        )
    ).all()
    _log(f"默认值插入 {inserted or 0} 行（{len(rows)} 个租户 × {len(_DEFAULTS)} 个平台）")
    for r in rows:
        _log(f"  - 租户 {r.code}: {r.n} 行")


def _seed_permissions(bind: sa.Connection) -> None:
    for scope, name in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
    granted = 0
    for role_code, scopes in _MATRIX.items():
        for scope in scopes:
            res = bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )
            granted += res.rowcount or 0
    _log(f"权限入册 {len(_PERMISSIONS)} 项功能 scope，角色授权 {granted} 条（管理员靠 *）")
    for role_code, scopes in _MATRIX.items():
        _log(f"  - {role_code}: {', '.join(scopes)}")


def _clear_coverage(bind: sa.Connection) -> None:
    cleared = bind.execute(sa.text("DELETE FROM report_summary_coverage")).rowcount
    _log(f"report_summary_coverage 清掉 {cleared or 0} 行，下一次刷新前报表回退实时")


def upgrade() -> None:
    bind = op.get_bind()
    _create_table()
    _seed_defaults(bind)
    _seed_permissions(bind)
    _clear_coverage(bind)


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [s for s, _ in _PERMISSIONS]
    # user_permission_override → permission 是 RESTRICT：先删个人授权；删之前逐条打印，回滚后照着手工补回
    overrides = bind.execute(
        sa.text(
            'SELECT coalesce(u.username, CAST(o.user_id AS text)) AS who, p.scope, o.effect '
            "FROM user_permission_override o "
            "JOIN permission p ON p.id = o.permission_id "
            'LEFT JOIN "user" u ON u.id = o.user_id '
            "WHERE p.scope = ANY(CAST(:scopes AS text[])) "
            "ORDER BY who, p.scope"
        ),
        {"scopes": scopes},
    ).all()
    _log(f"将删除这 {len(scopes)} 个 scope 上的个人授权 {len(overrides)} 条")
    for r in overrides:
        _log(f"  - 个人授权 {r.who} {r.scope} {r.effect}")
    bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(CAST(:scopes AS text[])))"
        ),
        {"scopes": scopes},
    )
    grants = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(CAST(:scopes AS text[])))"
        ),
        {"scopes": scopes},
    )
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(CAST(:scopes AS text[]))"),
        {"scopes": scopes},
    )
    _log(f"删除角色授权 {grants.rowcount or 0} 条与 scope {', '.join(scopes)}")

    op.execute(disable_rls_sql(_TABLE))
    op.drop_index("uq_promotion_platform_setting_tenant_platform", table_name=_TABLE)
    op.drop_table(_TABLE)
    _log(f"{_TABLE} 表已删除")
    # 回到 064 后工作进度爆文数按 >= 500 算，按平台阈值写下的覆盖记录作废
    _clear_coverage(bind)
