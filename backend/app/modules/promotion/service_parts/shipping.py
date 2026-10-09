"""PromotionService：发货 3 态（流程线 3.3 S2 ~ S4；S1 在建单、S7 随取消）。

顺序（7.1 / 细化 §3）：404 → 状态机 422 → 规则 403（``flow.matrix.require``，端点 scope 已在路由层判过）
→ 条件 UPDATE 0 行 409。推送（S3）的 ★ 在条件 UPDATE 与写入弹窗补的内容之后再判，缺项整笔回滚：
先抢到这一行，并发的输家停在条件 UPDATE、走不到写明细，不会撞 ``uq_promotion_item_style``。
过渡期没有事件表（PR-4），发货动作只写 audit_log。
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.security.field_permissions import can_read_field
from app.modules.auth.models import User
from app.modules.flow.matrix import FlowActor, ensure_gates, require, ui_for
from app.modules.promotion.display_name import normalize_goods_short_name
from app.modules.promotion.enums import ShipCourier, ShipStatus
from app.modules.promotion.exceptions import (
    ExportTooManyRowsError,
    PromotionNotFoundError,
    ShippedAtInFutureError,
    StateTransitionConflictError,
)
from app.modules.promotion.flow_doc import build_warehouse_doc
from app.modules.promotion.models import Promotion
from app.modules.promotion.receiver import RECEIVER_FIELDS
from app.modules.promotion.repository import PromotionItemView, WarehouseShipmentRecord
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionShipPushRequest,
    PromotionShipWithdrawRequest,
    PromotionWarehouseWaybillRequest,
    WarehouseShipmentItem,
    WarehouseShipmentPage,
    WarehouseShipmentRow,
)
from app.modules.promotion.service_parts.base import PromotionServiceBase, _utcnow
from app.modules.promotion.shipment_export import (
    EXPORT_ROW_LIMIT,
    ShipmentExportItem,
    ShipmentExportRow,
    build_shipment_workbook,
    count_lines,
)
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.service import UrgeService


def _item_short_name(view: PromotionItemView) -> str:
    return normalize_goods_short_name(view.style_short_name) or view.style_name


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v is not None else None


class PromotionShippingMixin(PromotionServiceBase):
    """纳入发货 / 确认推送仓库 / 撤回推送；仓库页列表、导出与回填（7.4，S5 / S6）。"""

    async def _get_or_404(self, promotion_id: UUID) -> Promotion:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        return promotion

    async def _ship_transition(
        self,
        promotion: Promotion,
        user: User,
        *,
        from_state: str | None,
        to_state: str,
        extra_fields: dict[str, Any] | None = None,
    ) -> Promotion:
        """``ship_status`` 的条件 UPDATE；0 行（别人刚处理过 / 已停用）→ 409。"""
        updated = await self._repo.update_state(
            promotion_id=promotion.id,
            tenant_id=user.tenant_id,
            from_state_field="ship_status",
            from_state_value=from_state,
            to_state_value=to_state,
            extra_fields=extra_fields,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "发货状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion.id)},
            )
        return updated

    async def ship_include(self, promotion_id: UUID, user: User) -> PromotionResponse:
        """S2 纳入发货：历史单（发货为空）→ 待发货。"""
        promotion = await self._get_or_404(promotion_id)
        actor = await self._flow_actor(user)
        require(actor, await self._promotion_doc(promotion), "ship_include")

        updated = await self._ship_transition(
            promotion, user, from_state=None, to_state=ShipStatus.PENDING.value
        )
        await self._audit.log(
            action="promotion.ship.include",
            resource="promotion",
            resource_id=promotion_id,
            before={"ship_status": None},
            after={"ship_status": ShipStatus.PENDING.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user, actor=actor)

    async def ship_push(
        self, promotion_id: UUID, payload: PromotionShipPushRequest, user: User
    ) -> PromotionResponse:
        """S3 确认推送仓库：待发货 → 待打单，写推送时间 / 人；弹窗补的明细与收件同一事务写入。

        ① ② 状态机与规则（``require(gates=False)``）→ 弹窗内容的格式校验（电话、明细，422，不写库）
        → ③ 条件 UPDATE（409）→ ④ 写明细（整组替换，同步 ``promotion.sku_id``）与收件
        → ⑤ 按动作前的阶段重组快照判 ★（422 ``FLOW_GATE_MISSING``），失败连 ③ 一起回滚。
        """
        promotion = await self._get_or_404(promotion_id)
        actor = await self._flow_actor(user)
        doc = await self._promotion_doc(promotion)
        require(actor, doc, "ship_push", gates=False)

        payload = await self._normalize_receiver(payload, user)
        rows = (
            await self._validate_goods_items(
                goods_main_id=promotion.goods_main_id,
                style_id=promotion.style_id,
                items=payload.items,
            )
            if payload.items is not None
            else None
        )
        receiver_set = [f for f in RECEIVER_FIELDS if f in payload.model_fields_set]

        try:
            updated = await self._ship_transition(
                promotion,
                user,
                from_state=ShipStatus.PENDING.value,
                to_state=ShipStatus.PRINTING.value,
                extra_fields={"ship_pushed_at": _utcnow(), "ship_pushed_by": user.id},
            )
            if rows is not None:
                await self._items_repo.replace(
                    tenant_id=updated.tenant_id, promotion_id=updated.id, rows=rows
                )
                main_sku_id = next((s for st, s in rows if st == updated.style_id), None)
                if main_sku_id is not None:
                    updated.sku_id = main_sku_id
            for field in receiver_set:
                setattr(updated, field, getattr(payload, field))
            await self._session.flush()
            ensure_gates(actor, await self._promotion_doc(updated, stage=doc.stage), "ship_push")
            await self._audit.log(
                action="promotion.ship.push",
                resource="promotion",
                resource_id=promotion_id,
                before={"ship_status": ShipStatus.PENDING.value},
                after={
                    "ship_status": ShipStatus.PRINTING.value,
                    "items_replaced": rows is not None,
                    "receiver_changed": receiver_set,
                },
                user_id=user.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise
        return await self._to_response(updated, user, actor=actor)

    async def ship_withdraw(
        self, promotion_id: UUID, payload: PromotionShipWithdrawRequest, user: User
    ) -> PromotionResponse:
        """S4 撤回推送：待打单 → 待发货。推送时间 / 人保留（再推覆盖），原因进 audit_log。"""
        promotion = await self._get_or_404(promotion_id)
        actor = await self._flow_actor(user)
        require(actor, await self._promotion_doc(promotion), "ship_withdraw")

        updated = await self._ship_transition(
            promotion,
            user,
            from_state=ShipStatus.PRINTING.value,
            to_state=ShipStatus.PENDING.value,
        )
        await self._audit.log(
            action="promotion.ship.withdraw",
            resource="promotion",
            resource_id=promotion_id,
            before={"ship_status": ShipStatus.PRINTING.value},
            after={"ship_status": ShipStatus.PENDING.value, "reason": payload.reason},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user, actor=actor)

    # ============================================================
    # 仓库页（7.4）：只回 WarehouseShipmentRow 投影，仓库从任何接口都拿不到整张推广单
    # ============================================================

    async def _warehouse_records(
        self,
        user: User,
        actor: FlowActor,
        *,
        bucket: str,
        keyword: str | None,
        page: int,
        page_size: int,
        promotion_id: UUID | None = None,
    ) -> tuple[builtins.list[WarehouseShipmentRecord], int]:
        thresholds = await UrgeService(self._session).get_urge_thresholds(user.tenant_id)
        return await self._repo.warehouse_shipments(
            tenant_id=user.tenant_id,
            bucket=bucket,
            keyword=(keyword or "").strip() or None,
            search_receiver=can_read_field("promotion", "receiver_name", actor.field_ctx),
            page=page,
            page_size=page_size,
            today=get_today(),
            urge_threshold_days=thresholds.urge_days,
            important_threshold_days=thresholds.important_days,
            promotion_id=promotion_id,
        )

    @staticmethod
    def _receiver_view(record: WarehouseShipmentRecord, actor: FlowActor) -> dict[str, Any]:
        """收件三项逐项过字段规则（仓库行也过注册表，细化 §2）。"""
        return {
            f: (getattr(record, f) if can_read_field("promotion", f, actor.field_ctx) else None)
            for f in RECEIVER_FIELDS
        }

    def _warehouse_row(
        self,
        record: WarehouseShipmentRecord,
        items: builtins.list[PromotionItemView],
        actor: FlowActor,
    ) -> WarehouseShipmentRow:
        doc = build_warehouse_doc(
            stage=record.stage, pr_id=record.pr_id, ship_status=record.ship_status
        )
        actions = ui_for(actor, doc).actions
        return WarehouseShipmentRow(
            id=record.id,
            internal_code=record.internal_code,
            style_code=record.style_code,
            display_short_name=record.display_short_name,
            goods_title=record.goods_title,
            items=[
                WarehouseShipmentItem(
                    display_short_name=_item_short_name(i), color=i.color, size=i.size
                )
                for i in items
            ],
            legacy_color_spec=None if items else record.legacy_color_spec,
            **self._receiver_view(record, actor),
            # 事件表随 M2（PR-4），这之前恒为 false
            receiver_updated_after_push=False,
            items_updated_after_push=False,
            ship_status=record.ship_status,
            ship_pushed_at=record.ship_pushed_at,
            ship_pushed_by_name=record.ship_pushed_by_name,
            ship_courier=record.ship_courier,
            ship_waybill=record.ship_waybill,
            shipped_at=record.shipped_at,
            ui={"actions": {k: a.to_dict() for k, a in actions.items()}},
        )

    async def _items_of(
        self, records: builtins.list[WarehouseShipmentRecord]
    ) -> Mapping[UUID, builtins.list[PromotionItemView]]:
        return await self._items_repo.list_by_promotions([r.id for r in records])

    async def list_warehouse_shipments(
        self, *, bucket: str, keyword: str | None, page: int, page_size: int, user: User
    ) -> WarehouseShipmentPage:
        actor = await self._flow_actor(user)
        records, total = await self._warehouse_records(
            user, actor, bucket=bucket, keyword=keyword, page=page, page_size=page_size
        )
        items = await self._items_of(records)
        page_actions: dict[str, Any] = {}
        if actor.perms.has("promotion_ship", "export"):
            page_actions["export"] = {"state": "enabled"}
        return WarehouseShipmentPage(
            items=[self._warehouse_row(r, items.get(r.id, []), actor) for r in records],
            total=total,
            page=page,
            page_size=page_size,
            couriers=[c.value for c in ShipCourier],
            ui={"actions": page_actions},
        )

    async def export_warehouse_shipments(
        self, *, bucket: str, keyword: str | None, user: User
    ) -> bytes:
        """导出 xlsx：超过 ``EXPORT_ROW_LIMIT`` 张单 → 422 提示缩小范围；每次导出写一条 audit_log。"""
        actor = await self._flow_actor(user)
        records, total = await self._warehouse_records(
            user, actor, bucket=bucket, keyword=keyword, page=1, page_size=EXPORT_ROW_LIMIT
        )
        if total > EXPORT_ROW_LIMIT:
            raise ExportTooManyRowsError(
                f"命中 {total} 张，超过导出上限 {EXPORT_ROW_LIMIT}，请缩小范围",
                details={"total": total, "limit": EXPORT_ROW_LIMIT},
            )
        items = await self._items_of(records)
        rows = [
            ShipmentExportRow(
                internal_code=r.internal_code,
                ship_pushed_at=r.ship_pushed_at,
                **self._receiver_view(r, actor),
                style_code=r.style_code,
                goods_code=r.goods_code,
                sku_code=r.sku_code,
                short_name=r.display_short_name,
                legacy_color_spec=r.legacy_color_spec,
                items=tuple(
                    ShipmentExportItem(
                        style_code=i.style_code,
                        sku_code=i.sku_code,
                        short_name=_item_short_name(i),
                        color=i.color,
                        size=i.size,
                    )
                    for i in items.get(r.id, [])
                ),
            )
            for r in records
        ]
        content = build_shipment_workbook(rows, watermark=None)
        await self._audit.log(
            action="warehouse.shipments.export",
            resource="warehouse_shipment",
            after={
                "bucket": bucket,
                "keyword": (keyword or "").strip() or None,
                "promotions": len(rows),
                "rows": count_lines(rows),
            },
            user_id=user.id,
        )
        await self._session.commit()
        return content

    async def update_warehouse_waybill(
        self, promotion_id: UUID, payload: PromotionWarehouseWaybillRequest, user: User
    ) -> WarehouseShipmentRow:
        """S5 仓库回填（待打单 → 已发货）/ S6 改快递信息（已发货不变）。

        404 → 状态机（只认待打单 / 已发货，422）→ 矩阵（H 列只有管理员，403）→ 发货时间不晚于现在（422）
        → 条件 UPDATE（发货状态没被别人改过，否则 409）。audit_log 记前后值；只回仓库行投影。
        """
        promotion = await self._get_or_404(promotion_id)
        actor = await self._flow_actor(user)
        stage = await self._compute_stage(promotion)
        doc = build_warehouse_doc(
            stage=stage, pr_id=promotion.pr_id, ship_status=promotion.ship_status
        )
        require(actor, doc, "ship_fill")
        now = _utcnow()
        shipped_at = payload.shipped_at or now
        if shipped_at > now:
            raise ShippedAtInFutureError(
                "发货时间不能晚于现在",
                details={"shipped_at": shipped_at.isoformat(), "now": now.isoformat()},
            )
        before = {
            "ship_status": promotion.ship_status,
            "ship_courier": promotion.ship_courier,
            "ship_waybill": promotion.ship_waybill,
            "shipped_at": _iso(promotion.shipped_at),
        }
        await self._ship_transition(
            promotion,
            user,
            from_state=promotion.ship_status,
            to_state=ShipStatus.SHIPPED.value,
            extra_fields={
                "ship_courier": payload.courier.value,
                "ship_waybill": payload.waybill,
                "shipped_at": shipped_at,
            },
        )
        await self._audit.log(
            action="promotion.warehouse_waybill.update",
            resource="promotion",
            resource_id=promotion_id,
            before=before,
            after={
                "ship_status": ShipStatus.SHIPPED.value,
                "ship_courier": payload.courier.value,
                "ship_waybill": payload.waybill,
                "shipped_at": shipped_at.isoformat(),
            },
            user_id=user.id,
        )
        await self._session.commit()
        records, _ = await self._warehouse_records(
            user,
            actor,
            bucket="全部",
            keyword=None,
            page=1,
            page_size=1,
            promotion_id=promotion_id,
        )
        items = await self._items_of(records)
        return self._warehouse_row(records[0], items.get(promotion_id, []), actor)
