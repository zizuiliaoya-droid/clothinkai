"""U06a importer 模块 Pydantic Schemas。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

# ---------------------------------------------------------------------------
# ImportBatch 响应 / 列表
# ---------------------------------------------------------------------------


class ImportImageSummary(BaseModel):
    """内嵌图补主图的款数：补了 / 已有主图跳过 / 图片无效 / 未找到或已删除款式 / 保存失败。"""

    set: int = 0
    kept: int = 0
    invalid: int = 0
    skipped: int = 0
    failed: int = 0


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
    # 8a-6：行互斥的仅补空 / 重复已跳过 / 冲突；不互斥的带提示行数 / 补空对象数（设计 §4.2）。
    # 商品资料与博主来源的 imported =「新增或已覆盖」的行数；其他来源与原来相同
    filled: int = 0
    skipped: int = 0
    conflicted: int = 0
    warning_count: int = 0
    filled_objects: int = 0
    pending_conflicts: int = 0  # 该批次仍待处理的冲突条数
    # 导入时读内嵌图补主图的每款结果（读时按 import_job.notes.image 汇总）；没读过内嵌图 → null
    image_summary: ImportImageSummary | None = None


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


class MappingTargetItem(BaseModel):
    """映射目录里的一个目标字段（8a-4）。``group`` 形如 ``组名:选项``（「其一必填」）。"""

    field: str
    label: str
    type: str
    default_col: str
    aliases: list[str] = Field(default_factory=list)
    required: bool = False
    group: str | None = None
    create_only: bool = False


class ActiveMappingInfo(BaseModel):
    version: int
    columns: list[dict[str, Any]]
    created_by_name: str | None = None
    created_at: datetime


class MappingSpecResponse(BaseModel):
    """``GET /api/imports/sources/{source}/mapping-spec``：目录、内置默认映射与当前生效版本。"""

    source: str
    targets: list[MappingTargetItem]
    builtin_columns: list[dict[str, Any]]
    active: ActiveMappingInfo | None = None


class FieldMappingResetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    source: str = Field(min_length=1, max_length=32)


class FieldMappingResetResponse(BaseModel):
    source: str
    active: None = None


# ---------------------------------------------------------------------------
# Upload 响应
# ---------------------------------------------------------------------------


class ImportUploadResponse(BaseModel):
    """upload 端点响应（202 语义）。"""

    model_config = ConfigDict(from_attributes=True)

    batch_id: UUID
    status: str
    source: str


class ImportSourceAccessResponse(BaseModel):
    """``GET /api/imports/access`` 的一项：当前用户对某个已注册来源的能力（8a-7）。"""

    source: str
    label: str
    configurable: bool  # 该来源的重复规则可切换（导入记录页据此显示新计数列，8a-6）
    can_view: bool
    can_upload: bool
    can_map: bool
    can_resolve: bool


# ---------------------------------------------------------------------------
# 行提示与补空明细（GET /batches/{id}/notes，8a-6）
# ---------------------------------------------------------------------------


class ImportJobFilledItem(BaseModel):
    object_type: str
    object_label: str
    fields: list[str]


class ImportJobImageNote(BaseModel):
    """该行所属款式的内嵌图补主图结果（只记在该款取图的那一行）。"""

    status: Literal["set", "kept", "invalid", "skipped", "failed"]
    style_code: str
    reason: str | None = None


class ImportJobNoteItem(BaseModel):
    """一行的提示与补空明细（只有字段名，不含任何值）。"""

    row_number: int
    status: str
    warnings: list[str] = Field(default_factory=list)
    filled: list[ImportJobFilledItem] = Field(default_factory=list)
    image: ImportJobImageNote | None = None


class ImportJobNotesPage(BaseModel):
    items: list[ImportJobNoteItem]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 冲突（8a-6，设计 §4.4、§4.5、§4.5.3）
# ---------------------------------------------------------------------------

ConflictStatusFilter = Literal["pending", "overwritten", "kept", "superseded", "invalid", "all"]
ConflictObjectType = Literal["style", "sku", "goods", "blogger"]
ResolveOutcome = Literal[
    "resolved", "stale", "gone", "not_pending", "not_overwritable", "invalid_value", "error"
]


class ConflictFieldDiff(BaseModel):
    """冲突里的一个字段差异；查看者没有读权限的受保护字段 masked=True、四个值置空。"""

    field: str
    label: str
    system: JsonValue = None
    file: JsonValue = None
    system_display: str | None = None
    file_display: str | None = None
    sensitive: bool = False
    masked: bool = False
    from_batch_id: UUID | None = None  # 从被取代的旧冲突并入的字段，标来源批次


class ImportConflictItem(BaseModel):
    id: UUID
    source: str
    batch_id: UUID | None = None
    batch_filename: str | None = None
    row_numbers: list[int]
    object_type: str
    object_id: UUID
    object_key: str
    object_label: str
    kind: str
    fields: list[ConflictFieldDiff]
    message: str | None = None
    status: str
    created_by: UUID | None = None
    created_at: datetime
    resolved_by: UUID | None = None
    resolved_by_name: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    superseded_by: UUID | None = None
    can_resolve: bool  # 当前用户的来源权限 ∧ 受保护字段的读写权限
    overwritable: bool  # 字段差异可「用文件覆盖」；键冲突只能「保留系统值」


class ImportConflictPage(BaseModel):
    items: list[ImportConflictItem]
    total: int
    page: int
    page_size: int


class ConflictSummary(BaseModel):
    pending: int


class ResolveItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    # 用户看到的系统值（按字段名，原样回传列表里的 system）；用文件覆盖时必填
    expected_system_values: dict[str, JsonValue] | None = None


class ConflictResolveRequest(BaseModel):
    """单条与多选共用：一次 1 ~ 200 条，id 不能重复（设计 §4.5）。"""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["overwrite", "keep"]
    items: list[ResolveItem] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("items")
    @classmethod
    def _unique_ids(cls, items: list[ResolveItem]) -> list[ResolveItem]:
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError("冲突 id 不能重复")
        return items

    @field_validator("note")
    @classmethod
    def _strip_note(cls, note: str | None) -> str | None:
        if note is None:
            return None
        note = note.strip()
        return note or None


class ConflictResolveResult(BaseModel):
    id: UUID
    outcome: ResolveOutcome
    status: str | None = None  # 处理后（或 not_pending 时当前）的冲突状态
    current_values: dict[str, JsonValue] | None = None  # stale：对象当前值（已按字段权限脱敏）
    masked_fields: list[str] = Field(default_factory=list)  # current_values 里被脱敏的字段
    field: str | None = None  # invalid_value：哪个字段
    message: str | None = None


class ConflictResolveSummary(BaseModel):
    resolved: int = 0
    stale: int = 0
    gone: int = 0
    not_pending: int = 0
    not_overwritable: int = 0
    invalid_value: int = 0
    error: int = 0


class ConflictResolveResponse(BaseModel):
    results: list[ConflictResolveResult]
    summary: ConflictResolveSummary


__all__ = [
    "ConflictFieldDiff",
    "ConflictObjectType",
    "ConflictResolveRequest",
    "ConflictResolveResponse",
    "ConflictResolveResult",
    "ConflictResolveSummary",
    "ConflictStatusFilter",
    "ConflictSummary",
    "FieldMappingColumn",
    "FieldMappingCreate",
    "ActiveMappingInfo",
    "FieldMappingResetRequest",
    "FieldMappingResetResponse",
    "FieldMappingResponse",
    "MappingSpecResponse",
    "MappingTargetItem",
    "ImportBatchListFilters",
    "ImportBatchPage",
    "ImportBatchResponse",
    "ImportConflictItem",
    "ImportConflictPage",
    "ImportImageSummary",
    "ImportJobFilledItem",
    "ImportJobImageNote",
    "ImportJobNoteItem",
    "ImportJobNotesPage",
    "ImportJobResponse",
    "ImportSourceAccessResponse",
    "ImportUploadResponse",
    "ResolveItem",
    "ResolveOutcome",
]
