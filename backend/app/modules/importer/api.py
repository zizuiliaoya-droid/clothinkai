"""U06a importer 模块 REST API 路由。

全部路由**不挂路由级权限依赖**（8a-7，设计 §4.6）：只要求登录（``CurrentActiveUser`` /
``CurrentPerms``），权限在处理函数 / service 里按来源判断（``importer/access.py``）。
路由级依赖先于处理函数执行，留着它会先把只有 ``product.*:*`` 的跟单挡在外面。

端点（权限 = 按来源，见 access.SOURCE_ACCESS；未声明的来源用括号里的默认）：
- POST   /api/imports/upload                        上传权限（importer.batch:write） 上传 + 异步触发
- GET    /api/imports/batches                       可见来源（importer.batch:read）  列表 + 过滤
- GET    /api/imports/batches/{id}                  来源可见，否则 404                详情
- POST   /api/imports/batches/{id}/retry            来源可见（否则 404）+ 上传权限   重试（两类分流）
- GET    /api/imports/batches/{id}/errors/download  来源可见，否则 404                失败明细 CSV（按字段权限脱敏）
- POST   /api/imports/field-mappings                映射权限（importer.mapping:write） 新建映射版本
- GET    /api/imports/field-mappings                来源可见，否则 403                列出版本
- GET    /api/imports/field-mappings/active         来源可见，否则 403                取 active 版本
- POST   /api/imports/field-mappings/reset          映射权限                          恢复内置默认（8a-4）
- GET    /api/imports/sources/{source}/mapping-spec 来源可见，否则 403                映射目录（8a-4）
- GET    /api/imports/batches/{id}/notes            来源可见，否则 404                行提示与补空明细（8a-6）
- GET    /api/imports/conflicts                     可见来源                          冲突列表（脱敏，8a-6）
- GET    /api/imports/conflicts/summary             来源可见，否则 403                待处理冲突条数
- GET    /api/imports/conflicts/download            可见来源                          冲突明细 CSV（脱敏）
- POST   /api/imports/conflicts/resolve             裁决权限 + 字段权限，整单预检     单条 / 多选裁决
- GET    /api/imports/access                        登录即可                          各来源的能力

商品资料（manual_style_sku）只认 product.import:write（跟单、运营、管理员）。

降级语义：业务异常 → 全局 error handler 自动映射；系统失败自然冒泡 5xx + Sentry。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.modules.auth.deps import CurrentActiveUser, CurrentPerms
from app.modules.importer import access
from app.modules.importer.conflicts import ConflictFilters
from app.modules.importer.deps import (
    FieldMappingServiceDep,
    ImportConflictServiceDep,
    ImportServiceDep,
)
from app.modules.importer.exceptions import ImportFileTooLargeError
from app.modules.importer.models import ImportBatch
from app.modules.importer.repository import ImportBatchListFilters
from app.modules.importer.schemas import (
    ConflictObjectType,
    ConflictResolveRequest,
    ConflictResolveResponse,
    ConflictStatusFilter,
    ConflictSummary,
    FieldMappingCreate,
    FieldMappingResetRequest,
    FieldMappingResetResponse,
    FieldMappingResponse,
    ImportBatchPage,
    ImportBatchResponse,
    ImportConflictPage,
    ImportJobNotesPage,
    ImportSourceAccessResponse,
    ImportUploadResponse,
    MappingSpecResponse,
)

router = APIRouter(prefix="/api/imports", tags=["importer"])


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=ImportUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_import_file(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
    source: Annotated[str, Form(max_length=32)],
    file: Annotated[UploadFile, File()],
    mapping_version: Annotated[int | None, Form()] = None,
) -> ImportUploadResponse:
    """EP07-S07 上传导入文件（DB 先行 + UNIQUE 去重 + 异步解析触发）。

    先判来源级上传权限（403），再读文件块（未知来源走默认规则，之后由 service 报 422）。
    L2 大小兜底（NF-6）：读取时累计字节超 IMPORT_MAX_FILE_MB → 422（不全量落盘）。
    """
    access.require_write(perms, source)

    # NF-6 L2：分块读取并在超限时立即中止（避免无限读入内存）
    max_bytes = settings.IMPORT_MAX_FILE_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ImportFileTooLargeError()
        chunks.append(chunk)
    content = b"".join(chunks)

    batch = await service.upload(
        content=content,
        filename=file.filename,
        content_type=file.content_type,
        source=source,
        user=user,
        mapping_version=mapping_version,
    )
    return ImportUploadResponse(batch_id=batch.id, status=batch.status, source=batch.source)


# ---------------------------------------------------------------------------
# 批次读查询
# ---------------------------------------------------------------------------


@router.get("/batches", response_model=ImportBatchPage)
async def list_batches(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source: Annotated[str | None, Query(max_length=32)] = None,
    batch_status: Annotated[str | None, Query(max_length=16)] = None,
    created_at_from: Annotated[date | None, Query()] = None,
    created_at_to: Annotated[date | None, Query()] = None,
) -> ImportBatchPage:
    """EP07 列表 + 过滤（source / status / 创建日期区间），只含可见来源。"""
    filters = ImportBatchListFilters(
        source=source,
        status=batch_status,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
    )
    items, total = await service.list_batches(
        filters=filters, page=page, page_size=page_size, user=user, perms=perms
    )
    pending = await service.pending_conflicts(b.id for b in items)
    return ImportBatchPage(
        items=[_batch_response(b, pending.get(b.id, 0)) for b in items],
        total=total,
        page=page,
        page_size=page_size,
    )


def _batch_response(batch: ImportBatch, pending_conflicts: int) -> ImportBatchResponse:
    resp = ImportBatchResponse.model_validate(batch)
    resp.pending_conflicts = pending_conflicts
    return resp


@router.get("/batches/{batch_id}", response_model=ImportBatchResponse)
async def get_batch(
    batch_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
) -> ImportBatchResponse:
    batch = await service.get_batch(batch_id, user, perms)
    pending = await service.pending_conflicts([batch.id])
    return _batch_response(batch, pending.get(batch.id, 0))


@router.get("/batches/{batch_id}/notes", response_model=ImportJobNotesPage)
async def get_batch_notes(
    batch_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportJobNotesPage:
    """有提示或补空的行（行号、类别、提示、补空字段名；不含受保护字段的值）。批次不可见 → 404。"""
    items, total = await service.batch_notes(batch_id, user, perms, page=page, page_size=page_size)
    return ImportJobNotesPage(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# 重试 + 失败明细下载
# ---------------------------------------------------------------------------


@router.post("/batches/{batch_id}/retry", response_model=ImportBatchResponse)
async def retry_batch(
    batch_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
) -> ImportBatchResponse:
    """EP07-S10 重试（NF-3 原子 claim 互斥 + FB-E 两类分流）。

    404：批次不存在或来源不可见；403：可见但没有该来源的上传权限；
    409：retry_count 已达上限（exhausted）或批次正在处理中（busy）。
    """
    batch = await service.retry(batch_id, user, perms)
    pending = await service.pending_conflicts([batch.id])
    return _batch_response(batch, pending.get(batch.id, 0))


@router.get("/batches/{batch_id}/errors/download")
async def download_errors(
    batch_id: UUID,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportServiceDep,
) -> StreamingResponse:
    """EP07-S10 失败明细 CSV 下载（csv_safe injection 防护 + UTF-8 BOM + 字段权限脱敏）。"""
    data = await service.build_error_csv(batch_id, user, perms)
    filename = f"import_errors_{batch_id}.csv"
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# 字段映射版本
# ---------------------------------------------------------------------------


@router.post(
    "/field-mappings",
    response_model=FieldMappingResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_field_mapping(
    payload: FieldMappingCreate,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: FieldMappingServiceDep,
) -> FieldMappingResponse:
    """EP07-S09 新建字段映射版本并设为 active（旧 active 同事务下线）。"""
    mapping = await service.create_version(payload, user, perms)
    return FieldMappingResponse.model_validate(mapping)


@router.get("/field-mappings", response_model=list[FieldMappingResponse])
async def list_field_mappings(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: FieldMappingServiceDep,
    source: Annotated[str, Query(max_length=32)],
) -> list[FieldMappingResponse]:
    """列出某 source 的所有映射版本（version 倒序）。"""
    versions = await service.list_versions(source, user, perms)
    return [FieldMappingResponse.model_validate(m) for m in versions]


@router.get("/field-mappings/active", response_model=FieldMappingResponse | None)
async def get_active_field_mapping(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: FieldMappingServiceDep,
    source: Annotated[str, Query(max_length=32)],
) -> FieldMappingResponse | None:
    """取某 source 当前 active 映射版本（无 → null）。"""
    mapping = await service.get_active(source, user, perms)
    return FieldMappingResponse.model_validate(mapping) if mapping else None


@router.post("/field-mappings/reset", response_model=FieldMappingResetResponse)
async def reset_field_mapping(
    payload: FieldMappingResetRequest,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: FieldMappingServiceDep,
) -> FieldMappingResetResponse:
    """恢复内置默认：下线生效版本（历史版本保留），之后的批次按内置默认读（8a-4）。"""
    await service.reset(payload.source, user, perms)
    return FieldMappingResetResponse(source=payload.source)


@router.get("/sources/{source}/mapping-spec", response_model=MappingSpecResponse)
async def get_mapping_spec(
    source: Annotated[str, Path(max_length=32)],
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: FieldMappingServiceDep,
) -> MappingSpecResponse:
    """映射目录（目标字段、必填 / 其一必填 / 仅新建时写入）、内置默认映射、当前生效版本。

    看不到该来源 → 403；来源没有目录 → 404 ``IMPORT_MAPPING_SPEC_UNAVAILABLE``。
    """
    return await service.spec(source, user, perms)


# ---------------------------------------------------------------------------
# 冲突（8a-6）
# ---------------------------------------------------------------------------


def _conflict_filters(
    source: str | None,
    batch_id: UUID | None,
    conflict_status: ConflictStatusFilter,
    field: str | None,
    object_type: ConflictObjectType | None,
) -> ConflictFilters:
    return ConflictFilters(
        source=source,
        batch_id=batch_id,
        status=conflict_status,
        field=field,
        object_type=object_type,
    )


@router.get("/conflicts", response_model=ImportConflictPage)
async def list_conflicts(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportConflictServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source: Annotated[str | None, Query(max_length=32)] = None,
    batch_id: Annotated[UUID | None, Query()] = None,
    conflict_status: Annotated[ConflictStatusFilter, Query(alias="status")] = "pending",
    field: Annotated[str | None, Query(max_length=64)] = None,
    object_type: Annotated[ConflictObjectType | None, Query()] = None,
) -> ImportConflictPage:
    """冲突列表（只含可见来源；受保护字段按字段权限脱敏；按字段筛选只认该来源的比较字段）。"""
    filters = _conflict_filters(source, batch_id, conflict_status, field, object_type)
    items, total = await service.list_conflicts(
        filters, page=page, page_size=page_size, user=user, perms=perms
    )
    return ImportConflictPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/conflicts/summary", response_model=ConflictSummary)
async def get_conflict_summary(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportConflictServiceDep,
    source: Annotated[str, Query(max_length=32)],
) -> ConflictSummary:
    """某来源的待处理冲突条数（成本表页的「待处理冲突 N」）。"""
    return ConflictSummary(pending=await service.summary(source, user, perms))


@router.get("/conflicts/download")
async def download_conflicts(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportConflictServiceDep,
    source: Annotated[str | None, Query(max_length=32)] = None,
    batch_id: Annotated[UUID | None, Query()] = None,
    conflict_status: Annotated[ConflictStatusFilter, Query(alias="status")] = "pending",
    field: Annotated[str | None, Query(max_length=64)] = None,
    object_type: Annotated[ConflictObjectType | None, Query()] = None,
) -> StreamingResponse:
    """冲突明细 CSV（UTF-8 BOM、csv_safe、脱敏写「有差异」；超过 10,000 条 → 422）。"""
    filters = _conflict_filters(source, batch_id, conflict_status, field, object_type)
    data = await service.download_csv(filters, user, perms)
    return StreamingResponse(
        iter([data]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="import_conflicts.csv"'},
    )


@router.post("/conflicts/resolve", response_model=ConflictResolveResponse)
async def resolve_conflicts(
    payload: ConflictResolveRequest,
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: ImportConflictServiceDep,
) -> ConflictResolveResponse:
    """裁决冲突（单条与多选同一接口）：整单预检权限（403），之后逐条处理、逐条返回结果。"""
    return await service.resolve(payload, user, perms)


# ---------------------------------------------------------------------------
# 来源能力
# ---------------------------------------------------------------------------


@router.get("/access", response_model=list[ImportSourceAccessResponse])
async def get_import_access(perms: CurrentPerms) -> list[ImportSourceAccessResponse]:
    """当前用户对每个已注册来源的能力（看 / 上传 / 改映射 / 裁决），前端据此显示按钮。"""
    return [ImportSourceAccessResponse(**item) for item in access.describe_access(perms)]


__all__ = ["router"]
