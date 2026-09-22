"""U16 OrderAdjustmentService（拍单自动生成 + 刷单录入 + 金额表达式解析）。"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment, attachment_service, check_image_payload
from app.core.audit import AuditService
from app.core.metrics import order_adjustment_auto_created_total
from app.modules.finance.enums import OrderAdjustmentStatus, OrderType
from app.modules.finance.exceptions import (
    AmountExpressionInvalidError,
    InvalidOrderPaymentQrError,
    OrderAdjustmentNotFoundError,
)
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.finance.order_adjustment_repository import (
    OrderAdjustmentRepository,
)
from app.modules.finance.order_adjustment_schemas import (
    BrushingCreate,
    OrderAdjustmentListFilters,
    OrderAdjustmentPage,
    OrderAdjustmentResponse,
)
from app.modules.product.models import Style

log = logging.getLogger(__name__)

_NUM = r"\d+(?:\.\d{1,2})?"
_EXPR = re.compile(rf"^\s*({_NUM})\s*(?:-\s*({_NUM}))?\s*$")

_QR_PURPOSE = "order_adjustment_payment_qr"
_QR_MAX_BYTES = 10 * 1024 * 1024


def parse_amount_expr(raw: str | Decimal) -> Decimal:
    """解析金额："数字" 或 "原价-返现"（如 "100-30" → 70）。不使用 eval。"""
    if isinstance(raw, Decimal):
        return raw
    m = _EXPR.match(str(raw))
    if not m:
        raise AmountExpressionInvalidError(f"非法金额格式: {raw}")
    try:
        base = Decimal(m.group(1))
        rebate = Decimal(m.group(2)) if m.group(2) else Decimal("0")
    except InvalidOperation as exc:
        raise AmountExpressionInvalidError(f"非法金额: {raw}") from exc
    amount = base - rebate
    if amount < 0:
        raise AmountExpressionInvalidError(f"金额不能为负: {raw}")
    return amount


class OrderAdjustmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrderAdjustmentRepository(session)

    async def auto_create_from_promotion(self, promo: Any) -> OrderAdjustment | None:
        """EP06-S09：promotion.in_store_order=true 时自动生成拍单（幂等）。"""
        existing = await self._repo.get_by_promotion(promo.id)
        if existing is not None:
            order_adjustment_auto_created_total.labels(result="skipped").inc()
            return existing
        row = OrderAdjustment(
            order_type=OrderType.STORE_ORDER.value,
            order_date=promo.cooperation_date,
            style_id=promo.style_id,
            sku_id=getattr(promo, "sku_id", None),
            blogger_identifier=str(promo.blogger_id),
            promotion_id=promo.id,
            amount=Decimal("0"),
            exclude_from_roi=False,
            status=OrderAdjustmentStatus.PENDING_PAYMENT.value,
        )
        self._repo.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            order_adjustment_auto_created_total.labels(result="skipped").inc()
            return None
        order_adjustment_auto_created_total.labels(result="created").inc()
        return row

    async def create_brushing(self, payload: BrushingCreate, user: Any) -> dict:
        """EP06-S10：刷单录入，exclude_from_roi 默认 true，金额表达式解析。"""
        amount = parse_amount_expr(payload.amount_expr)
        duplicate = False
        if payload.order_no:
            duplicate = await self._repo.exists_order_no(payload.order_no)
        row = OrderAdjustment(
            order_type=OrderType.BRUSHING.value,
            order_date=payload.order_date,
            order_no=payload.order_no,
            style_id=payload.style_id,
            sku_id=payload.sku_id,
            blogger_identifier=payload.blogger_identifier,
            amount=amount,
            exclude_from_roi=True,
            status=OrderAdjustmentStatus.PENDING_PAYMENT.value,
            remark=payload.remark,
        )
        self._repo.add(row)
        await self._session.flush()
        await AuditService(self._session).log(
            "finance.order.brushing_create",
            resource="order_adjustment",
            resource_id=row.id,
            user_id=user.id,
        )
        await self._session.commit()
        return {
            "id": row.id,
            "order_type": row.order_type,
            "amount": amount,
            "exclude_from_roi": True,
            "status": row.status,
            "order_no": row.order_no,
            "duplicate": duplicate,
        }

    async def list(
        self,
        *,
        filters: OrderAdjustmentListFilters | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> OrderAdjustmentPage:
        rows, total = await self._repo.list_paginated(
            filters=filters or OrderAdjustmentListFilters(),
            page=page,
            page_size=page_size,
        )
        style_map = await self._style_snapshot(rows)
        qr_urls = await self._payment_qr_urls(rows)
        items = [self._build_response(r, style_map, qr_urls) for r in rows]
        return OrderAdjustmentPage(items=items, total=total, page=page, page_size=page_size)

    # ----------------------- 收款码 ----------------------- #

    async def upload_payment_qr(
        self,
        row_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: Any,
    ) -> OrderAdjustmentResponse:
        """后端代传收款码到私有 R2（不依赖浏览器直传与 bucket CORS）。"""
        row = await self._repo.get_by_id(row_id)
        if row is None:
            raise OrderAdjustmentNotFoundError(f"单据 {row_id} 不存在")

        reason = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=_QR_MAX_BYTES,
            label="收款码",
        )
        # mime_type is None 时 check_image_payload 必然已返回原因；这里一并判断是为了
        # 把类型收窄成 str，后面 R2 上传要用它当 content_type。
        if reason is not None or mime_type is None:
            raise InvalidOrderPaymentQrError(reason or "收款码格式无法识别")

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose=_QR_PURPOSE,
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(data),
            )
            attachment_id = attachment.id
            attachment_key = attachment.r2_key
            await run_in_threadpool(
                attachment_service.upload_bytes,
                data,
                bucket="private",
                key=attachment_key,
                content_type=mime_type,
            )
            await attachment_service.mark_uploaded(
                session=self._session,
                attachment_id=attachment_id,
                tenant_id=user.tenant_id,
            )
            old_id = row.payment_qr_attachment_id
            row.payment_qr_attachment_id = attachment_id
            await AuditService(self._session).log(
                "finance.order.payment_qr_bind",
                resource="order_adjustment",
                resource_id=row.id,
                before={"attachment_changed": old_id is not None},
                after={"attachment_changed": True},
                user_id=user.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if attachment_key is not None:
                try:
                    await run_in_threadpool(attachment_service.delete, "private", attachment_key)
                except Exception:
                    log.warning(
                        "order_adjustment_payment_qr_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(row)

    async def remove_payment_qr(self, row_id: UUID, user: Any) -> None:
        row = await self._repo.get_by_id(row_id)
        if row is None:
            raise OrderAdjustmentNotFoundError(f"单据 {row_id} 不存在")
        row.payment_qr_attachment_id = None
        await AuditService(self._session).log(
            "finance.order.payment_qr_remove",
            resource="order_adjustment",
            resource_id=row.id,
            after={"attachment_removed": True},
            user_id=user.id,
        )
        await self._session.commit()

    # ----------------------- private ----------------------- #

    async def _style_snapshot(self, rows: Sequence[OrderAdjustment]) -> dict[UUID, tuple[str, str]]:
        """反范式富化：批量取款式编码/名称（对齐 final.xlsx 拍单/刷单的「款式/款号」）。"""
        style_ids = {r.style_id for r in rows if r.style_id is not None}
        if not style_ids:
            return {}
        result = await self._session.execute(
            select(Style.id, Style.style_code, Style.style_name).where(Style.id.in_(style_ids))
        )
        return {s.id: (s.style_code, s.style_name) for s in result.all()}

    async def _payment_qr_urls(self, rows: Sequence[OrderAdjustment]) -> dict[UUID, str]:
        """批量换取收款码签名 URL；只对已上传完成（ready）的附件签发。"""
        wanted = {r.payment_qr_attachment_id for r in rows if r.payment_qr_attachment_id}
        if not wanted or not attachment_service.is_configured:
            return {}
        result = await self._session.execute(
            select(Attachment.id, Attachment.r2_key, Attachment.status).where(
                Attachment.id.in_(wanted)
            )
        )
        keys = {a.id: a.r2_key for a in result.all() if a.status == "ready" and a.r2_key}
        urls: dict[UUID, str] = {}
        for row in rows:
            key = keys.get(row.payment_qr_attachment_id) if row.payment_qr_attachment_id else None
            if key is None:
                continue
            try:
                urls[row.id] = attachment_service.get_signed_url("private", key, expires_in=900)
            except Exception:
                log.warning(
                    "order_adjustment_payment_qr_sign_failed",
                    extra={"order_adjustment_id": str(row.id)},
                )
        return urls

    @staticmethod
    def _build_response(
        row: OrderAdjustment,
        style_map: Mapping[UUID, tuple[str, str]],
        qr_urls: Mapping[UUID, str],
    ) -> OrderAdjustmentResponse:
        snapshot = style_map.get(row.style_id) if row.style_id is not None else None
        return OrderAdjustmentResponse(
            id=row.id,
            order_type=row.order_type,
            order_date=row.order_date,
            order_no=row.order_no,
            style_id=row.style_id,
            sku_id=row.sku_id,
            style_code=snapshot[0] if snapshot else None,
            style_name=snapshot[1] if snapshot else None,
            blogger_identifier=row.blogger_identifier,
            amount=row.amount,
            payment_amount=row.payment_amount,
            payment_date=row.payment_date,
            exclude_from_roi=row.exclude_from_roi,
            status=row.status,
            promotion_id=row.promotion_id,
            remark=row.remark,
            payment_qr_attachment_id=row.payment_qr_attachment_id,
            payment_qr_signed_url=qr_urls.get(row.id),
        )

    async def _to_response(self, row: OrderAdjustment) -> OrderAdjustmentResponse:
        return self._build_response(
            row,
            await self._style_snapshot([row]),
            await self._payment_qr_urls([row]),
        )


__all__ = ["OrderAdjustmentService", "parse_amount_expr"]
