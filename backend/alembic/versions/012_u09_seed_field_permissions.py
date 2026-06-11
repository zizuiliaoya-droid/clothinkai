"""U09 - seed 字段级权限 scope 定义（不绑角色）

Revision ID: 012_u09_seed_field_permissions
Revises: 011_u07_create_wecom_tables
Create Date: 2026-06-07

仅向 permission 表 INSERT 18 个字段 scope（category='field'）：
- field.sku.cost_price:read|write / field.sku.purchase_price:read|write
- field.blogger.quote:read|write / wechat:read|write / phone:read|write
- field.promotion.quote_amount:read|write / cost_snapshot:read|write
- field.settlement.amount:read / total_amount:read（仅读，写由状态机控制）
- field.settlement.payment_amount:read|write

幂等 ON CONFLICT (scope) DO NOTHING；**不写 role_permission**（默认字段权限按
core 注册表角色判定，这些 scope 仅供自定义 grant/revoke 引用 + 存在性校验）。
无新表 / 无 DDL 变更。auth.permission:grant 已由 U01 seed，无需重复。
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "012_u09_seed_field_permissions"
down_revision: str | Sequence[str] | None = "011_u07_create_wecom_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FIELD_SCOPES: list[tuple[str, str]] = [
    ("field.sku.cost_price:read", "字段-SKU成本价-读"),
    ("field.sku.cost_price:write", "字段-SKU成本价-写"),
    ("field.sku.purchase_price:read", "字段-SKU采购价-读"),
    ("field.sku.purchase_price:write", "字段-SKU采购价-写"),
    ("field.blogger.quote:read", "字段-博主报价-读"),
    ("field.blogger.quote:write", "字段-博主报价-写"),
    ("field.blogger.wechat:read", "字段-博主微信-读"),
    ("field.blogger.wechat:write", "字段-博主微信-写"),
    ("field.blogger.phone:read", "字段-博主电话-读"),
    ("field.blogger.phone:write", "字段-博主电话-写"),
    ("field.promotion.quote_amount:read", "字段-推广报价-读"),
    ("field.promotion.quote_amount:write", "字段-推广报价-写"),
    ("field.promotion.cost_snapshot:read", "字段-推广成本快照-读"),
    ("field.promotion.cost_snapshot:write", "字段-推广成本快照-写"),
    ("field.settlement.amount:read", "字段-结算金额-读"),
    ("field.settlement.total_amount:read", "字段-结算总额-读"),
    ("field.settlement.payment_amount:read", "字段-结算实付-读"),
    ("field.settlement.payment_amount:write", "字段-结算实付-写"),
]


def upgrade() -> None:
    bind = op.get_bind()
    for scope, name in FIELD_SCOPES:
        bind.execute(
            sa.text(
                """
INSERT INTO permission (id, scope, name, category, created_at, updated_at)
VALUES (:id, :scope, :name, 'field', NOW(), NOW())
ON CONFLICT (scope) DO NOTHING
"""
            ),
            {"id": str(uuid4()), "scope": scope, "name": name},
        )


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [s for s, _ in FIELD_SCOPES]
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
