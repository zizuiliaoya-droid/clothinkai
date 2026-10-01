"""复盘环节：retro_status + 7 天数据字段 + blogger_retrospective 表。

PRD V1.4 改动 4。原流程最后一步是「已结款 → 发布满 7 天录点赞/收藏/评论+截图 →
已完成」，改动 4 在中间插了复盘::

    已结款 → 录 7 天数据(点赞/收藏/评论+截图) → 待复盘
          → PR 手输复盘文字 → 待确认
          → 主管确认 → 已完成（终态）

两个设计决定值得留痕：

**retro_status 是新字段，不是给 settlement_status 加值。** ``已付款`` 在
``SettlementStatusMachine`` 里是终态，而且全系统有一批查询按
``settlement_status = '已付款'`` 过滤（索引、汇总、财务列表）。把「待复盘」塞进那个
枚举，所有这些查询都会漏掉进入复盘的单子。另外置换单根本没有 settlement 行
（approve_barter 不发 SettlementRequested），把复盘挂在财务侧会让置换单永远进不了复盘。

**复盘文字在独立子表，不在 promotion 字段上。** PRD 原文「永久写入博主档案（跨单据
伴随这个博主）」「不随单据关闭而丢失」。存字段的话，被主管打回后重写会覆盖上一版，
推广单软删后 hover 卡也查不到 —— 两条都不满足「永久」。

收藏数 / 评论数转 typed 列：这三个指标是复盘依据，要给人看、要进校验，继续塞
``source_extra`` JSONB 里存什么类型都行挡不住脏数据。**没有动**
``source_extra['点赞数']`` —— typed ``like_count`` 与它并存不同步是独立的待确认项。

Revision ID: 050_retrospective
Revises: 049_urge_task
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "050_retrospective"
down_revision: str | Sequence[str] | None = "049_urge_task"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS: list[tuple[str, str]] = [
    ("promotion.retro:write", "录 7 天数据 / 提交复盘"),
    ("promotion.retro:confirm", "确认 / 打回复盘"),
]

# promotion.retro:write 不必显式授：PR 与主管的 promotion.*:* 通配已覆盖。
# confirm 必须显式给主管 —— 它的 action 是 confirm，PR 的 promotion.*:* 会命中，
# 所以这条拦不住 PR。真正的自审门槛在 service 的 RetroSelfConfirmForbiddenError。
_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["promotion.retro:confirm"],
}


def _log(msg: str) -> None:
    print(f"[050] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # promotion 新列
    # ------------------------------------------------------------------ #
    op.add_column("promotion", sa.Column("collect_count", sa.Integer(), nullable=True))
    op.add_column("promotion", sa.Column("comment_count", sa.Integer(), nullable=True))
    op.add_column(
        "promotion",
        sa.Column("metrics_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "promotion", sa.Column("metrics_recorded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "promotion",
        sa.Column(
            "retro_status", sa.String(8), nullable=False, server_default=sa.text("'未开始'")
        ),
    )
    op.add_column(
        "promotion",
        sa.Column("retro_confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "promotion", sa.Column("retro_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_foreign_key(
        "fk_promotion_metrics_attachment",
        "promotion",
        "attachment",
        ["metrics_attachment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_promotion_retro_confirmed_by",
        "promotion",
        "user",
        ["retro_confirmed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_check_constraint(
        "ck_promotion_collect_count_nonneg",
        "promotion",
        "collect_count IS NULL OR collect_count >= 0",
    )
    op.create_check_constraint(
        "ck_promotion_comment_count_nonneg",
        "promotion",
        "comment_count IS NULL OR comment_count >= 0",
    )
    op.create_check_constraint(
        "ck_promotion_retro_status",
        "promotion",
        "retro_status IN ('未开始', '待复盘', '待确认', '已完成')",
    )
    op.create_index("idx_promotion_retro_status", "promotion", ["tenant_id", "retro_status"])
    _log("promotion 新增 7 列（收藏/评论/截图/录入时间/复盘状态/确认人/确认时间）")

    # ------------------------------------------------------------------ #
    # blogger_retrospective
    # ------------------------------------------------------------------ #
    op.create_table(
        "blogger_retrospective",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["confirmed_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "length(btrim(content)) > 0", name="ck_blogger_retro_content_nonempty"
        ),
        sa.CheckConstraint(
            "(confirmed_by IS NULL) = (confirmed_at IS NULL)",
            name="ck_blogger_retro_confirm_fields",
        ),
    )
    op.create_index(
        "idx_blogger_retro_blogger",
        "blogger_retrospective",
        ["tenant_id", "blogger_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_blogger_retro_promotion", "blogger_retrospective", ["tenant_id", "promotion_id"]
    )
    op.execute(enable_rls_sql("blogger_retrospective"))
    _log("blogger_retrospective 表已创建（含 RLS 与 2 个索引）")

    # ------------------------------------------------------------------ #
    # 权限
    # ------------------------------------------------------------------ #
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
    _log(f"权限入册 {len(_PERMISSIONS)} 项，角色授权 {granted} 条")
    _log("  - promotion.retro:write 由 pr / pr_manager 的 promotion.*:* 通配覆盖")


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [s for s, _ in _PERMISSIONS]
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

    op.execute(disable_rls_sql("blogger_retrospective"))
    op.drop_table("blogger_retrospective")

    op.drop_index("idx_promotion_retro_status", table_name="promotion")
    op.drop_constraint("ck_promotion_retro_status", "promotion", type_="check")
    op.drop_constraint("ck_promotion_comment_count_nonneg", "promotion", type_="check")
    op.drop_constraint("ck_promotion_collect_count_nonneg", "promotion", type_="check")
    op.drop_constraint("fk_promotion_retro_confirmed_by", "promotion", type_="foreignkey")
    op.drop_constraint("fk_promotion_metrics_attachment", "promotion", type_="foreignkey")
    for col in (
        "retro_confirmed_at",
        "retro_confirmed_by",
        "retro_status",
        "metrics_recorded_at",
        "metrics_attachment_id",
        "comment_count",
        "collect_count",
    ):
        op.drop_column("promotion", col)
