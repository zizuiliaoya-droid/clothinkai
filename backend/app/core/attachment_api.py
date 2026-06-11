"""通用附件 API（U05 触发补齐 shared 基础设施）。

通用语义 — 不放 modules/finance：
- POST /api/attachments/upload-init：创建 attachment 记录（status='uploading'）+ 返回 presigned PUT URL
- POST /api/attachments/{id}/complete：前端直传 R2 完成后调用，标记 status='ready'

各业务模块（如 U05 settlement / U02 product / U03 blogger）通过 attachment_id 引用并做下游校验
（如 U05 ProofAttachmentValidator 6 项强校验，FB4）。

purpose 白名单由 ``core/attachment.py::ALLOWED_PURPOSES`` 维护，新增 purpose 需在白名单注册。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.attachment import (
    ALLOWED_PURPOSES,
    Attachment,
    BucketKind,
    attachment_service,
)
from app.core.exceptions import AttachmentError
from app.modules.auth.deps import CurrentActiveUser, SessionDep


router = APIRouter(prefix="/api", tags=["attachment"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AttachmentUploadInitRequest(BaseModel):
    """上传初始化入参。"""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    bucket: str = Field(min_length=1, max_length=16)
    purpose: str = Field(min_length=1, max_length=32)
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=0)


class AttachmentUploadInitResponse(BaseModel):
    """上传初始化响应。"""

    model_config = ConfigDict(from_attributes=True)

    attachment_id: UUID
    presigned_url: str
    expires_in_seconds: int = 900


class AttachmentResponse(BaseModel):
    """attachment 完整响应（不含 r2_key — 仅后端使用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    bucket: str
    purpose: str
    filename: str | None
    mime_type: str
    size_bytes: int
    status: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/attachments/upload-init",
    response_model=AttachmentUploadInitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_init(
    payload: AttachmentUploadInitRequest,
    user: CurrentActiveUser,
    session: SessionDep,
) -> AttachmentUploadInitResponse:
    """初始化上传：创建 attachment 记录 + 返回 presigned PUT URL（15 分钟有效）.

    流程：
    1. 调用本端点 → 返回 attachment_id + presigned_url
    2. 前端用 presigned_url 直传 R2（PUT 请求 + Content-Type 必须与 mime_type 一致）
    3. 调 ``POST /api/attachments/{id}/complete`` → 后端标记 status='ready'

    purpose 白名单由 ``core/attachment.py::ALLOWED_PURPOSES`` 控制；不在白名单 → 422。
    """
    # 白名单校验（提前抛错；service 层也会校验，但 API 层提前响应更友好）
    if payload.purpose not in ALLOWED_PURPOSES:
        raise AttachmentError(
            f"purpose '{payload.purpose}' 不在白名单",
            details={
                "allowed_purposes": sorted(ALLOWED_PURPOSES),
                "actual": payload.purpose,
            },
        )

    # bucket 类型校验（运行时强类型）
    bucket_value: BucketKind
    if payload.bucket in ("public", "private", "credentials", "backups"):
        bucket_value = payload.bucket  # type: ignore[assignment]
    else:
        raise AttachmentError(
            f"bucket '{payload.bucket}' 无效",
            details={"actual": payload.bucket},
        )

    attachment, presigned_url = await attachment_service.create_upload_record(
        session=session,
        tenant_id=user.tenant_id,
        created_by=user.id,
        bucket=bucket_value,
        purpose=payload.purpose,
        filename=payload.filename,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
    )
    await session.commit()

    return AttachmentUploadInitResponse(
        attachment_id=attachment.id,
        presigned_url=presigned_url,
        expires_in_seconds=900,
    )


@router.post(
    "/attachments/{attachment_id}/complete",
    response_model=AttachmentResponse,
)
async def complete_upload(
    attachment_id: UUID,
    user: CurrentActiveUser,
    session: SessionDep,
) -> AttachmentResponse:
    """前端直传完成后调用，标记 attachment.status='ready'.

    UPDATE WHERE 含 tenant_id 防越权（与 RLS 双重防护）。
    """
    attachment = await attachment_service.mark_uploaded(
        session=session,
        attachment_id=attachment_id,
        tenant_id=user.tenant_id,
    )
    await session.commit()
    return AttachmentResponse.model_validate(attachment)


__all__ = [
    "AttachmentResponse",
    "AttachmentUploadInitRequest",
    "AttachmentUploadInitResponse",
    "router",
]
