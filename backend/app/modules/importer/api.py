"""U06a importer 模块 REST API 路由（8 端点）。

端点（NF-5 权限对齐）：
- POST   /api/imports/upload                     importer.batch:write   上传 + 异步触发
- GET    /api/imports/batches                    importer.batch:read    列表 + 过滤
- GET    /api/imports/batches/{id}               importer.batch:read    详情
- POST   /api/imports/batches/{id}/retry         importer.batch:write   重试（两类分流）
- GET    /api/imports/batches/{id}/errors/download  importer.batch:read 失败明细 CSV
- POST   /api/imports/field-mappings             importer.mapping:write 新建映射版本
- GET    /api/imports/field-mappings             importer.batch:read    列出版本
- GET    /api/imports/field-mappings/active      importer.batch:read    取 active 版本

降级语义：业务异常 → 全局 error handler 自动映射；系统失败自然冒泡 5xx + Sentry。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.importer.deps import FieldMappingServiceDep, ImportServiceDep
from app.modules.importer.exceptions import ImportFileTooLargeError
from app.modules.importer.repository import ImportBatchListFilters
from app.modules.importer.schemas import (
    FieldMappingCreate,
    FieldMappingResponse,
    ImportBatchPage,
    ImportBatchResponse,
    ImportUploadResponse,
)

router = APIRouter(prefix="/api/imports", tags=["importer"])


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------


@router.post(
    "/upload",
    response_model=ImportUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[require_permission("importer.batch", "write")],
)
async def upload_import_file(
    user: CurrentActiveUser,
    service: ImportServiceDep,
    source: Annotated[str, Form(max_length=32)],
    file: Annotated[UploadFile, File()],
    mapping_version: Annotated[int | None, Form()] = None,
) -> ImportUploadResponse:
    """EP07-S07 上传导入文件（DB 先行 + UNIQUE 去重 + 异步解析触发）。

    L2 大小兜底（NF-6）：读取时累计字节超 IMPORT_MAX_FILE_MB → 422（不全量落盘）。
    """
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
    return ImportUploadResponse(
        batch_id=batch.id, status=batch.status, source=batch.source
    )


# ---------------------------------------------------------------------------
# 批次读查询
# ---------------------------------------------------------------------------


@router.get(
    "/batches",
    response_model=ImportBatchPage,
    dependencies=[require_permission("importer.batch", "read")],
)
async def list_batches(
    user: CurrentActiveUser,
    service: ImportServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    source: Annotated[str | None, Query(max_length=32)] = None,
    batch_status: Annotated[str | None, Query(max_length=16)] = None,
    created_at_from: Annotated[date | None, Query()] = None,
    created_at_to: Annotated[date | None, Query()] = None,
) -> ImportBatchPage:
    """EP07 列表 + 过滤（source / status / 创建日期区间）。"""
    filters = ImportBatchListFilters(
        source=source,
        status=batch_status,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
    )
    items, total = await service.list_batches(
        filters=filters, page=page, page_size=page_size, user=user
    )
    return ImportBatchPage(
        items=[ImportBatchResponse.model_validate(b) for b in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/batches/{batch_id}",
    response_model=ImportBatchResponse,
    dependencies=[require_permission("importer.batch", "read")],
)
async def get_batch(
    batch_id: UUID,
    user: CurrentActiveUser,
    service: ImportServiceDep,
) -> ImportBatchResponse:
    batch = await service.get_batch(batch_id, user)
    return ImportBatchResponse.model_validate(batch)


# ---------------------------------------------------------------------------
# 重试 + 失败明细下载
# ---------------------------------------------------------------------------


@router.post(
    "/batches/{batch_id}/retry",
    response_model=ImportBatchResponse,
    dependencies=[require_permission("importer.batch", "write")],
)
async def retry_batch(
    batch_id: UUID,
    user: CurrentActiveUser,
    service: ImportServiceDep,
) -> ImportBatchResponse:
    """EP07-S10 重试（NF-3 原子 claim 互斥 + FB-E 两类分流）。

    409：retry_count 已达上限（exhausted）或批次正在处理中（busy）。
    """
    batch = await service.retry(batch_id, user)
    return ImportBatchResponse.model_validate(batch)


@router.get(
    "/batches/{batch_id}/errors/download",
    dependencies=[require_permission("importer.batch", "read")],
)
async def download_errors(
    batch_id: UUID,
    user: CurrentActiveUser,
    service: ImportServiceDep,
) -> StreamingResponse:
    """EP07-S10 失败明细 CSV 下载（csv_safe injection 防护 + UTF-8 BOM）。"""
    data = await service.build_error_csv(batch_id, user)
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
    dependencies=[require_permission("importer.mapping", "write")],
)
async def create_field_mapping(
    payload: FieldMappingCreate,
    user: CurrentActiveUser,
    service: FieldMappingServiceDep,
) -> FieldMappingResponse:
    """EP07-S09 新建字段映射版本并设为 active（旧 active 同事务下线）。"""
    mapping = await service.create_version(payload, user)
    return FieldMappingResponse.model_validate(mapping)


@router.get(
    "/field-mappings",
    response_model=list[FieldMappingResponse],
    dependencies=[require_permission("importer.batch", "read")],
)
async def list_field_mappings(
    user: CurrentActiveUser,
    service: FieldMappingServiceDep,
    source: Annotated[str, Query(max_length=32)],
) -> list[FieldMappingResponse]:
    """列出某 source 的所有映射版本（version 倒序）。"""
    versions = await service.list_versions(source, user)
    return [FieldMappingResponse.model_validate(m) for m in versions]


@router.get(
    "/field-mappings/active",
    response_model=FieldMappingResponse | None,
    dependencies=[require_permission("importer.batch", "read")],
)
async def get_active_field_mapping(
    user: CurrentActiveUser,
    service: FieldMappingServiceDep,
    source: Annotated[str, Query(max_length=32)],
) -> FieldMappingResponse | None:
    """取某 source 当前 active 映射版本（无 → null）。"""
    mapping = await service.get_active(source, user)
    return FieldMappingResponse.model_validate(mapping) if mapping else None


__all__ = ["router"]
