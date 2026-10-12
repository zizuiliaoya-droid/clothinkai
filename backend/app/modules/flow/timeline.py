"""时间线读接口 ``LineEventService.read``（流程线设计 6.5、7.8）。

路由只要求登录，授权全在这里，三层过滤：
1. 能不能看到这条线（任一）：线上有推广单且 ``promotion:read``；谈款对当前用户可见（与谈款列表同一个判断）；
   线上有结款单且 ``finance.settlement:read``。都不满足 → 404 ``LINE_NOT_FOUND``（不回 403，免得暴露单据存在）
2. 能不能看到这组事件：``can_read_field("line_event", <分组>)``（个人授予 / 撤销照常生效）；再加行级规则——
   谈款段事件（``negotiation_id`` 非空）只给看得到这张谈款的人，看不到的折成 ``hidden_segments`` 一行。
   分组级就看不到的直接丢、不进 ``hidden_segments``（计划 D5 偏差）
3. 能不能看到这个字段：payload 过 ``filter_payload``（白名单）；附件过 ``attachment_readable`` 之后才签名

查询：动作人权限 2 条（``load_flow_actor``）+ 线头 1 条 + 事件 1 条 + 附件 / 动作人名各 1 条（没有就不查）
+ 催发阈值 1 条（线上有推广单时算阶段），与事件条数无关；列表接口不 join 事件表。
``legacy``、``next_step`` 随 S5 接入。
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.security.field_permissions import can_read_field
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.exceptions import LineNotFoundError
from app.modules.flow.line_events import (
    EVENT_GROUP,
    LineEventRepository,
    LineEventType,
    attachment_readable,
    filter_payload,
)
from app.modules.flow.matrix import FlowActor, FlowDocBase, ui_for
from app.modules.flow.models import LineEvent
from app.modules.flow.schemas import (
    HiddenSegment,
    LineTimelineResponse,
    TimelineActor,
    TimelineAttachment,
    TimelineEvent,
    TimelineNegotiation,
    TimelinePromotion,
)
from app.modules.flow.timeline_repository import LineHeader, LineReadRepository
from app.modules.negotiation.flow_doc import build_negotiation_doc
from app.modules.negotiation.service import can_see_negotiation
from app.modules.promotion.flow_doc import build_promotion_doc
from app.modules.promotion.stage_calculator import compute_stage
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.service import UrgeService

log = logging.getLogger(__name__)

# 6.5「排序与上限」：不分页，硬上限 500 条
TIMELINE_LIMIT = 500
# 6.5 第 2 层：规则键 → 分组中文名（响应里的 ``group``）
GROUP_LABELS: dict[str, str] = {
    "negotiation": "谈款",
    "comment": "留言",
    "promotion": "推广",
    "shipping": "发货",
    "settlement": "结款",
    "retro": "复盘",
}
HIDDEN_NEGOTIATION_REASON = "谈款过程只给谈款人和能看全部谈款的人看"
# 附件签名时长与推广响应一致
ATTACHMENT_URL_TTL = 900


def _group_of(event_type: str) -> str | None:
    """库里读出的是普通 str：先转枚举再查表；不认识的类型（以后加了没登记）不出。"""
    try:
        return EVENT_GROUP[LineEventType(event_type)]
    except (ValueError, KeyError):
        return None


class LineEventService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = LineEventRepository(session)
        self._lines = LineReadRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._attachment_service = attachment_service

    async def read(self, line_id: UUID, user: User) -> LineTimelineResponse:
        actor = await load_flow_actor(user.id, self._roles, self._perms)
        tenant_id = user.tenant_id
        header = await self._lines.header(tenant_id=tenant_id, line_id=line_id)
        promotion, negotiation = header.promotion, header.negotiation

        # 第 1 层
        sees_promotion = promotion is not None and actor.perms.has("promotion", "read")
        sees_negotiation = negotiation is not None and can_see_negotiation(
            actor, pr_id=negotiation.pr_id, status=negotiation.status
        )
        sees_settlement = header.has_settlement and actor.perms.has("finance.settlement", "read")
        if not (sees_promotion or sees_negotiation or sees_settlement):
            raise LineNotFoundError(details={"line_id": str(line_id)})

        rows = await self._events.list_by_line(
            tenant_id=tenant_id, line_id=line_id, newest_first=True, limit=TIMELINE_LIMIT + 1
        )
        truncated = len(rows) > TIMELINE_LIMIT
        rows = rows[:TIMELINE_LIMIT]

        # 第 2 层：分组 → 行级
        ctx = actor.field_ctx
        visible: list[tuple[LineEvent, str]] = []
        hidden = 0
        for event in rows:
            group = _group_of(event.event_type)
            if group is None or not can_read_field("line_event", group, ctx):
                continue
            if event.negotiation_id is not None and not sees_negotiation:
                hidden += 1
                continue
            visible.append((event, group))

        # 第 3 层：附件先过规则再签名（一次批量取）
        attachment_ids = {
            e.attachment_id
            for e, _ in visible
            if e.attachment_id is not None and attachment_readable(e.event_type, ctx)
        }
        attachments = await self._signed_attachments(attachment_ids)
        names = await self._lines.actor_names(
            {e.actor_id for e, _ in visible if e.actor_id is not None}
        )

        events = [
            TimelineEvent(
                seq=e.seq,
                event_type=e.event_type,
                group=GROUP_LABELS[group],
                created_at=e.created_at,
                actor=(
                    TimelineActor(id=e.actor_id, display_name=names.get(e.actor_id, ""))
                    if e.actor_id is not None
                    else None
                ),
                from_state=e.from_state,
                to_state=e.to_state,
                note=e.note,
                attachment=(
                    attachments.get(e.attachment_id) if e.attachment_id is not None else None
                ),
                payload=filter_payload(e.payload, ctx),
            )
            for e, group in visible
        ]
        hidden_segments = (
            [
                HiddenSegment(
                    group=GROUP_LABELS["negotiation"],
                    count=hidden,
                    reason=HIDDEN_NEGOTIATION_REASON,
                )
            ]
            if hidden
            else []
        )

        stage, doc = await self._doc(actor, header)
        comment = ui_for(actor, doc).actions.get("comment")
        return LineTimelineResponse(
            line_id=line_id,
            negotiation=(
                TimelineNegotiation(id=negotiation.id, status=negotiation.status)
                if negotiation is not None and sees_negotiation
                else None
            ),
            promotion=(
                TimelinePromotion(
                    id=promotion.id, internal_code=promotion.internal_code, stage=stage
                )
                if promotion is not None and stage is not None
                else None
            ),
            events=events,
            hidden_segments=hidden_segments,
            truncated=truncated,
            ui={"actions": {"comment": comment.to_dict()} if comment is not None else {}},
        )

    async def _doc(self, actor: FlowActor, header: LineHeader) -> tuple[str | None, FlowDocBase]:
        """留言按钮的矩阵快照（与 ``POST /comments`` 同一份）：有推广单按推广单，否则按谈款。

        「留言 / 贴图」这一格不看卡点，``items_complete`` 不影响结果，这里不为它多查明细。
        返回的阶段只在线上有推广单时非空（推广头信息用）。
        """
        promotion, negotiation = header.promotion, header.negotiation
        if promotion is not None:
            thresholds = await UrgeService(self._session).get_urge_thresholds(promotion.tenant_id)
            stage = compute_stage(
                publish_status=promotion.publish_status,
                recall_status=promotion.recall_status,
                settlement_status=promotion.settlement_status,
                ship_status=promotion.ship_status,
                is_active=promotion.is_active,
                scheduled_publish_date=promotion.scheduled_publish_date,
                today=get_today(),
                thresholds=thresholds,
            )
            return stage, build_promotion_doc(
                promotion,
                stage=stage,
                negotiator_id=negotiation.pr_id if negotiation is not None else None,
                items_complete=False,
            )
        if negotiation is None:  # 第 1 层已挡住：线上至少有一张单
            raise LineNotFoundError()
        return None, build_negotiation_doc(negotiation, items_complete=False)

    async def _signed_attachments(self, ids: set[UUID]) -> dict[UUID, TimelineAttachment]:
        refs = await self._lines.attachments(ids)
        out: dict[UUID, TimelineAttachment] = {}
        for ref in refs.values():
            url: str | None
            try:
                url = self._attachment_service.get_signed_url(
                    "private", ref.r2_key, expires_in=ATTACHMENT_URL_TTL
                )
            except Exception:
                log.warning(
                    "line_event_attachment_url_failed", extra={"attachment_id": str(ref.id)}
                )
                url = None
            out[ref.id] = TimelineAttachment(id=ref.id, url=url, mime=ref.mime_type)
        return out


__all__ = ["GROUP_LABELS", "HIDDEN_NEGOTIATION_REASON", "TIMELINE_LIMIT", "LineEventService"]
