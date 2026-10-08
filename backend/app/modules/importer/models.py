"""U06a ORM 模型：ImportBatch + ImportJob + FieldMapping + ImportConflict（8a-6）。

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

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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

# FK 目标 user 表：Celery 路径（只 import app.tasks.import_tasks）不会经过 auth 模块，
# 不显式 import 的话 flush ImportConflict 时 NoReferencedTableError（台账）
from app.modules.auth import models as _auth_models  # noqa: F401

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
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    imported: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 8a-6 计数：filled / skipped / conflicted 与 imported / failed 按行互斥；
    # warning_count（带提示的行）与 filled_objects（补空的对象数）不互斥（设计 §4.2）
    filled: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    conflicted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    filled_objects: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

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
        CheckConstraint(
            "filled >= 0 AND skipped >= 0 AND conflicted >= 0 AND warning_count >= 0 "
            "AND filled_objects >= 0",
            name="ck_import_batch_8a_counts_nonneg",
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
    target_resource_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # 行提示与补空明细：{"warnings": [str], "filled": [{object_type, object_label, fields}]}；
    # 只有字段名、没有值（不含受保护字段的值），两样都没有就是 NULL（设计 §4.2）
    # none_as_null：Python None 落 SQL NULL（不是 JSON null），/notes 按 IS NOT NULL 取行
    notes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),  # type: ignore[no-untyped-call]
        nullable=True,
    )

    __table_args__ = (
        # NF-3/FB-E：行幂等 + 重试原地更新定位
        Index("uq_import_job_batch_row", "batch_id", "row_number", unique=True),
        Index("idx_import_job_batch_status", "tenant_id", "batch_id", "status"),
        CheckConstraint(
            "status IN ('success','failed','filled','skipped','conflict')",
            name="ck_import_job_status",
        ),
        CheckConstraint("attempt_count >= 1", name="ck_import_job_attempt"),
    )


# ---------------------------------------------------------------------------
# ImportConflict（导入冲突，8a-6）
# ---------------------------------------------------------------------------


class ImportConflict(TenantScopedModel):
    """导入冲突：以「来源 + 对象」为单位持久化，可逐条或多选裁决（设计 §4.4）。

    「同来源同对象只有一条待处理」由部分唯一索引 ``uq_import_conflict_pending`` 保证
    （批次之间可并发，应用层查完再插有竞态）。
    """

    __tablename__ = "import_conflict"

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("import_batch.id", ondelete="SET NULL"),
        nullable=True,
    )
    row_numbers: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # 多态，不设外键：对象被删时冲突转「失效」
    object_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    object_key: Mapped[str] = mapped_column(String(128), nullable=False)
    object_label: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default=text("'pending'")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 不设外键，免得「先标旧冲突取代、再插新冲突」的插入顺序打架
    superseded_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_import_conflict_pending",
            "tenant_id",
            "source",
            "object_type",
            "object_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("idx_import_conflict_list", "tenant_id", "source", "status", "created_at"),
        Index("idx_import_conflict_batch", "tenant_id", "batch_id"),
        CheckConstraint(
            "object_type IN ('style','sku','goods','blogger')",
            name="ck_import_conflict_object_type",
        ),
        CheckConstraint("kind IN ('fields','key')", name="ck_import_conflict_kind"),
        CheckConstraint(
            "status IN ('pending','overwritten','kept','superseded','invalid')",
            name="ck_import_conflict_status",
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_import_conflict_resolved_at",
        ),
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
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
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
        Index("idx_field_mapping_active", "tenant_id", "source", "is_active"),
        CheckConstraint("version >= 1", name="ck_field_mapping_version"),
    )


__all__ = ["FieldMapping", "ImportBatch", "ImportConflict", "ImportJob"]
