"""PromotionService：收款码：上传初始化 / 上传 / 绑定 / 删除（从 service.py 搬出）。"""

from __future__ import annotations

from uuid import UUID

from app.modules.auth.models import User
from app.modules.promotion.exceptions import (
    InvalidPaymentQrAttachmentError,
    PromotionNotFoundError,
)
from app.modules.promotion.schemas import (
    PromotionPaymentQrBindRequest,
    PromotionPaymentQrUploadInitRequest,
    PromotionPaymentQrUploadInitResponse,
    PromotionResponse,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    log,
)


class PromotionPaymentQrMixin(PromotionServiceBase):
    """收款码：上传初始化 / 上传 / 绑定 / 删除。"""

    async def init_payment_qr_upload(
        self, promotion_id: UUID, payload: PromotionPaymentQrUploadInitRequest, user: User
    ) -> PromotionPaymentQrUploadInitResponse:
        if await self._repo.get_by_id(promotion_id) is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        allowed = {"image/jpeg", "image/png", "image/webp"}
        if payload.mime_type not in allowed:
            raise InvalidPaymentQrAttachmentError(
                "收款码仅支持 JPG、PNG、WebP 图片",
                details={"allowed": sorted(allowed), "actual": payload.mime_type},
            )
        attachment, url = await self._attachment_service.create_upload_record(
            session=self._session,
            tenant_id=user.tenant_id,
            created_by=user.id,
            bucket="private",
            purpose="promotion_payment_qr",
            filename=payload.filename,
            mime_type=payload.mime_type,
            size_bytes=payload.size_bytes,
        )
        await self._session.commit()
        return PromotionPaymentQrUploadInitResponse(attachment_id=attachment.id, presigned_url=url)

    async def upload_payment_qr(
        self,
        promotion_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> PromotionResponse:
        """由后端代传收款码到私有 R2，避免浏览器直传依赖 bucket CORS。"""
        from fastapi.concurrency import run_in_threadpool

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        allowed = {"image/jpeg", "image/png", "image/webp"}
        if mime_type not in allowed:
            raise InvalidPaymentQrAttachmentError(
                "收款码仅支持 JPG、PNG、WebP 图片",
                details={"allowed": sorted(allowed), "actual": mime_type},
            )
        if not data:
            raise InvalidPaymentQrAttachmentError("收款码图片不能为空")
        if len(data) > 10 * 1024 * 1024:
            raise InvalidPaymentQrAttachmentError("收款码图片不能超过 10MB")
        if filename is not None and len(filename) > 255:
            raise InvalidPaymentQrAttachmentError("收款码文件名不能超过 255 个字符")

        valid_signature = (
            (mime_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
            or (mime_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (
                mime_type == "image/webp"
                and len(data) >= 12
                and data[:4] == b"RIFF"
                and data[8:12] == b"WEBP"
            )
        )
        if not valid_signature:
            raise InvalidPaymentQrAttachmentError("收款码图片内容与声明格式不一致")

        attachment = None
        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="promotion_payment_qr",
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(data),
            )
            attachment_id = attachment.id
            attachment_key = attachment.r2_key
            await run_in_threadpool(
                self._attachment_service.upload_bytes,
                data,
                bucket="private",
                key=attachment_key,
                content_type=mime_type,
            )
            await self._attachment_service.mark_uploaded(
                session=self._session,
                attachment_id=attachment_id,
                tenant_id=user.tenant_id,
            )
            old_id = promotion.payment_qr_attachment_id
            promotion.payment_qr_attachment_id = attachment_id
            await self._audit.log(
                action="promotion.payment_qr.bind",
                resource="promotion",
                resource_id=promotion.id,
                before={"attachment_changed": old_id is not None},
                after={"attachment_changed": True},
                user_id=user.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if attachment_key is not None:
                try:
                    await run_in_threadpool(
                        self._attachment_service.delete,
                        "private",
                        attachment_key,
                    )
                except Exception:
                    log.warning(
                        "promotion_payment_qr_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(promotion, user)

    async def bind_payment_qr(
        self, promotion_id: UUID, payload: PromotionPaymentQrBindRequest, user: User
    ) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        attachment = await self._attachment_service.get_by_id(
            session=self._session, attachment_id=payload.payment_qr_attachment_id
        )
        if (
            attachment is None
            or attachment.tenant_id != user.tenant_id
            or attachment.bucket != "private"
            or attachment.purpose != "promotion_payment_qr"
            or attachment.mime_type not in {"image/jpeg", "image/png", "image/webp"}
            or attachment.size_bytes > 10 * 1024 * 1024
            or attachment.status != "ready"
        ):
            raise InvalidPaymentQrAttachmentError("收款码附件无效或尚未上传完成")
        old_id = promotion.payment_qr_attachment_id
        promotion.payment_qr_attachment_id = attachment.id
        await self._audit.log(
            action="promotion.payment_qr.bind",
            resource="promotion",
            resource_id=promotion.id,
            before={"attachment_changed": old_id is not None},
            after={"attachment_changed": True},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(promotion, user)

    async def remove_payment_qr(self, promotion_id: UUID, user: User) -> None:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        promotion.payment_qr_attachment_id = None
        await self._audit.log(
            action="promotion.payment_qr.remove",
            resource="promotion",
            resource_id=promotion.id,
            after={"attachment_removed": True},
            user_id=user.id,
        )
        await self._session.commit()
