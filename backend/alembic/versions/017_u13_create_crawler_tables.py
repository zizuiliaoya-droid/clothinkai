"""U13 - 创建采集表（worker_token/crawler_task/data_quality_issue/qianniu_daily/ad_daily）+ scope seed

Revision ID: 017_u13_create_crawler_tables
Revises: 016_u12_create_credential
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "017_u13_create_crawler_tables"
down_revision: str | Sequence[str] | None = "016_u12_create_credential"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "worker_token",
    "crawler_task",
    "data_quality_issue",
    "qianniu_daily",
    "ad_daily",
)


def _base_cols() -> list:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    op.create_table(
        "worker_token",
        *_base_cols(),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("ip_allowlist", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("consecutive_auth_failures", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_worker_token_hash", "worker_token",
                    ["tenant_id", "token_hash"], unique=True)
    op.create_index("idx_worker_token_active", "worker_token", ["tenant_id", "is_active"])

    op.create_table(
        "crawler_task",
        *_base_cols(),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("worker_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cred_token", sa.String(64), nullable=True),
        sa.Column("cred_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_reason", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.ForeignKeyConstraint(["credential_id"], ["credential.id"], ondelete="CASCADE",
                                name="fk_crawler_task_credential"),
        sa.ForeignKeyConstraint(["worker_token_id"], ["worker_token.id"], ondelete="SET NULL",
                                name="fk_crawler_task_worker_token"),
        sa.CheckConstraint("status IN ('pending','assigned','exchanged','success','failed')",
                           name="ck_crawler_task_status"),
    )
    op.create_index("uq_crawler_task_tenant_plat_cred_date", "crawler_task",
                    ["tenant_id", "platform", "credential_id", "target_date"], unique=True)
    op.create_index("idx_crawler_task_status", "crawler_task", ["tenant_id", "status"])

    op.create_table(
        "data_quality_issue",
        *_base_cols(),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False, server_default=sa.text("'open'")),
        sa.Column("entity_type", sa.String(32), nullable=True),
        sa.Column("entity_ref", sa.String(128), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.CheckConstraint("severity IN ('info','warning','error')", name="ck_dq_severity"),
        sa.CheckConstraint("status IN ('open','fixed','ignored')", name="ck_dq_status"),
    )
    op.create_index("idx_dq_tenant_source_sev", "data_quality_issue",
                    ["tenant_id", "source", "severity"])
    op.create_index("idx_dq_tenant_status", "data_quality_issue", ["tenant_id", "status"])

    for tbl, cols in (
        ("qianniu_daily", [
            sa.Column("visitors", sa.Integer(), nullable=True),
            sa.Column("pay_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("pay_orders", sa.Integer(), nullable=True),
        ]),
        ("ad_daily", [
            sa.Column("cost", sa.Numeric(12, 2), nullable=True),
            sa.Column("impressions", sa.Integer(), nullable=True),
            sa.Column("clicks", sa.Integer(), nullable=True),
            sa.Column("gmv", sa.Numeric(12, 2), nullable=True),
        ]),
    ):
        op.create_table(
            tbl,
            *_base_cols(),
            sa.Column("platform_product_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("platform_id_snapshot", sa.String(64), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            *cols,
            sa.Column("extra", postgresql.JSONB(), nullable=True),
            sa.ForeignKeyConstraint(["platform_product_id"], ["platform_product.id"],
                                    ondelete="SET NULL", name=f"fk_{tbl}_platform_product"),
        )
        op.create_index(f"uq_{tbl}_tenant_pid_date", tbl,
                        ["tenant_id", "platform_id_snapshot", "date"], unique=True)
        op.create_index(f"idx_{tbl}_date", tbl, ["tenant_id", "date"])

    for tbl in _TABLES:
        op.execute(enable_rls_sql(tbl))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    for tbl in _TABLES:
        op.execute(disable_rls_sql(tbl))
    op.drop_table("ad_daily")
    op.drop_table("qianniu_daily")
    op.drop_table("data_quality_issue")
    op.drop_table("crawler_task")
    op.drop_table("worker_token")


_PERMISSIONS = [
    ("crawler.worker:write", "签发/吊销采集 Worker Token", "function"),
    ("crawler.task:read", "查看采集任务", "function"),
    ("data_quality:read", "查看数据质量看板", "function"),
    ("data_quality:write", "处理数据质量异常", "function"),
]

_MATRIX = {
    "admin": ["crawler.worker:write", "crawler.task:read",
              "data_quality:read", "data_quality:write"],
    "operations": ["data_quality:read"],
}


def _seed_permissions() -> None:
    bind = op.get_bind()
    for scope, name, category in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, :category, NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name, "category": category},
        )
    for role_code, scope_list in _MATRIX.items():
        for scope in scope_list:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )


def _downgrade_permissions() -> None:
    bind = op.get_bind()
    scopes = [s for s, _, _ in _PERMISSIONS]
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
