"""U01 - 种子初始数据：default tenant + 10 个预设角色 + permission + role_permission 矩阵

Revision ID: 003_u01_seed_initial_data
Revises: 002_u01_enable_rls
Create Date: 2026-05-24 04:02:00.000000

幂等：使用 ON CONFLICT 跳过已存在记录。
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003_u01_seed_initial_data"
down_revision: str | Sequence[str] | None = "002_u01_enable_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------ #
    # 1) default tenant
    # ------------------------------------------------------------------ #
    bind.execute(
        sa.text(
            """
INSERT INTO tenant (id, code, name, status, created_at, updated_at)
VALUES (:id, 'default', '默认租户', 'active', NOW(), NOW())
ON CONFLICT (code) DO NOTHING
"""
        ),
        {"id": str(uuid4())},
    )

    # ------------------------------------------------------------------ #
    # 2) 10 个预设角色（与 default_roles.py 的 RoleSpec 一致）
    # ------------------------------------------------------------------ #
    roles = [
        ("admin", "管理员", "系统超级管理员，拥有所有权限"),
        ("platform_admin", "平台管理员", "跨租户的超级管理员（不属于任何 tenant）"),
        ("designer", "设计师", "负责设计稿与面辅料填写"),
        ("design_assistant", "设计助理", "负责面辅料补齐、核价信息填写"),
        ("pattern_maker", "版师", "负责制版与放码"),
        ("merchandiser", "跟单", "负责工艺录入、商品成本表、核价审批"),
        ("pr", "PR", "负责站外推广录入与博主维护"),
        ("pr_manager", "PR 主管", "PR 全部权限 + 财务结款核查 + 增加结算项"),
        ("finance", "财务", "负责付款、拍单、刷单、余额核对"),
        ("operations", "运营", "只读访问报表与店铺数据"),
    ]
    for code, name, description in roles:
        bind.execute(
            sa.text(
                """
INSERT INTO role (id, code, name, description, is_system, created_at, updated_at)
VALUES (:id, :code, :name, :description, true, NOW(), NOW())
ON CONFLICT (code) DO NOTHING
"""
            ),
            {"id": str(uuid4()), "code": code, "name": name, "description": description},
        )

    # ------------------------------------------------------------------ #
    # 3) permission 全集（含通配符 *、auth.* 系列、业务模块通配符占位）
    # ------------------------------------------------------------------ #
    permissions = [
        ("*", "所有权限", "function"),
        # auth
        ("auth.user:read", "auth.user 读取", "function"),
        ("auth.user:write", "auth.user 写入", "function"),
        ("auth.user:delete", "auth.user 删除", "function"),
        ("auth.role:assign", "auth.role 分配", "function"),
        ("auth.permission:grant", "auth.permission 授予", "function"),
        ("auth.audit:read", "auth.audit 读取", "function"),
        # 业务模块通配符（U01 占位，后续单元启用）
        ("product.*:*", "product 全部", "function"),
        ("product.*:read", "product 只读", "function"),
        ("design.*:*", "design 全部", "function"),
        ("design.*:read", "design 只读", "function"),
        ("design.costing:write", "design.costing 写入", "function"),
        ("design.craft:write", "design.craft 写入", "function"),
        ("design.tag_price:write", "design.tag_price 写入", "function"),
        ("design.confirm_price:approve", "design.confirm_price 审批", "function"),
        ("design.pattern:read", "design.pattern 读取", "function"),
        ("design.pattern:write", "design.pattern 写入", "function"),
        ("blogger.*:*", "blogger 全部", "function"),
        ("blogger.*:read", "blogger 只读", "function"),
        ("promotion.*:*", "promotion 全部", "function"),
        ("promotion.*:read", "promotion 只读", "function"),
        ("promotion.review:approve", "promotion 审核", "function"),
        ("finance.*:*", "finance 全部", "function"),
        ("finance.*:read", "finance 只读", "function"),
        ("finance.settlement:read", "finance.settlement 读取", "function"),
        ("finance.settlement:write", "finance.settlement 写入", "function"),
        ("finance.settlement:approve", "finance.settlement 审核", "function"),
        ("finance.settlement:pay", "finance.settlement 付款", "function"),
        ("finance.settlement_extra_item:write", "finance.settlement 额外项写入", "function"),
        ("finance.order_adjustment:write", "finance.order_adjustment 写入", "function"),
        ("finance.balance:write", "finance.balance 写入", "function"),
        ("report.*:read", "report 只读", "function"),
        ("report.publish_progress:read", "report.publish_progress 只读", "function"),
        ("wecom.*:*", "wecom 全部", "function"),
        ("importer.*:*", "importer 全部", "function"),
        ("importer.*:read", "importer 只读", "function"),
    ]
    for scope, name, category in permissions:
        bind.execute(
            sa.text(
                """
INSERT INTO permission (id, scope, name, category, created_at, updated_at)
VALUES (:id, :scope, :name, :category, NOW(), NOW())
ON CONFLICT (scope) DO NOTHING
"""
            ),
            {"id": str(uuid4()), "scope": scope, "name": name, "category": category},
        )

    # ------------------------------------------------------------------ #
    # 4) role_permission 矩阵
    # ------------------------------------------------------------------ #
    matrix = {
        "admin": ["*"],
        "platform_admin": ["*"],
        "designer": ["design.*:*", "product.*:read"],
        "design_assistant": ["design.*:*", "design.costing:write", "product.*:read"],
        "pattern_maker": ["design.pattern:read", "design.pattern:write"],
        "merchandiser": [
            "product.*:*",
            "design.craft:write",
            "design.tag_price:write",
            "design.confirm_price:approve",
        ],
        "pr": [
            "promotion.*:*",
            "blogger.*:*",
            "report.publish_progress:read",
        ],
        "pr_manager": [
            "promotion.*:*",
            "blogger.*:*",
            "promotion.review:approve",
            "finance.settlement:approve",
            "finance.settlement:read",
            "finance.settlement:write",
            "finance.settlement_extra_item:write",
            "report.*:read",
        ],
        "finance": [
            "finance.settlement:pay",
            "finance.settlement:read",
            "finance.order_adjustment:write",
            "finance.balance:write",
        ],
        "operations": [
            "report.*:read",
            "promotion.*:read",
            "blogger.*:read",
            "product.*:read",
            "importer.*:read",
        ],
    }

    for role_code, scope_list in matrix.items():
        for scope in scope_list:
            bind.execute(
                sa.text(
                    """
INSERT INTO role_permission (id, role_id, permission_id)
SELECT :id, r.id, p.id
FROM role r, permission p
WHERE r.code = :role_code AND p.scope = :scope
ON CONFLICT (role_id, permission_id) DO NOTHING
"""
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission")
    op.execute("DELETE FROM permission")
    op.execute("DELETE FROM role WHERE is_system = true")
    op.execute("DELETE FROM tenant WHERE code = 'default'")
