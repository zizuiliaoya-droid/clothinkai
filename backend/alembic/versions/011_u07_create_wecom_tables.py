"""U07 - 创建企微集成表（wecom_config / wecom_contact / message_template /
wecom_message / notification）+ 权限 seed

Revision ID: 011_u07_create_wecom_tables
Revises: 010_u06a_create_import_tables
Create Date: 2026-06-07

5 张表（均继承 TenantScopedModel → tenant_id + RLS）：
- wecom_config：企微自建应用配置。UNIQUE(tenant_id)。secret_ciphertext bytea（AESGCM 密文）。
- wecom_contact：博主外部联系人绑定。UNIQUE(tenant_id, blogger_id) + idx external。
- message_template：催发模板。UNIQUE(tenant_id, template_type)。
- wecom_message：群发消息记录。频控复合索引（tenant,blogger,created_at）/(tenant,pr,created_at)
  + idx(tenant,status) + idx(wecom_msgid)。无 is_active（永久留痕）。
- notification：站内通知。idx(tenant,user,is_read,created_at)。

5 RLS 策略。permission seed（幂等）：wecom.config:write / wecom.bind:write /
wecom.template:write / wecom.message:read / notification:read + 角色映射。
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "011_u07_create_wecom_tables"
down_revision: str | Sequence[str] | None = "010_u06a_create_import_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _upgrade_tables()
    _seed_permissions()


def downgrade() -> None:
    _downgrade_tables()


def _ts_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _upgrade_tables() -> None:
    # 1) wecom_config
    op.create_table(
        "wecom_config",
        *_ts_columns(),
        sa.Column("corp_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("callback_token", sa.String(64), nullable=True),
        sa.Column("callback_aes_key", sa.String(64), nullable=True),
        sa.Column("default_sender_userid", sa.String(64), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False,
            server_default=sa.text("true"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_wecom_config_tenant",
        ),
    )
    op.create_index(
        "uq_wecom_config_tenant", "wecom_config", ["tenant_id"], unique=True
    )

    # 2) wecom_contact
    op.create_table(
        "wecom_contact",
        *_ts_columns(),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_userid", sa.String(128), nullable=False),
        sa.Column("matched_wechat", sa.String(64), nullable=True),
        sa.Column("bound_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_wecom_contact_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["blogger_id"], ["blogger.id"], ondelete="CASCADE",
            name="fk_wecom_contact_blogger",
        ),
        sa.ForeignKeyConstraint(
            ["bound_by"], ["user.id"], ondelete="SET NULL",
            name="fk_wecom_contact_bound_by",
        ),
    )
    op.create_index(
        "uq_wecom_contact_blogger", "wecom_contact",
        ["tenant_id", "blogger_id"], unique=True,
    )
    op.create_index(
        "idx_wecom_contact_external", "wecom_contact",
        ["tenant_id", "external_userid"],
    )

    # 3) message_template
    op.create_table(
        "message_template",
        *_ts_columns(),
        sa.Column("template_type", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_message_template_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["user.id"], ondelete="SET NULL",
            name="fk_message_template_updated_by",
        ),
    )
    op.create_index(
        "uq_message_template_type", "message_template",
        ["tenant_id", "template_type"], unique=True,
    )

    # 4) wecom_message
    op.create_table(
        "wecom_message",
        *_ts_columns(),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("external_userid", sa.String(128), nullable=True),
        sa.Column("template_type", sa.String(16), nullable=False),
        sa.Column("rendered_content", sa.Text(), nullable=False),
        sa.Column(
            "promotion_ids", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status", sa.String(16), nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("wecom_msgid", sa.String(128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_wecom_message_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["blogger_id"], ["blogger.id"], ondelete="RESTRICT",
            name="fk_wecom_message_blogger",
        ),
        sa.ForeignKeyConstraint(
            ["pr_id"], ["user.id"], ondelete="SET NULL",
            name="fk_wecom_message_pr",
        ),
        sa.CheckConstraint(
            "status IN ('pending','created','sent','rejected',"
            "'rate_limited','failed')",
            name="ck_wecom_message_status",
        ),
    )
    op.create_index(
        "idx_wecom_message_blogger", "wecom_message",
        ["tenant_id", "blogger_id", "created_at"],
    )
    op.create_index(
        "idx_wecom_message_pr", "wecom_message",
        ["tenant_id", "pr_id", "created_at"],
    )
    op.create_index(
        "idx_wecom_message_status", "wecom_message", ["tenant_id", "status"]
    )
    op.create_index(
        "idx_wecom_message_msgid", "wecom_message", ["wecom_msgid"]
    )

    # 5) notification
    op.create_table(
        "notification",
        *_ts_columns(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("link", sa.String(255), nullable=True),
        sa.Column(
            "is_read", sa.Boolean(), nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_notification_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE",
            name="fk_notification_user",
        ),
    )
    op.create_index(
        "idx_notification_user", "notification",
        ["tenant_id", "user_id", "is_read", "created_at"],
    )

    # 6) RLS（5 表均启用）
    for tbl in (
        "wecom_config", "wecom_contact", "message_template",
        "wecom_message", "notification",
    ):
        op.execute(enable_rls_sql(tbl))


def _downgrade_tables() -> None:
    for tbl in (
        "notification", "wecom_message", "message_template",
        "wecom_contact", "wecom_config",
    ):
        op.execute(disable_rls_sql(tbl))
    op.drop_table("notification")
    op.drop_table("wecom_message")
    op.drop_table("message_template")
    op.drop_table("wecom_contact")
    op.drop_table("wecom_config")


def _seed_permissions() -> None:
    """新增 wecom.* + notification 权限 + 角色映射（幂等）。

    - wecom.config:write    → admin(*)
    - wecom.bind:write      → admin(*) / pr / pr_manager
    - wecom.template:write  → admin(*)
    - wecom.message:read    → admin(*) / pr / pr_manager / operations
    - notification:read     → admin(*) / pr / pr_manager / operations / finance / merchandiser

    admin/platform_admin 持 '*' 通配，无需显式关联；其余角色显式补关联。
    """
    bind = op.get_bind()

    permissions = [
        ("wecom.config:write", "配置企微自建应用", "function"),
        ("wecom.bind:write", "绑定博主企微外部联系人", "function"),
        ("wecom.template:write", "编辑催发消息模板", "function"),
        ("wecom.message:read", "查询企微消息记录", "function"),
        ("notification:read", "查询本人站内通知", "function"),
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

    matrix = {
        "pr": ["wecom.bind:write", "wecom.message:read", "notification:read"],
        "pr_manager": [
            "wecom.bind:write",
            "wecom.message:read",
            "notification:read",
        ],
        "operations": ["wecom.message:read", "notification:read"],
        "finance": ["notification:read"],
        "merchandiser": ["notification:read"],
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
