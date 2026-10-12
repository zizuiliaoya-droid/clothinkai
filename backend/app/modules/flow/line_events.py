"""主线事件的类型表与只追加仓储（流程线设计 6.3、6.4、7.8）。

- ``LineEventType``：6.3 的全部事件类型（库里不加 CHECK，加类型只改这里）
- ``EVENT_GROUP``：事件类型 → 字段规则键（``line_event.<键>``，6.5 第 2 层按它判能不能看到这组事件）
- ``LineEventRepository``：只有 ``append()`` 与 ``list_by_line()``，没有 update / delete（库里 ``clothing_app``
  也没有 UPDATE / DELETE，迁移 063）。``append()`` 只 add + flush、不提交：业务回滚时事件一起消失；
  写不进去就让整个动作失败（时间线是业务留痕，不吞异常）

- ``PAYLOAD_FIELD_RULES`` / ``ATTACHMENT_FIELD_RULES`` / ``filter_payload``：6.5 第 3 层（payload 键白名单、收款码类附件）
读接口 ``LineEventService.read`` 在 ``flow/timeline.py``（6.5）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import FieldPermissionContext, can_read_field
from app.modules.flow.exceptions import LineEventPayloadTooLargeError
from app.modules.flow.models import LineEvent

# 单条 payload 上限（6.3）：按 jsonb 文本口径（UTF-8 字节，``", "`` / ``": "`` 分隔）算，
# 与上线后核对的 ``octet_length(payload::text) > 8192`` 同一把尺子
PAYLOAD_MAX_BYTES = 8192


class LineEventType(str, Enum):
    """事件类型，命名 ``<对象>.<动作>``（6.3）。"""

    # 谈款
    NEGOTIATION_CREATED = "negotiation.created"
    NEGOTIATION_COPIED = "negotiation.copied"
    NEGOTIATION_COPIED_OUT = "negotiation.copied_out"
    NEGOTIATION_SUBMITTED = "negotiation.submitted"
    NEGOTIATION_MANAGER_APPROVED = "negotiation.manager_approved"
    NEGOTIATION_MANAGER_REJECTED = "negotiation.manager_rejected"
    NEGOTIATION_FINAL_APPROVED = "negotiation.final_approved"
    NEGOTIATION_FINAL_RETURNED = "negotiation.final_returned"
    NEGOTIATION_RECEIVER_SAVED = "negotiation.receiver_saved"
    NEGOTIATION_FINALIZED = "negotiation.finalized"
    NEGOTIATION_REASSIGNED = "negotiation.reassigned"
    # 留言
    LINE_COMMENT = "line.comment"
    # 推广
    PROMOTION_CREATED = "promotion.created"
    PROMOTION_IMPORTED = "promotion.imported"
    PROMOTION_UPDATED = "promotion.updated"
    PROMOTION_PUBLISHED = "promotion.published"
    PROMOTION_CANCELLED = "promotion.cancelled"
    PROMOTION_BRAND_COMMENT_UPLOADED = "promotion.brand_comment_uploaded"
    PROMOTION_RETURN_WAYBILL_SET = "promotion.return_waybill_set"
    PROMOTION_METRICS_RECORDED = "promotion.metrics_recorded"
    URGE_TASK_OPENED = "urge.task_opened"
    URGE_SENT = "urge.sent"
    URGE_COMPLETED = "urge.completed"
    URGE_REOPENED = "urge.reopened"
    URGE_CLOSED = "urge.closed"
    RECALL_STARTED = "recall.started"
    RECALL_WAYBILL_UPLOADED = "recall.waybill_uploaded"
    RECALL_SUCCEEDED = "recall.succeeded"
    RECALL_FAILED = "recall.failed"
    PROMOTION_COMPLETED = "promotion.completed"
    # 发货
    SHIP_INCLUDED = "ship.included"
    SHIP_PUSHED = "ship.pushed"
    SHIP_WITHDRAWN = "ship.withdrawn"
    SHIP_FILLED = "ship.filled"
    SHIP_WAYBILL_CHANGED = "ship.waybill_changed"
    SHIP_RECEIVER_CHANGED = "ship.receiver_changed"
    SHIP_ITEMS_CHANGED = "ship.items_changed"
    # 结款
    PROMOTION_PAYMENT_QR_UPLOADED = "promotion.payment_qr_uploaded"
    REVIEW_APPROVED = "review.approved"
    REVIEW_REJECTED = "review.rejected"
    REVIEW_RESUBMITTED = "review.resubmitted"
    SETTLEMENT_CREATED = "settlement.created"
    SETTLEMENT_EXTRA_ITEM_ADDED = "settlement.extra_item_added"
    SETTLEMENT_PAYMENT_FILLED = "settlement.payment_filled"
    SETTLEMENT_PAID = "settlement.paid"
    SETTLEMENT_REJECTED = "settlement.rejected"
    SETTLEMENT_RESUBMITTED = "settlement.resubmitted"
    SETTLEMENT_LEGACY_APPROVED = "settlement.legacy_approved"
    PROMOTION_SETTLED = "promotion.settled"
    FREIGHT_SUBMITTED = "freight.submitted"
    FREIGHT_APPROVED = "freight.approved"
    FREIGHT_REJECTED = "freight.rejected"
    FREIGHT_PAID = "freight.paid"
    FREIGHT_RESUBMITTED = "freight.resubmitted"
    # 复盘
    RETRO_WRITTEN = "retro.written"
    RETRO_EDITED = "retro.edited"
    # 旧复盘的确认 / 打回（PR-4 补写现有动作；PR-7 复盘 3 态之后不再写）
    RETRO_CONFIRMED = "retro.confirmed"
    RETRO_RETURNED = "retro.returned"


# 字段规则键（6.5 第 2 层；个人授予 / 撤销的 scope 是 ``field.line_event.<键>:read``，迁移 063 入册）
EVENT_GROUP_KEYS: tuple[str, ...] = (
    "negotiation",
    "comment",
    "promotion",
    "shipping",
    "settlement",
    "retro",
)

_T = LineEventType
_GROUP_MEMBERS: dict[str, tuple[LineEventType, ...]] = {
    "negotiation": (
        _T.NEGOTIATION_CREATED,
        _T.NEGOTIATION_COPIED,
        _T.NEGOTIATION_COPIED_OUT,
        _T.NEGOTIATION_SUBMITTED,
        _T.NEGOTIATION_MANAGER_APPROVED,
        _T.NEGOTIATION_MANAGER_REJECTED,
        _T.NEGOTIATION_FINAL_APPROVED,
        _T.NEGOTIATION_FINAL_RETURNED,
        _T.NEGOTIATION_RECEIVER_SAVED,
        _T.NEGOTIATION_FINALIZED,
        _T.NEGOTIATION_REASSIGNED,
    ),
    "comment": (_T.LINE_COMMENT,),
    "promotion": (
        _T.PROMOTION_CREATED,
        _T.PROMOTION_IMPORTED,
        _T.PROMOTION_UPDATED,
        _T.PROMOTION_PUBLISHED,
        _T.PROMOTION_CANCELLED,
        _T.PROMOTION_BRAND_COMMENT_UPLOADED,
        _T.PROMOTION_RETURN_WAYBILL_SET,
        _T.PROMOTION_METRICS_RECORDED,
        _T.URGE_TASK_OPENED,
        _T.URGE_SENT,
        _T.URGE_COMPLETED,
        _T.URGE_REOPENED,
        _T.URGE_CLOSED,
        _T.RECALL_STARTED,
        _T.RECALL_WAYBILL_UPLOADED,
        _T.RECALL_SUCCEEDED,
        _T.RECALL_FAILED,
        _T.PROMOTION_COMPLETED,
    ),
    "shipping": (
        _T.SHIP_INCLUDED,
        _T.SHIP_PUSHED,
        _T.SHIP_WITHDRAWN,
        _T.SHIP_FILLED,
        _T.SHIP_WAYBILL_CHANGED,
        _T.SHIP_RECEIVER_CHANGED,
        _T.SHIP_ITEMS_CHANGED,
    ),
    "settlement": (
        _T.PROMOTION_PAYMENT_QR_UPLOADED,
        _T.REVIEW_APPROVED,
        _T.REVIEW_REJECTED,
        _T.REVIEW_RESUBMITTED,
        _T.SETTLEMENT_CREATED,
        _T.SETTLEMENT_EXTRA_ITEM_ADDED,
        _T.SETTLEMENT_PAYMENT_FILLED,
        _T.SETTLEMENT_PAID,
        _T.SETTLEMENT_REJECTED,
        _T.SETTLEMENT_RESUBMITTED,
        _T.SETTLEMENT_LEGACY_APPROVED,
        _T.PROMOTION_SETTLED,
        _T.FREIGHT_SUBMITTED,
        _T.FREIGHT_APPROVED,
        _T.FREIGHT_REJECTED,
        _T.FREIGHT_PAID,
        _T.FREIGHT_RESUBMITTED,
    ),
    "retro": (_T.RETRO_WRITTEN, _T.RETRO_EDITED, _T.RETRO_CONFIRMED, _T.RETRO_RETURNED),
}

EVENT_GROUP: dict[LineEventType, str] = {
    event_type: group for group, members in _GROUP_MEMBERS.items() for event_type in members
}
"""事件类型 → 字段规则键。单测断言每个 ``LineEventType`` 恰好登记一次。"""

# 推广单 promotion.created 的来源（payload.source；上线后核对 SQL 按「直接新建」筛）
PROMOTION_SOURCE_DIRECT = "直接新建"
PROMOTION_SOURCE_NEGOTIATION = "谈款定稿"
# promotion.imported 的来源（导入站外推广，每行一条）
PROMOTION_SOURCE_IMPORT = "导入站外推广"


class PayloadContainer:
    """``PAYLOAD_FIELD_RULES`` 的标记值：这个键的值是 ``{键: …}``，内层按键再查同一张表。"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "CONTAINER"


CONTAINER = PayloadContainer()

_Q: tuple[str, str] = ("promotion", "quote_amount")

# 6.5 第 3 层：payload 键白名单（没登记的键读时一律删，单测守）。``snapshot`` / ``diff`` 的内层与顶层共用这张表，
# 键按字段名登记：服务费、应付 → 服务费规则；收件三项 → 各自的收件规则；其余是分组内都能看的关键值。
# 加 payload 键要先登记到这里，否则读接口看不到（``tests/flow_event_helpers.assert_payload_registered`` 守写入侧）。
# 值：``(entity, field)`` = 过这条字段规则；``None`` = 跟着分组走（过了第 2 层就能看）；``CONTAINER`` = 内层再查。
PAYLOAD_FIELD_RULES: dict[str, tuple[str, str] | PayloadContainer | None] = {
    "snapshot": CONTAINER,
    "diff": CONTAINER,
    # 金额与收件
    "quote_amount": _Q,
    "payable": _Q,
    "receiver_name": ("promotion", "receiver_name"),
    "receiver_phone": ("promotion", "receiver_phone"),
    "receiver_address": ("promotion", "receiver_address"),
    # 谈款：提交快照 / diff、定稿、复制、转交（PR-3）
    "blogger_id": None,
    "style_id": None,
    "goods_main_id": None,
    "goods_items": None,
    "cooperation_mode": None,
    "platform": None,
    "scheduled_publish_date": None,
    "promotion_id": None,
    "negotiation_id": None,
    "from_negotiation_id": None,
    "from_line_id": None,
    "to_negotiation_id": None,
    "to_line_id": None,
    "from_pr_id": None,
    "from_pr_name": None,
    "to_pr_id": None,
    "to_pr_name": None,
    # 推广：建单 / 导入、PATCH、发布 / 取消、录 7 天数据、寄回单号
    "source": None,
    "batch_id": None,
    "row_number": None,
    "system": None,
    "sku_id": None,
    "note_title": None,
    "remark": None,
    "is_active": None,
    "source_extra": None,
    "amount_fields": None,
    "publish_url": None,
    "actual_publish_date": None,
    "by_cancel": None,
    "like_count": None,
    "collect_count": None,
    "comment_count": None,
    "return_waybill": None,
    # 催发
    "trigger": None,
    "urge_task_id": None,
    "urge_record_id": None,
    "urge_count": None,
    "close_reason": None,
    # 推广审核
    "destination": None,
    "reject_category": None,
    # 复盘
    "retrospective_id": None,
    # 发货：推送明细、明细前后值、快递信息（收件 diff 走上面的收件规则）
    "items": None,
    "items_before": None,
    "items_after": None,
    "reason": None,
    "courier": None,
    "waybill": None,
    "shipped_at": None,
    "ship_courier": None,
    "ship_waybill": None,
}

# 附件的字段规则（6.5 第 3 层「收款码类附件」）：事件类型（``.value``）→ (entity, field)。没列的类型附件只跟分组走
ATTACHMENT_FIELD_RULES: dict[str, tuple[str, str]] = {
    LineEventType.PROMOTION_PAYMENT_QR_UPLOADED.value: ("promotion", "payment_qr"),
}


def filter_payload(payload: Mapping[str, Any], ctx: FieldPermissionContext) -> dict[str, Any]:
    """按 ``PAYLOAD_FIELD_RULES`` 裁剪 payload（返回新 dict，不改入参）。

    没登记的键删（超管也删：白名单是防漏登记，不是权限）；规则不可读删（``field_filter`` 语义，不置 null）；
    容器键的值不是 dict 就整键删，是 dict 就按同一张表逐键过（``diff`` 的条目按字段名过滤）。
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in PAYLOAD_FIELD_RULES:
            continue
        rule = PAYLOAD_FIELD_RULES[key]
        if isinstance(rule, PayloadContainer):
            if isinstance(value, Mapping):
                out[key] = filter_payload(value, ctx)
            continue
        if rule is not None and not can_read_field(rule[0], rule[1], ctx):
            continue
        out[key] = value
    return out


def attachment_readable(event_type: str, ctx: FieldPermissionContext) -> bool:
    """这类事件的附件能不能签给当前用户（第 3 层；第 2 层分组由调用方先判）。"""
    # 枚举成员的 hash 按成员名算，查以 .value 为键的表要先取值（库里读出来的是普通 str）
    key = event_type.value if isinstance(event_type, LineEventType) else event_type
    rule = ATTACHMENT_FIELD_RULES.get(key)
    return rule is None or can_read_field(rule[0], rule[1], ctx)


def payload_size(payload: dict[str, Any]) -> int:
    """按 jsonb 文本口径算 payload 的 UTF-8 字节数。"""
    return len(json.dumps(payload, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


class LineEventRepository:
    """只追加：公开方法只有 ``append`` 与 ``list_by_line``（单测断言没有别的）。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        tenant_id: UUID,
        line_id: UUID,
        event_type: LineEventType,
        actor_id: UUID | None,
        negotiation_id: UUID | None = None,
        promotion_id: UUID | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        note: str | None = None,
        attachment_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> LineEvent:
        """写一条事件：只 add + flush，不提交（同事务，6.4）。

        ``tenant_id`` 显式取单据的租户，不依赖上下文（导入 runner、进程内监听里也能直接用）。
        ``negotiation_id`` / ``promotion_id`` 至少一个（库里 ``ck_line_event_owner``）。
        payload 超过 ``PAYLOAD_MAX_BYTES`` → 422 ``LINE_EVENT_PAYLOAD_TOO_LARGE``，整个动作跟着失败。
        """
        body = dict(payload or {})
        size = payload_size(body)
        if size > PAYLOAD_MAX_BYTES:
            raise LineEventPayloadTooLargeError(
                event_type=event_type.value, size=size, limit=PAYLOAD_MAX_BYTES
            )
        event = LineEvent(
            tenant_id=tenant_id,
            line_id=line_id,
            negotiation_id=negotiation_id,
            promotion_id=promotion_id,
            event_type=event_type.value,
            from_state=from_state,
            to_state=to_state,
            note=note,
            attachment_id=attachment_id,
            payload=body,
            actor_id=actor_id,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_by_line(
        self,
        *,
        tenant_id: UUID,
        line_id: UUID,
        newest_first: bool = False,
        limit: int | None = None,
    ) -> list[LineEvent]:
        """一条主线的事件，按 ``seq`` 排（同一事务的几条 ``created_at`` 可能相同，6.4）。

        读接口用 ``newest_first=True``（最新在上）+ ``limit``（上限 + 1，多出来那条只用来判 ``truncated``），
        走 ``idx_line_event_line (tenant_id, line_id, seq)``。
        """
        order = LineEvent.seq.desc() if newest_first else LineEvent.seq
        stmt = (
            select(LineEvent)
            .where(LineEvent.tenant_id == tenant_id, LineEvent.line_id == line_id)
            .order_by(order)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self._session.execute(stmt)).scalars().all())


__all__ = [
    "ATTACHMENT_FIELD_RULES",
    "CONTAINER",
    "EVENT_GROUP",
    "EVENT_GROUP_KEYS",
    "PAYLOAD_FIELD_RULES",
    "PAYLOAD_MAX_BYTES",
    "PROMOTION_SOURCE_DIRECT",
    "PROMOTION_SOURCE_IMPORT",
    "PROMOTION_SOURCE_NEGOTIATION",
    "LineEventRepository",
    "LineEventType",
    "PayloadContainer",
    "attachment_readable",
    "filter_payload",
    "payload_size",
]
