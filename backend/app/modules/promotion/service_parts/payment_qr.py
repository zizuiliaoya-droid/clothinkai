"""PromotionService：收款码：上传初始化 / 上传 / 绑定 / 删除（从 service.py 搬出）。"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import IllegalStateTransitionError
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.promotion.enums import PublishStatus, RecallStatus, SettlementStatus
from app.modules.promotion.exceptions import (
    InvalidPaymentQrAttachmentError,
    PaymentQrLockedError,
    PromotionNotFoundError,
)
from app.modules.promotion.flow_matrix import PAYMENT_QR_GREY_HINT
from app.modules.promotion.models import Promotion
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
from app.modules.promotion.stage_calculator import STAGE_FALLBACK

# 7.3 收款码接口 ①：没发布（未发布 / 异常）且不在召回中 → 「发布后上传」，管理员也一样
_BEFORE_PUBLISH = frozenset({PublishStatus.UNPUBLISHED.value, PublishStatus.ABNORMAL.value})
# ①'：H 列只读（PR / 主管）。「已完结 · 召回」是没有 completed_at 前的过渡阶段，PR-7 起改走「已完结只读」
_READONLY_STAGES = frozenset(
    {"已停用", "已删除", "已完结 · 不合作", "已完结 · 召回", STAGE_FALLBACK}
)
_READONLY_REASON = "已停用 / 已取消，只读"
# ③：服务费已付 / 已结款（召回中、寄回运费付款单已驳回两个例外随 PR-8）
_SETTLED = frozenset({SettlementStatus.PAID.value, SettlementStatus.SETTLED.value})
# ④ 放行只剩 D 列、E 列的待财务付款（结款单待付款）与结款驳回、G 列召回中
_OPEN_STAGES = frozenset(
    {"待主管审核", "推广驳回 · 待重提", "待财务付款", "结款驳回 · 待重提", "召回中"}
)
# 结款单状态（推广模块不 import finance，同 review 的 R2 ⑥）
_SETTLEMENT_PAYING = "待财务付款"


class PromotionPaymentQrMixin(PromotionServiceBase):
    """收款码：上传初始化 / 上传 / 绑定 / 删除。"""

    async def _load_payment_qr_target(
        self, promotion_id: UUID, user: User
    ) -> tuple[Promotion, str | None]:
        """四个收款码接口共用：取单（含已停用，好报「只读」而不是 404）并判能不能传 / 换。

        返回推广单与它结款单的状态（没有结款单为 None，DELETE 要用）。
        """
        promotion = await self._writable_promotion(promotion_id, user, include_inactive=True)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        settlement_status = await self._assert_payment_qr_changeable(promotion, user)
        return promotion, settlement_status

    async def _assert_payment_qr_changeable(self, promotion: Promotion, user: User) -> str | None:
        """收款码什么时候能传、能换（流程线 7.3 收款码接口，矩阵 5.3 ⁹ 与之一致）。

        按顺序判，持 ``*`` 的管理员只受 ①：
        ① 发布状态 ∈ {未发布, 异常} 且不在召回中 → 422 ``ILLEGAL_STATE_TRANSITION``「发布后上传」；
        ①' 已停用 / 已删除 / 状态异常 / 已完结 · 不合作（及过渡的已完结 · 召回）→ 422「已停用 / 已取消，只读」；
        ② 结款单 = 待财务付款 → 409 ``PAYMENT_QR_LOCKED``（``lock = settlement_paying``）；
        ③ 推广单 已付款 / 已结款 → 409 ``PAYMENT_QR_LOCKED``（``lock = settled``）；
        ④ 其余只放行 D 列、E 列的待财务付款与结款驳回、G 列召回中。
        """
        if (
            promotion.publish_status in _BEFORE_PUBLISH
            and promotion.recall_status != RecallStatus.RECALLING.value
        ):
            raise IllegalStateTransitionError(
                PAYMENT_QR_GREY_HINT,
                details={"publish_status": promotion.publish_status},
            )
        brief = await self._repo.settlement_brief(
            tenant_id=promotion.tenant_id, promotion_id=promotion.id
        )
        settlement_status = brief.settlement_status if brief is not None else None
        if (await self._flow_actor(user)).is_star:
            return settlement_status
        stage = await self._compute_stage(promotion)
        if stage in _READONLY_STAGES:
            raise IllegalStateTransitionError(_READONLY_REASON, details={"stage": stage})
        if settlement_status == _SETTLEMENT_PAYING:
            raise PaymentQrLockedError(
                "财务付款中，收款码已锁定；码不对请让财务驳回后重传",
                details={"lock": "settlement_paying"},
            )
        if promotion.settlement_status in _SETTLED:
            raise PaymentQrLockedError(
                "已结款，收款码已锁定",
                details={"lock": "settled", "settlement_status": promotion.settlement_status},
            )
        if stage not in _OPEN_STAGES:
            raise IllegalStateTransitionError(_READONLY_REASON, details={"stage": stage})
        return settlement_status

    async def init_payment_qr_upload(
        self, promotion_id: UUID, payload: PromotionPaymentQrUploadInitRequest, user: User
    ) -> PromotionPaymentQrUploadInitResponse:
        await self._load_payment_qr_target(promotion_id, user)
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

        promotion, _ = await self._load_payment_qr_target(promotion_id, user)

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
            await self._append_payment_qr_uploaded(promotion, user, attachment_id)
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
        promotion, _ = await self._load_payment_qr_target(promotion_id, user)
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
        if old_id != attachment.id:
            await self._append_payment_qr_uploaded(promotion, user, attachment.id)
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

    async def _append_payment_qr_uploaded(
        self, promotion: Promotion, user: User, attachment_id: UUID
    ) -> None:
        """时间线（流程线 6.3）：每次上传 / 换绑一条带附件的事件，重传后旧码仍挂在时间线上；
        附件读时过 `promotion.payment_qr` 字段规则（6.5 第 3 层）。"""
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.PROMOTION_PAYMENT_QR_UPLOADED,
            actor_id=user.id,
            promotion_id=promotion.id,
            attachment_id=attachment_id,
        )

    async def remove_payment_qr(self, promotion_id: UUID, user: User) -> None:
        """删码：先过同一套锁；结款单已生成后一律拒绝（管理员也一样，7.3「结款单已生成后 DELETE 拒绝」）。"""
        promotion, settlement_status = await self._load_payment_qr_target(promotion_id, user)
        if settlement_status is not None:
            raise PaymentQrLockedError(
                "已生成结款单，收款码不能删除，只能更换",
                details={"lock": "settlement_created", "settlement_status": settlement_status},
            )
        promotion.payment_qr_attachment_id = None
        await self._audit.log(
            action="promotion.payment_qr.remove",
            resource="promotion",
            resource_id=promotion.id,
            after={"attachment_removed": True},
            user_id=user.id,
        )
        await self._session.commit()
