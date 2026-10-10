"""流程线 M1：推广单收件 / 发货 + 推广单商品明细（流程线设计 2.2）。

- promotion 加 9 列（都可空、无默认值，只改元数据）：收件三项、``ship_status``、推送时间 / 人、快递公司、快递单号、发货时间。
  ``ship_status`` 为 NULL = 历史单，没进系统的发货流程（导入与直接新建默认不进，11-58）。
- 新表 ``promotion_item``：推广单带的颜色尺码（单品 1 行，套装按成员各 1 行），推送仓库直接用。
- ``source_extra`` 的「打单地址」「发货单号」搬进 typed 列（照 047：按长度过滤，超长的原样留在 JSONB，不截断）；
  然后按搬出来的值定发货状态，谈款生成、还没发货的在途单记「待发货」。
- 032 的两个 JSONB 部分索引按 ``ship_status`` 重建。
- 权限：``promotion_ship:push``（纳入 / 推送 / 撤回）授 pr_manager；``promotion_ship:fill``（回填快递信息）、
  ``promotion_ship:export``（导出）授 warehouse；收件三项的字段 scope 只入册（照 012）；收回 warehouse 的 ``promotion:read``
  （仓库页改走专用接口）。独立一级域 ``promotion_ship``：叫 ``promotion.ship:*`` 会被 PR / 主管的 ``promotion.*:*`` 命中。
  旧 ``promotion.warehouse:write`` 不删、不再被检查（回滚用）。

下行有损：``ship_status``、收件人 / 电话（并进 JSONB 的打单地址文本）、快递公司、发货时间、推送人与商品明细丢失；
这 9 个 scope 上的个人授权（``user_permission_override``）一并删除。

Revision ID: 060_promotion_shipping
Revises: 059_8b_blogger_library
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "060_promotion_shipping"
down_revision: str | Sequence[str] | None = "059_8b_blogger_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADDRESS_KEY = "打单地址"
_WAYBILL_KEY = "发货单号"

_PERMISSIONS: list[tuple[str, str]] = [
    ("promotion_ship:push", "推广单纳入发货 / 确认推送仓库 / 撤回推送"),
    ("promotion_ship:fill", "仓库回填快递信息"),
    ("promotion_ship:export", "导出待打单"),
]

_MATRIX: dict[str, list[str]] = {
    "pr_manager": ["promotion_ship:push"],
    "warehouse": ["promotion_ship:fill", "promotion_ship:export"],
}

# 照 012：只入册、不绑角色（默认按字段注册表的角色判定，这些 scope 供个人授予 / 撤销引用）
_FIELD_SCOPES: list[tuple[str, str]] = [
    ("field.promotion.receiver_name:read", "字段-推广收件人-读"),
    ("field.promotion.receiver_name:write", "字段-推广收件人-写"),
    ("field.promotion.receiver_phone:read", "字段-推广收件电话-读"),
    ("field.promotion.receiver_phone:write", "字段-推广收件电话-写"),
    ("field.promotion.receiver_address:read", "字段-推广收件地址-读"),
    ("field.promotion.receiver_address:write", "字段-推广收件地址-写"),
]

_REVOKED_WAREHOUSE_SCOPE = "promotion:read"

# 032 的原文，下行按它重建
_PRINT_ADDRESS_PREDICATE = "COALESCE(BTRIM(source_extra->>'打单地址'), '') <> ''"
_WAYBILL_PREDICATE = "COALESCE(BTRIM(source_extra->>'发货单号'), '') <> ''"


def _log(msg: str) -> None:
    print(f"[060] {msg}")


def _move_key(bind: sa.Connection, *, key: str, column: str, max_len: int) -> None:
    """把 ``source_extra[key]`` 搬进 typed 列：BTRIM 后 1~max_len 字才搬并删键；空串键删；超长的原样留着。"""
    moved = bind.execute(
        sa.text(
            f"""
            UPDATE promotion
            SET {column} = BTRIM(source_extra->>CAST(:key AS text)),
                source_extra = source_extra - CAST(:key AS text)
            WHERE char_length(BTRIM(COALESCE(source_extra->>CAST(:key AS text), '')))
                  BETWEEN 1 AND CAST(:max_len AS integer)
            """
        ),
        {"key": key, "max_len": max_len},
    )
    _log(f"「{key}」→ {column} 搬入 {moved.rowcount or 0} 条")
    emptied = bind.execute(
        sa.text(
            """
            UPDATE promotion SET source_extra = source_extra - CAST(:key AS text)
            WHERE source_extra ? CAST(:key AS text)
              AND BTRIM(COALESCE(source_extra->>CAST(:key AS text), '')) = ''
            """
        ),
        {"key": key},
    )
    _log(f"「{key}」空串键删除 {emptied.rowcount or 0} 条")
    kept = bind.execute(
        sa.text(
            "SELECT count(*) FROM promotion WHERE source_extra ? CAST(:key AS text)"
        ),
        {"key": key},
    ).scalar_one()
    _log(f"「{key}」超过 {max_len} 字、原样留在 source_extra {kept} 条")


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("promotion", sa.Column("receiver_name", sa.String(32), nullable=True))
    op.add_column("promotion", sa.Column("receiver_phone", sa.String(32), nullable=True))
    op.add_column("promotion", sa.Column("receiver_address", sa.String(255), nullable=True))
    op.add_column("promotion", sa.Column("ship_status", sa.String(8), nullable=True))
    op.add_column(
        "promotion", sa.Column("ship_pushed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "promotion",
        sa.Column(
            "ship_pushed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("promotion", sa.Column("ship_courier", sa.String(16), nullable=True))
    op.add_column("promotion", sa.Column("ship_waybill", sa.String(128), nullable=True))
    op.add_column("promotion", sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True))
    _log("promotion 加 9 列")

    op.create_table(
        "promotion_item",
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
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_promotion_item_style",
        "promotion_item",
        ["tenant_id", "promotion_id", "style_id"],
        unique=True,
    )
    # SKU 删除前的引用校验用
    op.create_index("idx_promotion_item_sku", "promotion_item", ["tenant_id", "sku_id"])
    op.execute(enable_rls_sql("promotion_item"))
    _log("promotion_item 表已创建（含 RLS 与 2 个索引）")

    _move_key(bind, key=_ADDRESS_KEY, column="receiver_address", max_len=255)
    _move_key(bind, key=_WAYBILL_KEY, column="ship_waybill", max_len=128)

    shipped = bind.execute(
        sa.text("UPDATE promotion SET ship_status = '已发货' WHERE ship_waybill IS NOT NULL")
    )
    _log(f"ship_status = 已发货 {shipped.rowcount or 0} 条（有快递单号）")
    printing = bind.execute(
        sa.text(
            "UPDATE promotion SET ship_status = '待打单' "
            "WHERE ship_waybill IS NULL AND receiver_address IS NOT NULL"
        )
    )
    _log(f"ship_status = 待打单 {printing.rowcount or 0} 条（有地址、没单号）")
    pending = bind.execute(
        sa.text(
            """
            UPDATE promotion p SET ship_status = '待发货'
            WHERE p.ship_status IS NULL AND p.is_active
              AND p.publish_status = '未发布' AND p.recall_status = '未召回'
              AND EXISTS (SELECT 1 FROM negotiation n WHERE n.promotion_id = p.id)
            """
        )
    )
    _log(f"ship_status = 待发货 {pending.rowcount or 0} 条（谈款生成、未发布未召回）")

    # 回填只写 3 个合法值，CHECK 放在回填之后
    op.create_check_constraint(
        "ck_promotion_ship_status",
        "promotion",
        "ship_status IS NULL OR ship_status IN ('待发货', '待打单', '已发货')",
    )
    op.drop_index("idx_promotion_waybill", table_name="promotion")
    op.drop_index("idx_promotion_print_address", table_name="promotion")
    op.create_index(
        "idx_promotion_ship_status",
        "promotion",
        [
            "tenant_id",
            "ship_status",
            sa.text("cooperation_date DESC"),
            sa.text("created_at DESC"),
        ],
        postgresql_where=sa.text("ship_status IS NOT NULL"),
    )
    _log("索引：删 idx_promotion_print_address / idx_promotion_waybill，建 idx_promotion_ship_status")

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
        f"角色授权 {granted} 条"
    )
    for role_code, scopes in _MATRIX.items():
        _log(f"  - {role_code}: {', '.join(scopes)}")

    revoked = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE "
            "role_id IN (SELECT id FROM role WHERE code = 'warehouse') AND "
            "permission_id IN (SELECT id FROM permission WHERE scope = :scope)"
        ),
        {"scope": _REVOKED_WAREHOUSE_SCOPE},
    )
    _log(f"收回 warehouse 的 {_REVOKED_WAREHOUSE_SCOPE} {revoked.rowcount or 0} 条")


def downgrade() -> None:
    bind = op.get_bind()

    # typed 列写回 JSONB（只写还没有这个键的行：超长没搬走的原值还在，不覆盖）
    address_back = bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET source_extra = COALESCE(source_extra, '{}'::jsonb) || jsonb_build_object(
                CAST(:key AS text), concat_ws(' ', receiver_name, receiver_phone, receiver_address)
            )
            WHERE concat_ws(' ', receiver_name, receiver_phone, receiver_address) <> ''
              AND NOT (COALESCE(source_extra, '{}'::jsonb) ? CAST(:key AS text))
            """
        ),
        {"key": _ADDRESS_KEY},
    )
    _log(f"收件信息写回「{_ADDRESS_KEY}」{address_back.rowcount or 0} 条")
    waybill_back = bind.execute(
        sa.text(
            """
            UPDATE promotion
            SET source_extra = COALESCE(source_extra, '{}'::jsonb)
                || jsonb_build_object(CAST(:key AS text), ship_waybill)
            WHERE ship_waybill IS NOT NULL
              AND NOT (COALESCE(source_extra, '{}'::jsonb) ? CAST(:key AS text))
            """
        ),
        {"key": _WAYBILL_KEY},
    )
    _log(f"ship_waybill 写回「{_WAYBILL_KEY}」{waybill_back.rowcount or 0} 条")

    op.drop_index("idx_promotion_ship_status", table_name="promotion")
    op.create_index(
        "idx_promotion_print_address",
        "promotion",
        ["tenant_id", sa.text("cooperation_date DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text(_PRINT_ADDRESS_PREDICATE),
    )
    op.create_index(
        "idx_promotion_waybill",
        "promotion",
        ["tenant_id", sa.text("cooperation_date DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text(_WAYBILL_PREDICATE),
    )
    op.drop_constraint("ck_promotion_ship_status", "promotion", type_="check")

    op.execute(disable_rls_sql("promotion_item"))
    op.drop_table("promotion_item")
    for column in (
        "shipped_at",
        "ship_waybill",
        "ship_courier",
        "ship_pushed_by",
        "ship_pushed_at",
        "ship_status",
        "receiver_address",
        "receiver_phone",
        "receiver_name",
    ):
        op.drop_column("promotion", column)

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

    restored = bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT :id, r.id, p.id FROM role r, permission p "
            "WHERE r.code = 'warehouse' AND p.scope = :scope "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"id": str(uuid4()), "scope": _REVOKED_WAREHOUSE_SCOPE},
    )
    _log(f"补回 warehouse 的 {_REVOKED_WAREHOUSE_SCOPE} {restored.rowcount or 0} 条")
