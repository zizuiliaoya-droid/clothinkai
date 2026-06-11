"""U01 - 启用 RLS + audit_log REVOKE

Revision ID: 002_u01_enable_rls
Revises: 001_u01_initial_schema
Create Date: 2026-05-24 04:01:00.000000

依赖 init/001_create_roles.sql 已经在 PG 实例中创建过：
    clothing_app / clothing_bypass / clothing_archiver

策略：
- 所有 TenantScopedModel 表启用 RLS
- audit_log REVOKE UPDATE/DELETE FROM clothing_app（append-only）

"""

from __future__ import annotations

from typing import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_u01_enable_rls"
down_revision: str | Sequence[str] | None = "001_u01_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 启用 RLS 的业务表（继承 TenantScopedModel）
TENANT_SCOPED_TABLES = (
    "user",
    "user_role",
    "user_permission_override",
    "refresh_token",
)


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 业务表启用 RLS
    # ------------------------------------------------------------------ #
    for table in TENANT_SCOPED_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
CREATE POLICY tenant_isolation ON "{table}"
    FOR ALL
    TO clothing_app
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
"""
        )

    # ------------------------------------------------------------------ #
    # audit_log append-only：REVOKE UPDATE/DELETE FROM clothing_app
    # 归档专用 clothing_archiver 拥有 DELETE
    # ------------------------------------------------------------------ #
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM clothing_app")
    op.execute("GRANT SELECT, INSERT ON audit_log TO clothing_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO clothing_app")
    op.execute("GRANT SELECT, DELETE ON audit_log TO clothing_archiver")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO clothing_archiver")

    # bypass / archiver 仍按 default privilege 持有读写
    # （创建角色脚本中已 GRANT SELECT/INSERT/UPDATE/DELETE ON ALL TABLES）


def downgrade() -> None:
    # 恢复 audit_log 权限
    op.execute("GRANT UPDATE, DELETE ON audit_log TO clothing_app")
    op.execute("REVOKE SELECT, DELETE ON audit_log FROM clothing_archiver")

    # 移除 RLS
    for table in TENANT_SCOPED_TABLES:
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
