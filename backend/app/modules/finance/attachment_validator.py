"""U05 ProofAttachmentValidator — 付款截图 attachment 6 项强校验（FB4）.

按 nfr-design-patterns.md P-U05-02 实施。

校验顺序很重要：
1. 存在性 → 不暴露 attachment 是否存在（FB4 防侧信道）
2. tenant_id（最敏感）→ 跨租户 4 层防御：指标 + Sentry + 独立 audit + 抛异常
3. bucket / purpose / mime / size / status

任一失败抛对应异常（422）。失败均记 Prometheus 指标。
"""

from __future__ import annotations

import logging
from uuid import UUID

import sentry_sdk
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment, AttachmentService
from app.core.audit import AuditService
from app.core.db import AsyncSessionBypass
from app.core.metrics import attachment_validation_failures_total
from app.core.tenancy import bypass_rls_ctx, user_id_ctx

from app.modules.finance.exceptions import (
    AttachmentNotReadyError,
    AttachmentTooLargeError,
    InvalidAttachmentBucketError,
    InvalidAttachmentMimeError,
    InvalidAttachmentPurposeError,
    InvalidAttachmentReferenceError,
)


log = logging.getLogger(__name__)


ALLOWED_MIME: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "application/pdf"}
)

MAX_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

EXPECTED_BUCKET: str = "private"
EXPECTED_PURPOSE: str = "settlement_proof"
EXPECTED_STATUS: str = "ready"


class ProofAttachmentValidator:
    """付款截图 attachment 6 项强校验（FB4）。

    校验项：
    1. 存在性 → 不暴露 attachment 是否存在
    2. tenant_id（最敏感）→ 4 层跨租户防御
    3. bucket = "private"
    4. purpose = "settlement_proof"
    5. mime_type ∈ ALLOWED_MIME
    6. size_bytes ≤ MAX_SIZE_BYTES
    7. status = "ready"

    （上面写"6 项"实际是 6 + 存在性 = 7 项；
    "6 项"是 NFR §4 编号惯例，存在性不计入业务校验项。）
    """

    def __init__(self, attachment_service: AttachmentService) -> None:
        self._service = attachment_service

    async def validate(
        self,
        *,
        session: AsyncSession,
        attachment_id: UUID,
        tenant_id: UUID,
    ) -> Attachment:
        """6 项校验全部通过返回 Attachment 实例；任一失败抛对应异常."""
        attachment = await self._service.get_by_id(
            session=session, attachment_id=attachment_id
        )
        if attachment is None:
            self._record_failure("not_found")
            raise InvalidAttachmentReferenceError(
                "attachment 不存在或已删除",
                details={"attachment_id": str(attachment_id)},
            )

        # 1. tenant_id（防越权 + 4 层防御）
        if attachment.tenant_id != tenant_id:
            await self._handle_cross_tenant_attempt(
                attachment_id=attachment_id,
                expected_tenant_id=tenant_id,
                actual_tenant_id=attachment.tenant_id,
            )
            raise InvalidAttachmentReferenceError(
                "attachment 不属于当前租户",
                details={"attachment_id": str(attachment_id)},
            )

        # 2. bucket
        if attachment.bucket != EXPECTED_BUCKET:
            self._record_failure("bucket_invalid")
            raise InvalidAttachmentBucketError(
                f"attachment.bucket={attachment.bucket}，要求 {EXPECTED_BUCKET}",
                details={
                    "actual": attachment.bucket,
                    "expected": EXPECTED_BUCKET,
                },
            )

        # 3. purpose
        if attachment.purpose != EXPECTED_PURPOSE:
            self._record_failure("purpose_invalid")
            raise InvalidAttachmentPurposeError(
                f"attachment.purpose={attachment.purpose}，要求 {EXPECTED_PURPOSE}",
                details={
                    "actual": attachment.purpose,
                    "expected": EXPECTED_PURPOSE,
                },
            )

        # 4. mime_type
        if attachment.mime_type not in ALLOWED_MIME:
            self._record_failure("mime_invalid")
            raise InvalidAttachmentMimeError(
                f"attachment.mime_type={attachment.mime_type} 不在白名单",
                details={
                    "actual": attachment.mime_type,
                    "allowed": sorted(ALLOWED_MIME),
                },
            )

        # 5. size_bytes
        if attachment.size_bytes > MAX_SIZE_BYTES:
            self._record_failure("size_too_large")
            raise AttachmentTooLargeError(
                f"attachment.size_bytes={attachment.size_bytes} 超过 {MAX_SIZE_BYTES}",
                details={
                    "actual_bytes": attachment.size_bytes,
                    "max_bytes": MAX_SIZE_BYTES,
                },
            )

        # 6. status
        if attachment.status != EXPECTED_STATUS:
            self._record_failure("status_not_ready")
            raise AttachmentNotReadyError(
                f"attachment.status={attachment.status}，要求 {EXPECTED_STATUS}",
                details={
                    "actual": attachment.status,
                    "expected": EXPECTED_STATUS,
                },
            )

        return attachment

    def _record_failure(self, failure_type: str) -> None:
        """所有失败均记 Prometheus 指标（attachment_validation_failures_total）。"""
        attachment_validation_failures_total.labels(
            failure_type=failure_type, source_module="finance"
        ).inc()

    async def _handle_cross_tenant_attempt(
        self,
        *,
        attachment_id: UUID,
        expected_tenant_id: UUID,
        actual_tenant_id: UUID,
    ) -> None:
        """跨租户尝试 4 层防御（FB4）：指标 + Sentry + 独立 audit + 抛异常.

        本方法不抛异常 — 由调用方完成 unique entry point。
        """
        # 1. 指标
        self._record_failure("tenant_mismatch")

        # 2. Sentry warning（每次都上报，不去重 — 安全事件优先级高）
        try:
            sentry_sdk.capture_message(
                "potential_cross_tenant_attempt",
                level="warning",
                extras={
                    "attachment_id": str(attachment_id),
                    "expected_tenant_id": str(expected_tenant_id),
                    "actual_tenant_id": str(actual_tenant_id),
                    "user_id": (
                        str(user_id_ctx.get()) if user_id_ctx.get() else None
                    ),
                    "source_module": "finance",
                },
            )
        except Exception:  # noqa: BLE001
            log.exception("sentry_capture_cross_tenant_failed")

        # 3. 独立 bypass session 写 audit（防被原事务回滚带走）
        bypass_token = bypass_rls_ctx.set(True)
        try:
            try:
                async with AsyncSessionBypass() as audit_session:
                    audit = AuditService(audit_session)
                    await audit.log(
                        action="settlement.attachment_cross_tenant_attempt",
                        resource="settlement",
                        resource_id=None,
                        actor_type="anonymous",
                        user_id=user_id_ctx.get(),
                        after={
                            "attempted_attachment_id": str(attachment_id),
                            "user_tenant_id": str(expected_tenant_id),
                            "source_module": "finance",
                        },
                    )
                    await audit_session.commit()
            except Exception as audit_exc:  # noqa: BLE001
                # 兜底：audit 失败仅 log，不阻塞原异常上抛
                log.exception(
                    "audit_for_cross_tenant_failed",
                    extra={"audit_error": type(audit_exc).__name__},
                )
        finally:
            bypass_rls_ctx.reset(bypass_token)


__all__ = [
    "ALLOWED_MIME",
    "EXPECTED_BUCKET",
    "EXPECTED_PURPOSE",
    "EXPECTED_STATUS",
    "MAX_SIZE_BYTES",
    "ProofAttachmentValidator",
]
