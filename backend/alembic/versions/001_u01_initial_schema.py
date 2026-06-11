"""U01 - 初始 schema

Revision ID: 001_u01_initial_schema
Revises:
Create Date: 2026-05-24 04:00:00.000000

"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_u01_initial_schema"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # RLS 角色（幂等创建）—— migration 自包含，不依赖 init/001_create_roles.sql
    #
    # 背景：CI / pytest 在 bare postgres 上跑 `alembic upgrade head`，未挂载
    # docker-compose 的 init 脚本。migration 002 的 CREATE POLICY ... TO
    # clothing_app 与 audit_log GRANT 需要这些角色已存在，否则报
    # "role clothing_app does not exist"。此处幂等补建保证任何环境一致。
    #
    # 生产环境密码请在部署后通过 ALTER ROLE 替换（与 init 脚本一致）。
    # ------------------------------------------------------------------ #
    op.execute(
        """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_app') THEN
        CREATE ROLE clothing_app NOINHERIT LOGIN PASSWORD 'app_password_change_me';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_bypass') THEN
        CREATE ROLE clothing_bypass BYPASSRLS NOINHERIT LOGIN PASSWORD 'bypass_password_change_me';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'clothing_archiver') THEN
        CREATE ROLE clothing_archiver NOINHERIT LOGIN PASSWORD 'archiver_password_change_me';
    END IF;
END
$$;
"""
    )
    op.execute("GRANT USAGE ON SCHEMA public TO clothing_app, clothing_bypass, clothing_archiver")

    # ------------------------------------------------------------------ #
    # tenant
    # ------------------------------------------------------------------ #
    op.create_table(
        "tenant",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("max_storage_mb", sa.Integer(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("code", name="uq_tenant_code"),
    )

    # ------------------------------------------------------------------ #
    # role / permission / role_permission
    # ------------------------------------------------------------------ #
    op.create_table(
        "role",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.UniqueConstraint("code", name="uq_role_code"),
    )

    op.create_table(
        "permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="function"),
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
        sa.UniqueConstraint("scope", name="uq_permission_scope"),
    )

    op.create_table(
        "role_permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], ondelete="CASCADE", name="fk_role_permission_role"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            ondelete="CASCADE",
            name="fk_role_permission_permission",
        ),
        sa.UniqueConstraint("role_id", "permission_id", name="uq_role_permission_role_perm"),
    )

    # ------------------------------------------------------------------ #
    # user
    # ------------------------------------------------------------------ #
    op.create_table(
        "user",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(64), nullable=True),
        sa.Column("email", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column(
            "password_must_change", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "password_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_user_tenant"
        ),
        sa.UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),
    )
    op.create_index("ix_user_tenant_id", "user", ["tenant_id"])
    op.create_index(
        "ix_user_locked",
        "user",
        ["tenant_id", "locked_at"],
        postgresql_where=sa.text("locked_at IS NOT NULL"),
    )

    # ------------------------------------------------------------------ #
    # user_role / user_permission_override
    # ------------------------------------------------------------------ #
    op.create_table(
        "user_role",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_user_role_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name="fk_user_role_user"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["role.id"], ondelete="RESTRICT", name="fk_user_role_role"
        ),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "role_id", name="uq_user_role_tenant_user_role"
        ),
    )
    op.create_index("ix_user_role_tenant_id", "user_role", ["tenant_id"])

    op.create_table(
        "user_permission_override",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("effect", sa.String(8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            ondelete="RESTRICT",
            name="fk_user_permission_override_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name="fk_upo_user"
        ),
        sa.ForeignKeyConstraint(
            ["permission_id"],
            ["permission.id"],
            ondelete="RESTRICT",
            name="fk_upo_permission",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "permission_id",
            name="uq_user_permission_override_tenant_user_perm",
        ),
        sa.CheckConstraint("effect IN ('grant', 'revoke')", name="ck_upo_effect"),
    )
    op.create_index(
        "ix_user_permission_override_tenant_id", "user_permission_override", ["tenant_id"]
    )

    # ------------------------------------------------------------------ #
    # refresh_token
    # ------------------------------------------------------------------ #
    op.create_table(
        "refresh_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            ondelete="RESTRICT",
            name="fk_refresh_token_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE", name="fk_refresh_token_user"
        ),
        sa.UniqueConstraint("jti", name="uq_refresh_token_jti"),
    )
    op.create_index("ix_refresh_token_tenant_id", "refresh_token", ["tenant_id"])
    op.create_index(
        "ix_refresh_token_user_revoked", "refresh_token", ["user_id", "revoked_at"]
    )
    op.create_index(
        "ix_refresh_token_active_expiry",
        "refresh_token",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # audit_log（特殊：BIGSERIAL，无 deleted_at，REVOKE UPDATE/DELETE 在 002 启用）
    # ------------------------------------------------------------------ #
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("purpose", sa.String(128), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(256), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_log_tenant_created", "audit_log", ["tenant_id", "created_at"])
    op.create_index(
        "ix_audit_log_tenant_action_created",
        "audit_log",
        ["tenant_id", "action", "created_at"],
    )
    op.create_index(
        "ix_audit_log_tenant_user_created",
        "audit_log",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_index("ix_audit_log_created", "audit_log", ["created_at"])

    # ------------------------------------------------------------------ #
    # backup_record（系统级，无 tenant_id）
    # ------------------------------------------------------------------ #
    op.create_table(
        "backup_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("backup_type", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("includes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("r2_key", sa.String(256), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retention_until", sa.Date(), nullable=True),
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
    )
    op.create_index(
        "ix_backup_record_retention",
        "backup_record",
        ["retention_until"],
        postgresql_where=sa.text("r2_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_backup_record_retention", table_name="backup_record")
    op.drop_table("backup_record")

    op.drop_index("ix_audit_log_created", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_user_created", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_action_created", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_created", table_name="audit_log")
    op.drop_table("audit_log")

    op.drop_index("ix_refresh_token_active_expiry", table_name="refresh_token")
    op.drop_index("ix_refresh_token_user_revoked", table_name="refresh_token")
    op.drop_index("ix_refresh_token_tenant_id", table_name="refresh_token")
    op.drop_table("refresh_token")

    op.drop_index(
        "ix_user_permission_override_tenant_id", table_name="user_permission_override"
    )
    op.drop_table("user_permission_override")

    op.drop_index("ix_user_role_tenant_id", table_name="user_role")
    op.drop_table("user_role")

    op.drop_index("ix_user_locked", table_name="user")
    op.drop_index("ix_user_tenant_id", table_name="user")
    op.drop_table("user")

    op.drop_table("role_permission")
    op.drop_table("permission")
    op.drop_table("role")
    op.drop_table("tenant")
