"""流程线 M3：催发「标记完成」+ 直接建单 scope（流程线设计 2.4）。

- ``urge_task`` 加 ``close_attachment_id``（→ attachment RESTRICT，部分索引）：「标记完成」的截图。
  任务被手动催发重开时由 service 与 ``closed_at``、``close_reason`` 一起清空；截图本身永久留在附件表和 ``line_event``。
- 关闭原因 CHECK 删 → 同名重建，加「已完成」；新建 ``ck_urge_task_complete_screenshot``：已完成必须有截图（库级兜底，
  service 先报友好错误）。
- 权限：``promotion_create:write``（新建推广 / 导入站外推广 / 停用推广单）授 pr_manager，管理员靠 ``*``。
  独立一级域：叫 ``promotion.create:write`` 会被 PR 的 ``promotion.*:*`` 命中。

下行：「已完成」的任务改记「手动关闭」（打印行数），恢复旧 CHECK、删列；这个 scope 上的个人授权
（``user_permission_override``）一并删除。

Revision ID: 064_urge_complete
Revises: 063_negotiation_two_level
Create Date: 2026-10-13
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "064_urge_complete"
down_revision: str | Sequence[str] | None = "063_negotiation_two_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移编号只出现在这里（与并行分支谁后合并谁改号，见 brief-flow5 第 1 节）
_REV = "064"

_COMPLETED = "已完成"
_MANUAL = "手动关闭"
_REASONS_OLD = ("博主已发布", "已取消", "手动关闭")
_REASONS_NEW = (*_REASONS_OLD, _COMPLETED)

_PERMISSIONS: list[tuple[str, str]] = [
    ("promotion_create:write", "新建推广 / 导入站外推广 / 停用推广单"),
]

_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["promotion_create:write"],
}


def _log(msg: str) -> None:
    print(f"[{_REV}] {msg}")


def _reason_check(values: tuple[str, ...]) -> str:
    return "close_reason IS NULL OR close_reason IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _status_counts(bind: sa.Connection) -> str:
    rows = bind.execute(
        sa.text(
            "SELECT status, coalesce(close_reason, '-') AS reason, count(*) AS n FROM urge_task "
            "GROUP BY status, close_reason ORDER BY status, close_reason"
        )
    ).all()
    return "、".join(f"{r.status}·{r.reason} {r.n}" for r in rows) or "无任务"


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


def upgrade() -> None:
    bind = op.get_bind()
    _log(f"现有催发任务：{_status_counts(bind)}")
    op.add_column(
        "urge_task",
        sa.Column(
            "close_attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attachment.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_urge_task_close_attachment",
        "urge_task",
        ["close_attachment_id"],
        postgresql_where=sa.text("close_attachment_id IS NOT NULL"),
    )
    _log("urge_task 加 close_attachment_id（→ attachment RESTRICT）与部分索引")
    # 删 → 同名重建（命名约定套前缀，库里实名 ck_urge_task_ck_urge_task_close_reason）
    op.drop_constraint("ck_urge_task_close_reason", "urge_task", type_="check")
    op.create_check_constraint(
        "ck_urge_task_close_reason", "urge_task", _reason_check(_REASONS_NEW)
    )
    op.create_check_constraint(
        "ck_urge_task_complete_screenshot",
        "urge_task",
        f"close_reason IS DISTINCT FROM '{_COMPLETED}' OR close_attachment_id IS NOT NULL",
    )
    _log("关闭原因 CHECK 加「已完成」；新建 ck_urge_task_complete_screenshot（已完成必须有截图）")
    _seed_permissions(bind)


def downgrade() -> None:
    bind = op.get_bind()
    folded = bind.execute(
        sa.text(
            "UPDATE urge_task SET close_reason = CAST(:manual AS varchar), close_attachment_id = NULL "
            "WHERE close_reason = CAST(:completed AS varchar)"
        ),
        {"manual": _MANUAL, "completed": _COMPLETED},
    )
    _log(f"已完成 → 手动关闭 {folded.rowcount or 0} 个任务（截图仍在附件表与事件里）")
    op.drop_constraint("ck_urge_task_complete_screenshot", "urge_task", type_="check")
    op.drop_constraint("ck_urge_task_close_reason", "urge_task", type_="check")
    op.create_check_constraint(
        "ck_urge_task_close_reason", "urge_task", _reason_check(_REASONS_OLD)
    )
    op.drop_index("idx_urge_task_close_attachment", table_name="urge_task")
    op.drop_column("urge_task", "close_attachment_id")
    _log("已恢复旧关闭原因 CHECK，删 close_attachment_id")
    # user_permission_override → permission 是 RESTRICT：先删个人授权，再删角色授权与 scope
    scopes = [s for s, _ in _PERMISSIONS]
    overrides = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    _log(f"删除这 {len(scopes)} 个 scope 上的个人授权 {overrides.rowcount or 0} 条")
    grants = bind.execute(
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
    _log(f"删除角色授权 {grants.rowcount or 0} 条与 scope {', '.join(scopes)}")
