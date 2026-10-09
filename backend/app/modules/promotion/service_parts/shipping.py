"""PromotionService：发货 3 态（流程线 3.3 S2 ~ S4；S1 在建单、S7 随取消）。

顺序（7.1 / 细化 §3）：404 → 状态机 422 → 规则 403（``flow.matrix.require``，端点 scope 已在路由层判过）
→ 条件 UPDATE 0 行 409。推送（S3）的 ★ 在条件 UPDATE 与写入弹窗补的内容之后再判，缺项整笔回滚：
先抢到这一行，并发的输家停在条件 UPDATE、走不到写明细，不会撞 ``uq_promotion_item_style``。
过渡期没有事件表（PR-4），发货动作只写 audit_log。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.modules.auth.models import User
from app.modules.flow.matrix import ensure_gates, require
from app.modules.promotion.enums import ShipStatus
from app.modules.promotion.exceptions import (
    PromotionNotFoundError,
    StateTransitionConflictError,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.receiver import RECEIVER_FIELDS
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionShipPushRequest,
    PromotionShipWithdrawRequest,
)
from app.modules.promotion.service_parts.base import PromotionServiceBase, _utcnow


class PromotionShippingMixin(PromotionServiceBase):
    """纳入发货 / 确认推送仓库 / 撤回推送。"""

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
