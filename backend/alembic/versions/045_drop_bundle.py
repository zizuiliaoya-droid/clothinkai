"""删除 bundle_product / bundle_item 与 product.bundle 权限。

U17 做的这套「套装」从来没有接通：``bundle_api`` 的 router 从未注册到 app
（``tests/api`` 里甚至有一条断言守着 ``/api/bundles`` 不出现在 OpenAPI 里），
``split_quantities`` 只在它自己的测试里被调用过，两张表在生产都是 0 行。

而「套装」这个概念现在由 ``goods_main.is_suit`` + ``goods_style_item`` 承担：
那套是报表归属的主体，有成本、有软删留痕、有平台链接。留着 bundle 只会让「套装」
在代码和界面上有两个互不相干的含义。

两者的粒度确实不同 —— bundle 是「SKU × 数量」，goods_style_item 是「款式 + 成本」。
真要做「卖一套出 2 件 A + 1 件 B」的库存拆分，给 ``goods_style_item`` 加一列 quantity
比维护两套套装模型简单得多。

``report.export:read`` 是 021 一起 seed 的，仍在使用，这里不动。

Revision ID: 045_drop_bundle
Revises: 044_goods_perm
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "045_drop_bundle"
down_revision: str | Sequence[str] | None = "044_goods_perm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPES: list[str] = ["product.bundle:read", "product.bundle:write"]

# downgrade 要连 merchandiser 的绑定一起还原，保持与 021 之后一致。
_GRANTS: list[tuple[str, str, str]] = [
    ("merchandiser", "product.bundle:read", "查询套装/组合商品"),
    ("merchandiser", "product.bundle:write", "创建/编辑套装"),
]


def _log(msg: str) -> None:
    print(f"[045] {msg}")


def _base_cols() -> list:
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    bind = op.get_bind()

    # 先确认真的没数据。有数据说明这套东西在某处被用起来了，那就不该在这里悄悄删掉。
    for table in ("bundle_item", "bundle_product"):
        rows = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()  # noqa: S608
        _log(f"{table} 现有 {rows} 行")
        if rows:
            raise RuntimeError(
                f"{table} 还有 {rows} 行数据，拒绝删表。"
                "请先确认这些数据是否需要迁移到 goods_main / goods_style_item。"
            )

    unbound = bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": _SCOPES},
    )
    dropped = bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": _SCOPES},
    )
    _log(f"权限清理：解绑 {unbound.rowcount or 0} 条，删除 permission {dropped.rowcount or 0} 条")

    op.execute(disable_rls_sql("bundle_item"))
    op.execute(disable_rls_sql("bundle_product"))
    op.drop_table("bundle_item")
    op.drop_table("bundle_product")
    _log("已删除 bundle_item / bundle_product（套装改由 goods_main.is_suit 承担）")


def downgrade() -> None:
    op.create_table(
        "bundle_product",
        *_base_cols(),
        sa.Column("bundle_code", sa.String(64), nullable=False),
        sa.Column("bundle_name", sa.String(255), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index(
        "uq_bundle_product_code", "bundle_product", ["tenant_id", "bundle_code"], unique=True
    )

    op.create_table(
        "bundle_item",
        *_base_cols(),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["bundle_product.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("quantity >= 1", name="ck_bundle_item_quantity_pos"),
    )
    op.create_index(
        "uq_bundle_item_sku", "bundle_item", ["tenant_id", "bundle_id", "sku_id"], unique=True
    )
    op.create_index("idx_bundle_item_bundle", "bundle_item", ["tenant_id", "bundle_id"])

    op.execute(enable_rls_sql("bundle_product"))
    op.execute(enable_rls_sql("bundle_item"))

    bind = op.get_bind()
    for role_code, scope, name in _GRANTS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )
        bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT :id, r.id, p.id FROM role r, permission p "
                "WHERE r.code = :role_code AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"id": str(uuid4()), "role_code": role_code, "scope": scope},
        )
