"""流程线 M5：复盘三态、主管写复盘及已完结（设计2.6）。

旧待确认稿留档且不盖确认戳；待确认、已发布未开始映射待复盘。
按正常/召回条件回填 completed_at，不自动生成 R33。
下行恢复四态约束，但不恢复旧待确认状态或原完结时间（有损，优先向前修复）；
删除新scope及个人授权，授权需在下行前另行导出。

Revision ID: 067_retro_completion
Revises: 066_platform_metric_setting
Create Date: 2026-10-21
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "067_retro_completion"
down_revision: str | Sequence[str] | None = "066_platform_metric_setting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE = "promotion_retro:write"


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("promotion", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "idx_promotion_completed", "promotion", ["tenant_id", sa.text("completed_at DESC")],
        postgresql_where=sa.text("completed_at IS NOT NULL"),
    )
    op.drop_constraint("ck_promotion_retro_status", "promotion", type_="check")
    mapped = bind.execute(sa.text(
        "UPDATE promotion SET retro_status='待复盘' WHERE retro_status='待确认' "
        "OR (retro_status='未开始' AND publish_status='已发布')"
    ))
    op.create_check_constraint(
        "ck_promotion_retro_status", "promotion", "retro_status IN ('未开始','待复盘','已完成')"
    )
    completed = bind.execute(sa.text(
        "UPDATE promotion SET completed_at=now() WHERE completed_at IS NULL AND ("
        "(publish_status='已发布' AND settlement_status='已结款' "
        "AND metrics_recorded_at IS NOT NULL AND retro_status='已完成') "
        "OR (publish_status='已取消' AND recall_status='召回成功'))"
    ))
    bind.execute(sa.text(
        "INSERT INTO permission(id,scope,name,category,created_at,updated_at) "
        "VALUES (:id,:scope,'主管写复盘','function',now(),now()) ON CONFLICT(scope) DO NOTHING"
    ), {"id": str(uuid4()), "scope": _SCOPE})
    bind.execute(sa.text(
        "INSERT INTO role_permission(id,role_id,permission_id) "
        "SELECT :id,r.id,p.id FROM role r,permission p WHERE r.code='pr_manager' "
        "AND p.scope=:scope ON CONFLICT(role_id,permission_id) DO NOTHING"
    ), {"id": str(uuid4()), "scope": _SCOPE})
    print(f"[067] 复盘映射 {mapped.rowcount}；完结回填 {completed.rowcount}；主管复盘scope入册")


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint("ck_promotion_retro_status", "promotion", type_="check")
    op.create_check_constraint(
        "ck_promotion_retro_status", "promotion",
        "retro_status IN ('未开始','待复盘','待确认','已完成')",
    )
    op.drop_index("idx_promotion_completed", table_name="promotion")
    op.drop_column("promotion", "completed_at")
    overrides = bind.execute(sa.text(
        "DELETE FROM user_permission_override WHERE permission_id IN "
        "(SELECT id FROM permission WHERE scope=:scope)"
    ), {"scope": _SCOPE})
    bind.execute(sa.text(
        "DELETE FROM role_permission WHERE permission_id IN "
        "(SELECT id FROM permission WHERE scope=:scope)"
    ), {"scope": _SCOPE})
    bind.execute(sa.text("DELETE FROM permission WHERE scope=:scope"), {"scope": _SCOPE})
    print(f"[067] 下行丢弃完结时间、不恢复待确认；删除个人授权 {overrides.rowcount} 条")
