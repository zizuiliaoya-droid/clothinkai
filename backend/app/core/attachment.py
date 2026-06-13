"""附件管理（R2 + 公私桶分离 + 签名 URL + Attachment ORM）。

按应用设计 Q11=C 决策：
- 业务表用 attachment_id 关联，不直接存 URL
- public 桶：CDN 公开访问（商品图、设计稿）
- private 桶：签名 URL，TTL 默认 15 分钟（付款截图、版型文件）

R2 兼容 S3 API，通过 boto3 client 访问。

U05 触发补齐 shared attachment 基础设施（详见 U05 code-generation-plan §1.0）：
- Attachment ORM 模型（表）
- AttachmentService.create_upload_record / mark_uploaded / get_by_id 3 新方法
- POST /api/attachments/upload-init / complete 通用端点（在 core/attachment_api.py）
- U02/U03 现有 ``attachment_key`` 字段保留，V1 attachment 单元做迁移整合
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import IO, Any, Literal
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.db import TenantScopedModel
from app.core.exceptions import AttachmentError

log = logging.getLogger(__name__)

BucketKind = Literal["public", "private", "credentials", "backups"]

# 本地开发回退：R2 未配置时把对象落到本地共享目录（backend 与 celery-worker
# 同挂 ./backend:/app，故 /app 下目录天然共享）。生产配置 R2 后永不走此分支。
_LOCAL_STORAGE_DIR = Path(__file__).resolve().parents[2] / ".local_storage"


def _local_object_path(bucket: str, key: str) -> Path:
    return _LOCAL_STORAGE_DIR / str(bucket) / key


def _make_s3_client() -> Any:
    if not settings.R2_ENDPOINT_URL or not settings.R2_ACCESS_KEY_ID:
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=(
            settings.R2_SECRET_ACCESS_KEY.get_secret_value()
            if settings.R2_SECRET_ACCESS_KEY
            else None
        ),
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


_s3_client: Any = None


def get_s3_client() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = _make_s3_client()
    return _s3_client


def _bucket_name(kind: BucketKind) -> str:
    return {
        "public": settings.R2_BUCKET_PUBLIC,
        "private": settings.R2_BUCKET_PRIVATE,
        "credentials": settings.R2_BUCKET_CREDENTIALS,
        "backups": settings.R2_BUCKET_BACKUPS,
    }[kind]


# ---------------------------------------------------------------------------
# Attachment ORM（U05 触发补齐 shared 基础设施）
# ---------------------------------------------------------------------------


class Attachment(TenantScopedModel):
    """统一附件元数据表（shared 基础设施）。

    用法：
    - 业务表通过 ``attachment_id`` (UUID FK) 引用 attachment 行
    - 不直接存 R2 key（防绕过校验 / RLS / 引用计数）
    - V1 引入 ``reference_count`` 字段实现 GC 引用保护

    生命周期（status 状态机）：
    - ``uploading``：``create_upload_record`` 创建后；前端正在直传 R2
    - ``ready``：前端调 ``mark_uploaded`` 后；可被业务表引用

    跨租户访问由 RLS（tenant_isolation 策略）+ 应用层 ``ProofAttachmentValidator``
    双层防护（FB4）。
    """

    __tablename__ = "attachment"

    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    """public / private / credentials / backups（与 BucketKind 一致）。"""

    r2_key: Mapped[str] = mapped_column(String(512), nullable=False)
    """R2 内部 path（不暴露前端；仅后端 service 通过 get_signed_url 生成访问 URL）。"""

    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    """业务用途（如 settlement_proof）。各模块在使用前白名单校验。"""

    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """原始文件名（仅供前端展示；不参与 R2 path 生成）。"""

    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'uploading'")
    )
    """uploading / ready。"""

    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_attachment_tenant_purpose", "tenant_id", "purpose"),
        Index("idx_attachment_status", "status", "created_at"),  # V1 GC 任务用
        Index("uq_attachment_r2_key", "r2_key", unique=True),
        CheckConstraint("size_bytes >= 0", name="ck_attachment_size_nonneg"),
        CheckConstraint(
            "bucket IN ('public', 'private', 'credentials', 'backups')",
            name="ck_attachment_bucket",
        ),
        CheckConstraint(
            "status IN ('uploading', 'ready')", name="ck_attachment_status"
        ),
    )


# 允许的 purpose 白名单（U05 引入；后续模块按需追加）
ALLOWED_PURPOSES: frozenset[str] = frozenset({
    "settlement_proof",  # U05 付款截图（FB4）
})


# ---------------------------------------------------------------------------
# AttachmentService
# ---------------------------------------------------------------------------


class AttachmentService:
    """统一封装 R2 上传/下载/签名 URL。"""

    def __init__(self) -> None:
        self._client = get_s3_client()

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def upload_bytes(
        self,
        data: bytes | IO[bytes],
        *,
        bucket: BucketKind,
        key: str,
        content_type: str = "application/octet-stream",
    ) -> str:
        """上传字节流到指定桶 + key，返回完整 key。"""
        if not self.is_configured:
            # 本地回退：写入共享文件系统（dev only）
            data_bytes = data if isinstance(data, bytes) else data.read()
            path = _local_object_path(bucket, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data_bytes)
            log.info(
                "attachment_uploaded_local",
                extra={"bucket": bucket, "key": key, "path": str(path)},
            )
            return key
        try:
            self._client.put_object(
                Bucket=_bucket_name(bucket),
                Key=key,
                Body=data,
                ContentType=content_type,
            )
            log.info(
                "attachment_uploaded",
                extra={"bucket": bucket, "key": key, "content_type": content_type},
            )
            return key
        except Exception as exc:  # noqa: BLE001
            log.exception("attachment_upload_failed", extra={"bucket": bucket, "key": key})
            raise AttachmentError(f"上传 R2 失败: {exc}") from exc

    def get_public_url(self, key: str) -> str:
        """生成 public 桶的 CDN URL。"""
        if not settings.R2_PUBLIC_BASE_URL:
            raise AttachmentError("R2_PUBLIC_BASE_URL 未配置")
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key.lstrip('/')}"

    def get_signed_url(
        self,
        bucket: BucketKind,
        key: str,
        *,
        expires_in: int = 900,  # 15 分钟
    ) -> str:
        """生成预签名 URL（私有桶用）。"""
        if not self.is_configured:
            raise AttachmentError("R2 未配置")
        try:
            return str(
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": _bucket_name(bucket), "Key": key},
                    ExpiresIn=expires_in,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise AttachmentError(f"生成签名 URL 失败: {exc}") from exc

    def delete(self, bucket: BucketKind, key: str) -> None:
        """删除对象。"""
        if not self.is_configured:
            raise AttachmentError("R2 未配置")
        try:
            self._client.delete_object(Bucket=_bucket_name(bucket), Key=key)
        except Exception as exc:  # noqa: BLE001
            log.warning("attachment_delete_failed", extra={"bucket": bucket, "key": key})
            raise AttachmentError(f"删除 R2 对象失败: {exc}") from exc

    def get_object_bytes(self, bucket: BucketKind, key: str) -> bytes:
        """读取对象全部字节（U06a 导入框架解析用，FB-A）。

        属 U01 R2 helper 的合理扩展（与 U05 Attachment ORM 无关）。
        MVP 一次性读入内存（导入文件 ≤ 20MB 可控）；V1 评估流式 TextIOWrapper。
        """
        if not self.is_configured:
            # 本地回退：从共享文件系统读取（dev only）
            path = _local_object_path(bucket, key)
            if not path.exists():
                raise AttachmentError(f"本地存储对象不存在: {path}")
            return path.read_bytes()
        try:
            obj = self._client.get_object(Bucket=_bucket_name(bucket), Key=key)
            return bytes(obj["Body"].read())
        except Exception as exc:  # noqa: BLE001
            log.exception(
                "attachment_get_object_failed",
                extra={"bucket": bucket, "key": key},
            )
            raise AttachmentError(f"读取 R2 对象失败: {exc}") from exc

    @staticmethod
    def make_tenant_key(
        tenant_id: UUID, prefix: str, *, filename: str | None = None
    ) -> str:
        """生成统一格式的对象键：``{tenant_id}/{prefix}/{uuid}_{filename}``。"""
        suffix = filename or uuid4().hex
        return f"{tenant_id}/{prefix}/{uuid4().hex}_{suffix}"

    # ----------------------------------------------------------------- #
    # ORM-backed methods（U05 shared 基础设施补齐）
    # ----------------------------------------------------------------- #

    async def create_upload_record(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        created_by: UUID,
        bucket: BucketKind,
        purpose: str,
        filename: str | None,
        mime_type: str,
        size_bytes: int,
    ) -> tuple[Attachment, str]:
        """创建 attachment 记录（status='uploading'）+ 生成 r2_key + presigned PUT URL.

        前端流程：
        1. 调 ``POST /api/attachments/upload-init`` → 后端创建 attachment 行 + 返回 attachment_id + presigned PUT URL
        2. 前端用 presigned URL 直传 R2
        3. 调 ``POST /api/attachments/{id}/complete`` → 后端 mark_uploaded()

        Returns:
            (Attachment 实例, presigned PUT URL)。

        Raises:
            AttachmentError: R2 未配置或 purpose 不在白名单。
        """
        if purpose not in ALLOWED_PURPOSES:
            raise AttachmentError(
                f"purpose '{purpose}' 不在白名单，"
                f"允许值：{sorted(ALLOWED_PURPOSES)}"
            )
        if not self.is_configured:
            raise AttachmentError("R2 未配置")

        attachment_id = uuid4()
        # path = {tenant_id}/{purpose}/{attachment_id}/{filename}
        safe_filename = (filename or attachment_id.hex).replace("/", "_")
        r2_key = f"{tenant_id}/{purpose}/{attachment_id}/{safe_filename}"

        attachment = Attachment(
            id=attachment_id,
            tenant_id=tenant_id,
            bucket=bucket,
            r2_key=r2_key,
            purpose=purpose,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            status="uploading",
            created_by=created_by,
        )
        session.add(attachment)
        await session.flush()

        try:
            presigned_url = self._client.generate_presigned_url(
                "put_object",
                Params={
                    "Bucket": _bucket_name(bucket),
                    "Key": r2_key,
                    "ContentType": mime_type,
                },
                ExpiresIn=900,  # 15 分钟（与 GET 签名一致）
                HttpMethod="PUT",
            )
        except Exception as exc:  # noqa: BLE001
            raise AttachmentError(f"生成 PUT 签名 URL 失败: {exc}") from exc

        log.info(
            "attachment_upload_record_created",
            extra={
                "attachment_id": str(attachment_id),
                "tenant_id": str(tenant_id),
                "purpose": purpose,
            },
        )
        return attachment, str(presigned_url)

    async def mark_uploaded(
        self,
        *,
        session: AsyncSession,
        attachment_id: UUID,
        tenant_id: UUID,
    ) -> Attachment:
        """前端直传完成后调用，将 status 从 'uploading' 改为 'ready'.

        WHERE 含 tenant_id 防越权（与 RLS 双重防护）。
        """
        from datetime import datetime as _datetime
        from datetime import timezone as _timezone

        stmt = (
            update(Attachment)
            .where(
                Attachment.id == attachment_id,
                Attachment.tenant_id == tenant_id,
                Attachment.status == "uploading",
            )
            .values(status="ready", updated_at=_datetime.now(_timezone.utc))
            .returning(Attachment)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        row = result.fetchone()
        if row is None:
            raise AttachmentError(
                f"attachment {attachment_id} 不存在或不属于本租户或非 uploading 状态"
            )
        await session.flush()
        attachment = row[0]
        # RETURNING 可能命中 session 身份映射中的旧实例（status 仍为 uploading）；
        # 显式刷新以反映 DB 最新状态。
        await session.refresh(attachment)
        return attachment

    async def get_by_id(
        self,
        *,
        session: AsyncSession,
        attachment_id: UUID,
    ) -> Attachment | None:
        """供下游（如 U05 ProofAttachmentValidator）取 attachment 记录做 6 项校验.

        注：不在此处做 tenant_id 校验 — 由调用方根据业务需求处理（如 ProofAttachmentValidator
        需要明确捕获 tenant_mismatch 并报警）。
        """
        return await session.get(Attachment, attachment_id)


# 便捷单例
attachment_service: AttachmentService = AttachmentService()
