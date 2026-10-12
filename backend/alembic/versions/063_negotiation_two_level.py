"""流程线 M2：谈款两级审核 + 主线号 + 事件表 + 谈款商品明细（流程线设计 2.3）。

- 新表 ``line_event``：跨环节事件，只追加（``seq`` identity 定序；``clothing_app`` 只有 INSERT / SELECT）。
- ``negotiation`` / ``promotion`` 各加 ``line_id``（库发号）；谈款生成的推广单改成谈款的号，再建两个唯一索引
  （``uq_promotion_line`` 同时是「一条主线只有一张推广单」的库级保证）。
- ``negotiation`` 加 9 列：老板审（``final_reviewed_*``）、收货三项、收货确认人 / 时间、复制来源。
- 状态 CHECK 换成 7 态：删旧 CHECK → 同义改名（待审核 → 主管待审、审核通过 → 已定稿）→ 建新 CHECK。
- 新表 ``negotiation_item``：谈款选的颜色尺码（定稿时复制成 ``promotion_item``）。
- 权限：``negotiation_all:read`` 授 pr_manager / finance / operations；``negotiation_final:approve`` 只入册（管理员靠 ``*``）；
  时间线 6 个分组的字段 scope ``field.line_event.<规则键>:read`` 只入册（照 012）。

下行有损：7 态折回 4 态（主管待审 / 待老板审核 / 待补收货地址 → 待审核，老板退回 → 草稿，已定稿 → 审核通过），
老板审、收货信息、复制来源、谈款商品明细与全部事件丢弃；这 8 个 scope 上的个人授权（``user_permission_override``）一并删除。

Revision ID: 063_negotiation_two_level
Revises: 062_report_view_columns
Create Date: 2026-10-10
"""

from __future__ import annotations

from typing import Any, Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "063_negotiation_two_level"
down_revision: str | Sequence[str] | None = "062_report_view_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 迁移编号只出现在这里（与并行分支谁后合并谁改号，见 brief-flow3 第 8 节）
_REV = "063"

_APP_ROLE = "clothing_app"
# identity 列的隐式序列名（PG 规则：<表>_<列>_seq）
_LINE_EVENT_SEQ = "line_event_seq_seq"

_STATUSES_OLD = ("草稿", "待审核", "审核通过", "审核驳回")
_STATUSES_NEW = ("草稿", "主管待审", "审核驳回", "待老板审核", "老板退回", "待补收货地址", "已定稿")

_PERMISSIONS: list[tuple[str, str]] = [
    ("negotiation_all:read", "查看全部谈款（草稿除外）"),
    ("negotiation_final:approve", "老板定稿 / 退回谈款"),
]

_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["negotiation_all:read"],
    "finance": ["negotiation_all:read"],
    "operations": ["negotiation_all:read"],
}

# 照 012：只入册、不绑角色（时间线事件分组的个人授予 / 撤销用，设计 6.5）
_FIELD_SCOPES: list[tuple[str, str]] = [
    ("field.line_event.negotiation:read", "字段-时间线谈款段-读"),
    ("field.line_event.comment:read", "字段-时间线留言-读"),
    ("field.line_event.promotion:read", "字段-时间线推广段-读"),
    ("field.line_event.shipping:read", "字段-时间线发货段-读"),
    ("field.line_event.settlement:read", "字段-时间线结款段-读"),
    ("field.line_event.retro:read", "字段-时间线复盘段-读"),
]

_NEGOTIATION_NEW_COLUMNS = (
    "copied_from_id",
    "receiver_confirmed_by",
    "receiver_confirmed_at",
    "receiver_address",
    "receiver_phone",
    "receiver_name",
    "final_review_opinion",
    "final_reviewed_at",
    "final_reviewed_by",
)


def _log(msg: str) -> None:
    print(f"[{_REV}] {msg}")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _base_columns() -> list[sa.Column[Any]]:
    """TenantScopedModel 的 4 列。"""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _create_line_event() -> None:
    op.create_table(
        "line_event",
        *_base_columns(),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("negotiation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("from_state", sa.String(16), nullable=True),
        sa.Column("to_state", sa.String(16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["negotiation_id"], ["negotiation.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "negotiation_id IS NOT NULL OR promotion_id IS NOT NULL",
            name="ck_line_event_owner",
        ),
    )
    op.create_index("idx_line_event_line", "line_event", ["tenant_id", "line_id", "seq"])
    op.execute(enable_rls_sql("line_event"))
    # 测试库与 CI 以 postgres 跑迁移，clothing_bypass 的默认权限（025）不生效，所以显式授；生产幂等无害。
    # 只追加：clothing_app 只有 INSERT / SELECT（照 002 / 025 对 audit_log 的做法）
    op.execute(f"GRANT SELECT, INSERT ON line_event TO {_APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON line_event FROM {_APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {_LINE_EVENT_SEQ} TO {_APP_ROLE}")
    _log("line_event 表已创建（RLS、idx_line_event_line；clothing_app 只有 INSERT / SELECT）")


def _add_line_ids(bind: sa.Connection) -> None:
    # 易变默认值让 PG 逐行发号（重写表）
    for table in ("negotiation", "promotion"):
        op.add_column(
            table,
            sa.Column(
                "line_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
        )
    counts = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM negotiation) AS n, (SELECT count(*) FROM promotion) AS p"
        )
    ).one()
    _log(f"line_id 发号：谈款 {counts.n} 张、推广单 {counts.p} 张")

    # D3：一张推广单被两张谈款指向时只能继承其中一张的号（不阻断，打印出来上线前核对）
    shared = bind.execute(
        sa.text(
            "SELECT count(*) FROM (SELECT promotion_id FROM negotiation "
            "WHERE promotion_id IS NOT NULL GROUP BY promotion_id HAVING count(*) > 1) d"
        )
    ).scalar_one()
    _log(f"D3：被两张以上谈款指向的推广单 {shared} 张（预期 0）")
    inherited = bind.execute(
        sa.text(
            "UPDATE promotion p SET line_id = n.line_id FROM negotiation n "
            "WHERE n.promotion_id = p.id AND n.tenant_id = p.tenant_id"
        )
    )
    _log(f"谈款生成的推广单继承谈款的 line_id {inherited.rowcount or 0} 张")

    op.create_index("uq_negotiation_line", "negotiation", ["tenant_id", "line_id"], unique=True)
    op.create_index("uq_promotion_line", "promotion", ["tenant_id", "line_id"], unique=True)
    _log("唯一索引 uq_negotiation_line / uq_promotion_line 已建")


def _add_negotiation_columns() -> None:
    def user_fk(name: str) -> sa.Column[Any]:
        return sa.Column(
            name,
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        )

    op.add_column("negotiation", user_fk("final_reviewed_by"))
    op.add_column(
        "negotiation", sa.Column("final_reviewed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("negotiation", sa.Column("final_review_opinion", sa.Text(), nullable=True))
    op.add_column("negotiation", sa.Column("receiver_name", sa.String(32), nullable=True))
    op.add_column("negotiation", sa.Column("receiver_phone", sa.String(32), nullable=True))
    op.add_column("negotiation", sa.Column("receiver_address", sa.String(255), nullable=True))
    op.add_column(
        "negotiation",
        sa.Column("receiver_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("negotiation", user_fk("receiver_confirmed_by"))
    op.add_column(
        "negotiation",
        sa.Column(
            "copied_from_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("negotiation.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    _log("negotiation 加 9 列（老板审 3、收货 3、收货确认 2、复制来源 1）")


def _switch_status_check(bind: sa.Connection) -> None:
    # 顺序：删 → 改名 → 建（旧 CHECK 不认新值；「审核通过 ⇔ 有推广单」引用旧值）
    op.drop_constraint("ck_negotiation_status", "negotiation", type_="check")
    op.drop_constraint("ck_negotiation_approved_has_promotion", "negotiation", type_="check")
    pending = bind.execute(
        sa.text("UPDATE negotiation SET status = '主管待审' WHERE status = '待审核'")
    )
    finalized = bind.execute(
        sa.text("UPDATE negotiation SET status = '已定稿' WHERE status = '审核通过'")
    )
    _log(
        f"同义改名：待审核 → 主管待审 {pending.rowcount or 0} 张，"
        f"审核通过 → 已定稿 {finalized.rowcount or 0} 张"
    )
    op.create_check_constraint(
        "ck_negotiation_status", "negotiation", f"status IN ({_in_list(_STATUSES_NEW)})"
    )
    op.create_check_constraint(
        "ck_negotiation_finalized_has_promotion",
        "negotiation",
        "(status = '已定稿' AND promotion_id IS NOT NULL)"
        " OR (status <> '已定稿' AND promotion_id IS NULL)",
    )
    dist = bind.execute(
        sa.text("SELECT status, count(*) FROM negotiation GROUP BY status ORDER BY status")
    ).all()
    _log("状态 CHECK 换成 7 态；现分布：" + ("、".join(f"{s} {n}" for s, n in dist) or "无谈款"))


def _create_negotiation_item() -> None:
    op.create_table(
        "negotiation_item",
        *_base_columns(),
        sa.Column("negotiation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["negotiation_id"], ["negotiation.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_negotiation_item_style",
        "negotiation_item",
        ["tenant_id", "negotiation_id", "style_id"],
        unique=True,
    )
    # SKU 删除前的引用校验用；草稿可以先不选颜色尺码，只索引选了的
    op.create_index(
        "idx_negotiation_item_sku",
        "negotiation_item",
        ["tenant_id", "sku_id"],
        postgresql_where=sa.text("sku_id IS NOT NULL"),
    )
    op.execute(enable_rls_sql("negotiation_item"))
    _log("negotiation_item 表已创建（RLS、uq_negotiation_item_style、idx_negotiation_item_sku）")


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
    for scope, name in _FIELD_SCOPES:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'field', NOW(), NOW()) "
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
    _log(
        f"权限入册 {len(_PERMISSIONS)} 项功能 scope + {len(_FIELD_SCOPES)} 项字段 scope，"
        f"角色授权 {granted} 条（negotiation_final:approve 不绑角色，管理员靠 *）"
    )
    for role_code, scopes in _MATRIX.items():
        _log(f"  - {role_code}: {', '.join(scopes)}")


def upgrade() -> None:
    bind = op.get_bind()
    _create_line_event()
    _add_line_ids(bind)
    _add_negotiation_columns()
    _switch_status_check(bind)
    _create_negotiation_item()
    _seed_permissions(bind)


def _log_lossy(bind: sa.Connection) -> None:
    row = bind.execute(
        sa.text(
            """
            SELECT
              count(*) FILTER (WHERE final_reviewed_by IS NOT NULL OR final_reviewed_at IS NOT NULL
                               OR final_review_opinion IS NOT NULL) AS final_review,
              count(*) FILTER (WHERE receiver_name IS NOT NULL OR receiver_phone IS NOT NULL
                               OR receiver_address IS NOT NULL
                               OR receiver_confirmed_at IS NOT NULL) AS receiver,
              count(*) FILTER (WHERE copied_from_id IS NOT NULL) AS copied
            FROM negotiation
            """
        )
    ).one()
    items = bind.execute(sa.text("SELECT count(*) FROM negotiation_item")).scalar_one()
    events = bind.execute(sa.text("SELECT count(*) FROM line_event")).scalar_one()
    _log(
        f"下行有损：老板审 {row.final_review} 张、收货信息 {row.receiver} 张、"
        f"复制来源 {row.copied} 张、谈款商品明细 {items} 行、事件 {events} 条将丢弃"
    )


def _fold_statuses(bind: sa.Connection) -> None:
    op.drop_constraint("ck_negotiation_finalized_has_promotion", "negotiation", type_="check")
    op.drop_constraint("ck_negotiation_status", "negotiation", type_="check")
    folds = (
        ("待审核", ("主管待审", "待老板审核", "待补收货地址")),
        ("草稿", ("老板退回",)),
        ("审核通过", ("已定稿",)),
    )
    for to_status, from_statuses in folds:
        res = bind.execute(
            sa.text(
                "UPDATE negotiation SET status = CAST(:to AS varchar) "
                "WHERE status = ANY(CAST(:froms AS varchar[]))"
            ),
            {"to": to_status, "froms": list(from_statuses)},
        )
        _log(f"折回 {'/'.join(from_statuses)} → {to_status} {res.rowcount or 0} 张")
    op.create_check_constraint(
        "ck_negotiation_status", "negotiation", f"status IN ({_in_list(_STATUSES_OLD)})"
    )
    op.create_check_constraint(
        "ck_negotiation_approved_has_promotion",
        "negotiation",
        "(status = '审核通过' AND promotion_id IS NOT NULL)"
        " OR (status <> '审核通过' AND promotion_id IS NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    _log_lossy(bind)
    _fold_statuses(bind)

    op.execute(disable_rls_sql("negotiation_item"))
    op.drop_table("negotiation_item")
    op.execute(disable_rls_sql("line_event"))
    op.drop_table("line_event")
    op.drop_index("uq_promotion_line", table_name="promotion")
    op.drop_index("uq_negotiation_line", table_name="negotiation")
    op.drop_column("promotion", "line_id")
    op.drop_column("negotiation", "line_id")
    for column in _NEGOTIATION_NEW_COLUMNS:
        op.drop_column("negotiation", column)
    _log("已删 negotiation_item、line_event、两边 line_id 与谈款 9 列")

    # user_permission_override → permission 是 RESTRICT：先删个人授权，再删角色授权与 scope
    scopes = [s for s, _ in _PERMISSIONS] + [s for s, _ in _FIELD_SCOPES]
    overrides = bind.execute(
        sa.text(
            "DELETE FROM user_permission_override WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    _log(f"删除这 {len(scopes)} 个 scope 上的个人授权 {overrides.rowcount or 0} 条")
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
