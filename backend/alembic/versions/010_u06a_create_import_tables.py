"""U06a - 创建统一导入框架表（import_batch / import_job / field_mapping）+ 权限 seed

Revision ID: 010_u06a_create_import_tables
Revises: 009_u05_seed_smoke_test_data
Create Date: 2026-06-04

3 张表（均继承 TenantScopedModel → tenant_id + RLS）：
- import_batch：导入批次。UNIQUE(tenant_id, source, file_hash)（NF-2 并发去重权威）
  + status / bucket / counts / retry CHECK。FK tenant_id + created_by。
- import_job：行级结果。UNIQUE(batch_id, row_number)（NF-3/FB-E 行幂等 + 重试原地更新）
  + status / attempt_count CHECK。FK tenant_id + batch_id(ON DELETE CASCADE)。
- field_mapping：映射版本。UNIQUE(tenant_id, source, version) + 部分 UNIQUE(tenant_id, source)
  WHERE is_active。FK tenant_id + created_by。

3 RLS 策略（enable_rls_sql 单 DO 块，asyncpg 兼容）。
permission seed（NF-5，幂等）：importer.batch:read/write + importer.mapping:write
  → 与 default_roles.py 同步（admin/* 覆盖；operations importer.*:read 覆盖 batch:read）。

不使用 attachment FK（FB-A）：import_batch.file_r2_key 直存 R2 key。
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "010_u06a_create_import_tables"
down_revision: str | Sequence[str] | None = "009_u05_seed_smoke_test_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _upgrade_tables()
    _seed_permissions()


def downgrade() -> None:
    _downgrade_tables()


# ===========================================================================
# 表创建
# ===========================================================================


def _upgrade_tables() -> None:
    # 1) import_batch
    op.create_table(
        "import_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_r2_key", sa.String(512), nullable=False),
        sa.Column(
            "file_bucket",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'private'"),
        ),
        sa.Column("mapping_version", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'processing'"),
        ),
        sa.Column(
            "total_rows", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "imported", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "failed", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
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
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_import_batch_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], ondelete="SET NULL",
            name="fk_import_batch_created_by",
        ),
        sa.CheckConstraint(
            "status IN ('processing','completed','partial','failed')",
            name="ck_import_batch_status",
        ),
        sa.CheckConstraint(
            "file_bucket IN ('public','private','credentials','backups')",
            name="ck_import_batch_bucket",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND imported >= 0 AND failed >= 0",
            name="ck_import_batch_counts_nonneg",
        ),
        sa.CheckConstraint(
            "retry_count >= 0 AND retry_count <= 3",
            name="ck_import_batch_retry",
        ),
    )
    # NF-2 并发去重权威（永久 UNIQUE）
    op.create_index(
        "uq_import_batch_hash",
        "import_batch",
        ["tenant_id", "source", "file_hash"],
        unique=True,
    )
    op.create_index(
        "idx_import_batch_tenant_status",
        "import_batch",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "idx_import_batch_source",
        "import_batch",
        ["tenant_id", "source", "created_at"],
    )

    # 2) import_job
    op.create_table(
        "import_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raw_data", postgresql.JSONB(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "target_resource_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
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
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_import_job_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["import_batch.id"], ondelete="CASCADE",
            name="fk_import_job_batch",
        ),
        sa.CheckConstraint(
            "status IN ('success','failed')", name="ck_import_job_status"
        ),
        sa.CheckConstraint("attempt_count >= 1", name="ck_import_job_attempt"),
    )
    # NF-3/FB-E 行幂等 + 重试原地更新定位（永久 UNIQUE）
    op.create_index(
        "uq_import_job_batch_row",
        "import_job",
        ["batch_id", "row_number"],
        unique=True,
    )
    op.create_index(
        "idx_import_job_batch_status",
        "import_job",
        ["tenant_id", "batch_id", "status"],
    )

    # 3) field_mapping
    op.create_table(
        "field_mapping",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mapping_config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
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
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_field_mapping_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], ondelete="SET NULL",
            name="fk_field_mapping_created_by",
        ),
        sa.CheckConstraint("version >= 1", name="ck_field_mapping_version"),
    )
    op.create_index(
        "uq_field_mapping_version",
        "field_mapping",
        ["tenant_id", "source", "version"],
        unique=True,
    )
    # 同 (tenant, source) 仅一个 active（部分唯一）
    op.create_index(
        "uq_field_mapping_active",
        "field_mapping",
        ["tenant_id", "source"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "idx_field_mapping_active",
        "field_mapping",
        ["tenant_id", "source", "is_active"],
    )

    # 4) RLS（3 表均启用）
    op.execute(enable_rls_sql("import_batch"))
    op.execute(enable_rls_sql("import_job"))
    op.execute(enable_rls_sql("field_mapping"))


def _downgrade_tables() -> None:
    op.execute(disable_rls_sql("field_mapping"))
    op.execute(disable_rls_sql("import_job"))
    op.execute(disable_rls_sql("import_batch"))

    op.drop_index("idx_field_mapping_active", table_name="field_mapping")
    op.drop_index("uq_field_mapping_active", table_name="field_mapping")
    op.drop_index("uq_field_mapping_version", table_name="field_mapping")
    op.drop_table("field_mapping")

    op.drop_index("idx_import_job_batch_status", table_name="import_job")
    op.drop_index("uq_import_job_batch_row", table_name="import_job")
    op.drop_table("import_job")

    op.drop_index("idx_import_batch_source", table_name="import_batch")
    op.drop_index("idx_import_batch_tenant_status", table_name="import_batch")
    op.drop_index("uq_import_batch_hash", table_name="import_batch")
    op.drop_table("import_batch")


# ===========================================================================
# 权限 seed（NF-5，幂等）
# ===========================================================================


def _seed_permissions() -> None:
    """新增 importer.batch / importer.mapping 细粒度权限 + 角色关联（幂等）。

    与 default_roles.py 同步：
    - importer.batch:read  → admin(*) / operations / pr / pr_manager
    - importer.batch:write → admin(*) / pr / pr_manager
    - importer.mapping:write → admin(*) / pr_manager

    注：admin/platform_admin 持 '*' 通配；operations 已有 importer.*:read（覆盖 batch:read）。
    本 seed 仍显式补 batch:read 关联，确保 EffectivePermissions 精确匹配与未来收紧通配符时不回归。
    """
    bind = op.get_bind()

    permissions = [
        ("importer.batch:read", "导入批次读取 / 失败明细下载", "function"),
        ("importer.batch:write", "导入文件上传 / 批次重试", "function"),
        ("importer.mapping:write", "字段映射版本创建", "function"),
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
        "operations": ["importer.batch:read"],
        "pr": ["importer.batch:read", "importer.batch:write"],
        "pr_manager": [
            "importer.batch:read",
            "importer.batch:write",
            "importer.mapping:write",
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
