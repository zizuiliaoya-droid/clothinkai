"""U06a importer 模块 Pydantic Schemas。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# ImportBatch 响应 / 列表
# ---------------------------------------------------------------------------


class ImportBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    file_hash: str
    original_filename: str
    mapping_version: int | None = None
    status: str
    total_rows: int
    imported: int
    failed: int
    retry_count: int
    error_summary: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ImportBatchPage(BaseModel):
    items: list[ImportBatchResponse]
    total: int
    page: int
    page_size: int


class ImportBatchListFilters(BaseModel):
    """列表过滤入参（query string 解析后构造）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=16)
    created_at_from: date | None = None
    created_at_to: date | None = None


# ---------------------------------------------------------------------------
# ImportJob 响应
# ---------------------------------------------------------------------------


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    row_number: int
    status: str
    raw_data: dict[str, Any]
    error_detail: str | None = None
    target_resource_id: UUID | None = None
    attempt_count: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# FieldMapping
# ---------------------------------------------------------------------------


class FieldMappingColumn(BaseModel):
    """单列映射配置。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    source_col: str = Field(min_length=1, max_length=128)
    target_field: str = Field(min_length=1, max_length=64)
    required: bool = False
    type: str = Field(default="str", max_length=16)  # str/int/decimal/date/datetime/bool
    transform: str | None = Field(default=None, max_length=64)


class FieldMappingCreate(BaseModel):
    """新建字段映射版本（EP07-S09）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    source: str = Field(min_length=1, max_length=32)
    columns: list[FieldMappingColumn] = Field(min_length=1)


class FieldMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    version: int
    mapping_config: dict[str, Any]
    is_active: bool
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Upload 响应
# ---------------------------------------------------------------------------


class ImportUploadResponse(BaseModel):
    """upload 端点响应（202 语义）。"""

    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    status: str
    source: str


__all__ = [
    "FieldMappingColumn",
    "FieldMappingCreate",
    "FieldMappingResponse",
    "ImportBatchListFilters",
    "ImportBatchPage",
    "ImportBatchResponse",
    "ImportJobResponse",
    "ImportUploadResponse",
]
