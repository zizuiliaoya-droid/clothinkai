"""U06a ORM 模型：ImportBatch + ImportJob + FieldMapping。

按 functional-design/domain-entities.md 定义。继承 ``TenantScopedModel``（U01）：
- 自动 id (UUID PK) + tenant_id (FK + ORM 钩子) + created_at / updated_at
- 启用 RLS（migration 010 通过 enable_rls_sql 配置）

关键约束：
- import_batch：``UNIQUE(tenant_id, source, file_hash)`` 永久（NF-2 并发去重权威）
- import_job：``UNIQUE(batch_id, row_number)``（NF-3/FB-E 行幂等 + 重试原地更新定位）
- field_mapping：``UNIQUE(tenant_id, source, version)`` + 部分 ``UNIQUE(tenant_id, source) WHERE is_active``

**不使用 attachment FK**（FB-A）：import_batch.file_r2_key 直存 R2 key，用 U01 R2 helper 读写。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


# ---------------------------------------------------------------------------
# ImportBatch（导入批次）
# ---------------------------------------------------------------------------


class ImportBatch(TenantScopedModel):
    """导入批次（一次上传 = 一个 batch）。"""

    __tablename__ = "import_batch"

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # 文件存储（FB-A：直存 R2 key，非 attachment FK）
    file_r2_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_bucket: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'private'")
    )

    mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'processing'")
    )
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    imported: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    failed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # NF-2：并发去重的唯一权威（永久 UNIQUE）
        Index(
            "uq_import_batch_hash",
            "tenant_id",
            "source",
            "file_hash",
            unique=True,
        ),
        Index(
            "idx_import_batch_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
        Index("idx_import_batch_source", "tenant_id", "source", "created_at"),
        CheckConstraint(
            "status IN ('processing','completed','partial','failed')",
            name="ck_import_batch_status",
        ),
        CheckConstraint(
            "file_bucket IN ('public','private','credentials','backups')",
            name="ck_import_batch_bucket",
        ),
        CheckConstraint(
            "total_rows >= 0 AND imported >= 0 AND failed >= 0",
            name="ck_import_batch_counts_nonneg",
        ),
        CheckConstraint(
            "retry_count >= 0 AND retry_count <= 3",
            name="ck_import_batch_retry",
        ),
    )


# ---------------------------------------------------------------------------
# ImportJob（导入行级结果）
# ---------------------------------------------------------------------------


class ImportJob(TenantScopedModel):
    """导入行级结果（每行一条，便于精确重试 / 失败下载）。"""

    __tablename__ = "import_job"

    batch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("import_batch.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_resource_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    __table_args__ = (
        # NF-3/FB-E：行幂等 + 重试原地更新定位
        Index("uq_import_job_batch_row", "batch_id", "row_number", unique=True),
        Index(
            "idx_import_job_batch_status", "tenant_id", "batch_id", "status"
        ),
        CheckConstraint(
            "status IN ('success','failed')", name="ck_import_job_status"
        ),
        CheckConstraint("attempt_count >= 1", name="ck_import_job_attempt"),
    )


# ---------------------------------------------------------------------------
# FieldMapping（字段映射版本）
# ---------------------------------------------------------------------------


class FieldMapping(TenantScopedModel):
    """字段映射版本（同 source 多版本，仅一个 active，EP07-S09）。"""

    __tablename__ = "field_mapping"

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "uq_field_mapping_version",
            "tenant_id",
            "source",
            "version",
            unique=True,
        ),
        # 同 (tenant, source) 仅一个 active（部分唯一）
        Index(
            "uq_field_mapping_active",
            "tenant_id",
            "source",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "idx_field_mapping_active", "tenant_id", "source", "is_active"
        ),
        CheckConstraint("version >= 1", name="ck_field_mapping_version"),
    )


__all__ = ["FieldMapping", "ImportBatch", "ImportJob"]
