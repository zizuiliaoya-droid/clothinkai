"""催发任务：urge_config / urge_task / urge_record 三表 + 权限。

PRD V1.4 改动 2。现状是 U07 只有「每天 09:00 扫描 → 企微群发」一条自动链路，
而且生产上 ``wecom_config`` 0 行 —— 那条链路从未产生过一条记录。所以催发任务
刻意不建在 ``wecom_message`` 之上：企微是**通知方式**之一，任务本身要能脱离它成立
（手动催发、留痕、计次、看板都不需要企微）。

三张表的分工：
- ``urge_config``：租户级阈值（单行）。顺便收编 ``legacy_settings`` 里的两个硬编码
  天数阈值，它们在 ``wecom/scan_service.py`` 还被重复定义了一遍，双份真相迟早不一致。
- ``urge_task``：一个推广单一个任务。``UNIQUE(tenant_id, promotion_id)`` 是自动扫描的
  幂等基石；「博主确认发布 → 任务自动关闭」「超过 N 次提示主管」都是单据维度的语义，
  而 ``wecom_message`` 是按 (blogger, pr) 聚合的，一条覆盖多个推广单，挂不上去。
- ``urge_record``：一个任务多条留痕（截图 + 时间戳 + 备注），永久保留无 is_active。

关于 ``max_overdue_days``：``find_urge_candidates`` 把 urge_status='超时' 也算候选，
而生产有 5134 条历史单的排期在半年前（diff -113 ~ -184 天）。不设上限的话自动扫描
第一次跑就会建 5134 个任务，而且之后天天催。超过上限的陈旧单不自动建任务，仍可手动催。

权限：
- ``promotion.urge:read/write`` 走 promotion 一级域 —— 催发是 PR 的日常工作，
  PR 持 ``promotion.*:*`` 自动获得正是想要的效果；运营持 ``promotion.*:read`` 只读。
- ``urge_config:read/write`` 必须**独立一级域**。叫 ``promotion.urge_config:write``
  的话 PR 会连阈值一起改掉（``has()`` 的前缀通配只看第一段），改阈值是管理层的事。

Revision ID: 049_urge_task
Revises: 048_negotiation
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "049_urge_task"
down_revision: str | Sequence[str] | None = "048_negotiation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS: list[tuple[str, str]] = [
    ("promotion.urge:read", "查看催发任务与留痕"),
    ("promotion.urge:write", "发起催发 / 关闭催发任务"),
    ("urge_config:read", "查看催发阈值配置"),
    ("urge_config:write", "修改催发阈值配置"),
]

# promotion.urge 两条不必显式授给 pr / pr_manager / operations ——
# 它们分别持 promotion.*:* 与 promotion.*:read，前缀通配已覆盖。
# 这里只授通配覆盖不到的：urge_config 独立一级域给主管，财务只读看催发进度。
_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["urge_config:read", "urge_config:write"],
    "finance": ["promotion.urge:read"],
}


def _log(msg: str) -> None:
    print(f"[049] {msg}")


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # urge_config：租户级阈值（单行）
    # ------------------------------------------------------------------ #
    op.create_table(
        "urge_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("no_publish_days", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("max_urge_times", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("max_overdue_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column(
            "urge_threshold_days", sa.Integer(), nullable=False, server_default=sa.text("10")
        ),
        sa.Column(
            "important_threshold_days", sa.Integer(), nullable=False, server_default=sa.text("3")
        ),
        sa.Column(
            "auto_scan_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "no_publish_days BETWEEN 1 AND 60", name="ck_urge_config_no_publish_days"
        ),
        sa.CheckConstraint("max_urge_times BETWEEN 1 AND 20", name="ck_urge_config_max_urge_times"),
        sa.CheckConstraint(
            "max_overdue_days BETWEEN 1 AND 365", name="ck_urge_config_max_overdue_days"
        ),
        sa.CheckConstraint(
            "urge_threshold_days BETWEEN 1 AND 60", name="ck_urge_config_urge_threshold_days"
        ),
        # 「重要催发」必须比「催发」更紧急，否则 urge_status 的分支永远取不到重要催发
        sa.CheckConstraint(
            "important_threshold_days >= 0 AND important_threshold_days <= urge_threshold_days",
            name="ck_urge_config_important_le_urge",
        ),
    )
    op.create_index("uq_urge_config_tenant", "urge_config", ["tenant_id"], unique=True)
    op.execute(enable_rls_sql("urge_config"))
    _log("urge_config 表已创建（单租户单行）")

    # ------------------------------------------------------------------ #
    # urge_task：一个推广单一个任务
    # ------------------------------------------------------------------ #
    op.create_table(
        "urge_task",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        # blogger_id / pr_id 是冗余列：看板要按 PR 聚合、列表要显示博主，
        # 存一份省掉每次 join promotion。推广单的这两个字段建单后不变，不存在漂移。
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(8), nullable=False, server_default=sa.text("'进行中'")),
        sa.Column("urge_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_urged_at", sa.DateTime(timezone=True), nullable=True),
        # 自动扫描的当日幂等：UPDATE WHERE last_auto_urged_on IS DISTINCT FROM :today
        # 单语句原子，0 行就跳过。比表达式唯一索引简单，够用。
        sa.Column("last_auto_urged_on", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(16), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('进行中', '已关闭')", name="ck_urge_task_status"),
        sa.CheckConstraint("urge_count >= 0", name="ck_urge_task_urge_count_nonneg"),
        sa.CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('博主已发布', '已取消', '手动关闭')",
            name="ck_urge_task_close_reason",
        ),
        # 关闭必须留时间与原因，没关闭就不该有 —— 否则看板统计「进行中」会算错
        sa.CheckConstraint(
            "(status = '已关闭' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)"
            " OR (status = '进行中' AND closed_at IS NULL AND close_reason IS NULL)",
            name="ck_urge_task_closed_fields",
        ),
    )
    # 一单一任务。自动扫描靠这个唯一约束幂等（ON CONFLICT DO NOTHING），
    # 不靠 SELECT-then-INSERT —— 那个模式在 wecom/scan_service 里真并发会双发。
    op.create_index(
        "uq_urge_task_promotion", "urge_task", ["tenant_id", "promotion_id"], unique=True
    )
    op.create_index("idx_urge_task_status", "urge_task", ["tenant_id", "status", "last_urged_at"])
    op.create_index("idx_urge_task_pr", "urge_task", ["tenant_id", "pr_id", "status"])
    op.create_index("idx_urge_task_blogger", "urge_task", ["tenant_id", "blogger_id"])
    op.execute(enable_rls_sql("urge_task"))
    _log("urge_task 表已创建（一单一任务，UNIQUE 幂等）")

    # ------------------------------------------------------------------ #
    # urge_record：一个任务多条留痕
    # ------------------------------------------------------------------ #
    op.create_table(
        "urge_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("urge_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(8), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("screenshot_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("wecom_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        # 任务删了留痕没有意义，跟着走
        sa.ForeignKeyConstraint(["urge_task_id"], ["urge_task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        # 截图是留痕证据，不允许附件先被删掉
        sa.ForeignKeyConstraint(
            ["screenshot_attachment_id"], ["attachment.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["wecom_message_id"], ["wecom_message.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint("trigger_type IN ('手动', '自动')", name="ck_urge_record_trigger_type"),
    )
    # 时间线倒序（详情页）
    op.create_index(
        "idx_urge_record_task",
        "urge_record",
        ["tenant_id", "urge_task_id", sa.text("created_at DESC")],
    )
    # 看板「本周已催发 N」
    op.create_index("idx_urge_record_created", "urge_record", ["tenant_id", "created_at"])
    op.create_index("idx_urge_record_promotion", "urge_record", ["tenant_id", "promotion_id"])
    op.execute(enable_rls_sql("urge_record"))
    _log("urge_record 表已创建（永久留痕，时间线倒序索引）")

    # ------------------------------------------------------------------ #
    # 为现存租户插入默认配置，省掉「第一次用要先去配一遍」
    # ------------------------------------------------------------------ #
    res = bind.execute(
        sa.text(
            "INSERT INTO urge_config (id, tenant_id, created_at, updated_at) "
            "SELECT gen_random_uuid(), t.id, NOW(), NOW() FROM tenant t "
            "ON CONFLICT DO NOTHING"
        )
    )
    _log(f"为 {res.rowcount or 0} 个租户写入默认阈值（5 天 / 3 次 / 超时上限 30 天）")

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
    _log("  - pr / pr_manager 的 promotion.urge:* 由 promotion.*:* 通配覆盖")
    _log("  - operations 的 promotion.urge:read 由 promotion.*:read 通配覆盖")


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
    op.execute(disable_rls_sql("urge_record"))
    op.drop_table("urge_record")
    op.execute(disable_rls_sql("urge_task"))
    op.drop_table("urge_task")
    op.execute(disable_rls_sql("urge_config"))
    op.drop_table("urge_config")
