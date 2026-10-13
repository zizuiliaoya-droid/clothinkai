"""时间线读接口 ``LineEventService.read`` 与留言 ``comment``（流程线设计 6.5、7.6、7.8）。

路由只要求登录，授权全在这里，三层过滤：
1. 能不能看到这条线（任一）：线上有推广单且 ``promotion:read``；谈款对当前用户可见（与谈款列表同一个判断）；
   线上有结款单且 ``finance.settlement:read``。都不满足 → 404 ``LINE_NOT_FOUND``（不回 403，免得暴露单据存在）
2. 能不能看到这组事件：``can_read_field("line_event", <分组>)``（个人授予 / 撤销照常生效）；再加行级规则——
   谈款段事件（``negotiation_id`` 非空）只给看得到这张谈款的人，看不到的折成 ``hidden_segments`` 一行。
   分组级就看不到的直接丢、不进 ``hidden_segments``（计划 D5 偏差）
3. 能不能看到这个字段：payload 过 ``filter_payload``（白名单）；附件过 ``attachment_readable`` 之后才签名

``legacy``（上线前记录）：这条线第一条事件之前、按单据字段推出来的时间点（无事件 = 全部），同样过第 2 层
（分组 + 谈款行级）；只出动作与原因，不带金额、收件。``next_step``：有推广单按推广阶段，否则按看得到的谈款状态。
推广单头（``promotion``）与推广侧 ``next_step`` 只给第 1 层经推广单或结款单进来的人；只靠谈款进来的
``promotion = null``，线上已有推广单时 ``next_step = null``。
查询：动作人权限 2 条（``load_flow_actor``）+ 线头 1 条 + 事件 1 条 + 附件 / 动作人名各 1 条（没有就不查）
+ 催发阈值 1 条（线上有推广单时算阶段）+ 第一条事件时间 1 条（只在超 500 条截断时），与事件条数无关；
列表接口不 join 事件表。

留言 `comment`：第 1 层与读接口同一个判断；再要 `negotiation:write` 或 `promotion:write`、矩阵「留言 / 贴图」可点。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment, attachment_service, check_image_payload
from app.core.exceptions import PermissionDeniedError
from app.core.security.field_permissions import FieldPermissionContext, can_read_field
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.exceptions import (
    LineCommentEmptyError,
    LineCommentImageInvalidError,
    LineNotFoundError,
)
from app.modules.flow.line_events import (
    EVENT_GROUP,
    LineEventRepository,
    LineEventType,
    attachment_readable,
    filter_payload,
)
from app.modules.flow.matrix import FlowActor, FlowDocBase, require, ui_for
from app.modules.flow.models import LineEvent
from app.modules.flow.next_step import next_step
from app.modules.flow.schemas import (
    HiddenSegment,
    LegacyPoint,
    LineTimelineResponse,
    TimelineActor,
    TimelineAttachment,
    TimelineEvent,
    TimelineNegotiation,
    TimelineNextStep,
    TimelinePromotion,
)
from app.modules.flow.timeline_repository import LineHeader, LineReadRepository
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.flow_doc import build_negotiation_doc
from app.modules.negotiation.permissions import SCOPE_NEGOTIATION
from app.modules.negotiation.service import can_see_negotiation
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.flow_doc import build_promotion_doc
from app.modules.promotion.stage_calculator import compute_stage
from app.modules.promotion.urge_calculator import DEFAULT_TENANT_TZ, get_today
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
# 留言贴图（7.6）：jpg / png / webp、≤ 10MB，一次 1 张，私有桶
COMMENT_IMAGE_PURPOSE = "line_comment_image"
COMMENT_IMAGE_MAX_BYTES = 10 * 1024 * 1024


def _day_start(value: date) -> datetime:
    """日期字段（发布日期、付款日期）按当天 00:00（Asia/Shanghai）当时间点。"""
    return datetime.combine(value, time.min, tzinfo=DEFAULT_TENANT_TZ)


def _with_reason(text: str, reason: str | None) -> str:
    return f"{text}：{reason}" if reason else text


def legacy_points(
    header: LineHeader,
    *,
    first_event_at: datetime | None,
    ctx: FieldPermissionContext,
    sees_negotiation: bool,
) -> list[LegacyPoint]:
    """上线前记录（6.5）：第一条事件之前的字段时间点（没有事件 = 全部），最新在上。

    同样过第 2 层：按点所属的事件分组 ``can_read_field("line_event", 组)``，谈款的点还要看得到这张谈款（行级）；
    看不到的直接不出（不计入 ``hidden_segments``：那里数的是事件）。只出动作与原因，金额、收件不进文案。
    发货两点（推送、发货）是计划 D7 加的：PR-2 ~ PR-4 之间的推送 / 回填没有事件；
    两条迁移标记（``settled_at`` / ``completed_at``）的列随 PR-6 / M5 才有，那时再加。
    """
    # (分组, 时间, 文案, 来源字段)，按业务先后排列：同一时刻的几点，后发生的排在上面
    points: list[tuple[str, datetime | date | None, str, str]] = []
    neg = header.negotiation
    if neg is not None and sees_negotiation:
        rejected = neg.status == NegotiationStatus.REJECTED.value
        points += [
            ("negotiation", neg.created_at, "PR 新建谈款", "negotiation.created_at"),
            ("negotiation", neg.submitted_at, "PR 提交谈款审核", "negotiation.submitted_at"),
            (
                "negotiation",
                neg.reviewed_at,
                _with_reason(
                    "主管驳回谈款" if rejected else "主管审核通过谈款", neg.review_opinion
                ),
                "negotiation.reviewed_at",
            ),
        ]
    promo = header.promotion
    if promo is not None:
        rejected_review = promo.review_action == ReviewAction.REJECT.value
        category = promo.review_reason_category
        review_text = (
            _with_reason(
                f"主管驳回推广（{category}）" if category else "主管驳回推广", promo.review_reason
            )
            if rejected_review
            else "主管审核通过推广"
        )
        points += [
            ("promotion", promo.created_at, "新建推广单", "promotion.created_at"),
            ("shipping", promo.ship_pushed_at, "推送仓库发货", "promotion.ship_pushed_at"),
            ("shipping", promo.shipped_at, "仓库已发货", "promotion.shipped_at"),
            (
                "promotion",
                promo.actual_publish_date,
                "博主发布",
                "promotion.actual_publish_date",
            ),
            ("settlement", promo.reviewed_at, review_text, "promotion.reviewed_at"),
            (
                "settlement",
                header.settlement_payment_date,
                "财务付款",
                "settlement.payment_date",
            ),
            (
                "promotion",
                promo.metrics_recorded_at,
                "PR 录入 7 天数据",
                "promotion.metrics_recorded_at",
            ),
            ("retro", promo.retro_confirmed_at, "主管确认复盘", "promotion.retro_confirmed_at"),
        ]
    out: list[tuple[datetime, int, LegacyPoint]] = []
    for order, (group, raw, text, source) in enumerate(points):
        if raw is None:
            continue
        at = raw if isinstance(raw, datetime) else _day_start(raw)
        if first_event_at is not None and at >= first_event_at:
            continue
        if not can_read_field("line_event", group, ctx):
            continue
        out.append((at, order, LegacyPoint(at=at, text=text, source=source)))
    out.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [point for _, _, point in out]


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

    async def _visible_line(
        self, line_id: UUID, user: User
    ) -> tuple[FlowActor, LineHeader, bool, bool]:
        """第 1 层：返回 ``(actor, header, 看得到谈款, 看得到推广单头)``；线上任何单据都看不到 → 404。

        推广单头（编号、阶段与推广侧 ``next_step``）只给经推广单（``promotion:read``）或结款单
        （``finance.settlement:read``）进来的人；只靠谈款进来的（默认财务）看不到（设计 5.3 A ~ D 列财务「—」）。
        """
        actor = await load_flow_actor(user.id, self._roles, self._perms)
        header = await self._lines.header(tenant_id=user.tenant_id, line_id=line_id)
        promotion, negotiation = header.promotion, header.negotiation
        sees_promotion = promotion is not None and actor.perms.has("promotion", "read")
        sees_negotiation = negotiation is not None and can_see_negotiation(
            actor, pr_id=negotiation.pr_id, status=negotiation.status
        )
        sees_settlement = header.has_settlement and actor.perms.has("finance.settlement", "read")
        if not (sees_promotion or sees_negotiation or sees_settlement):
            raise LineNotFoundError(details={"line_id": str(line_id)})
        return actor, header, sees_negotiation, sees_promotion or sees_settlement

    async def comment(
        self,
        line_id: UUID,
        user: User,
        *,
        text: str | None,
        image: tuple[str | None, str | None, bytes] | None,
    ) -> TimelineEvent:
        """留言 / 贴图（7.6，N11 与推广阶段共用）：写一条 ``line.comment``，返回这条事件（6.5 的形状）。

        判定顺序：第 1 层看不到 → 404；``negotiation:write`` / ``promotion:write`` 都不持 → 403；
        矩阵「留言 / 贴图」不可点 → 403（与读接口 ``ui.actions.comment`` 同一份快照）；
        文字（去首尾空白）与图片都空 → 422 ``LINE_COMMENT_EMPTY``；图片不合法 → 422 ``LINE_COMMENT_IMAGE_INVALID``。
        挂哪一边（6.3）：线上已有推广单只挂 ``promotion_id``，还没有只挂 ``negotiation_id``。
        图片后端代传私有桶（用途 ``line_comment_image``）；事件写失败整单回滚并删掉已传的对象。
        """
        actor, header, _, _ = await self._visible_line(line_id, user)
        if not (
            actor.perms.has(SCOPE_NEGOTIATION, "write") or actor.perms.has("promotion", "write")
        ):
            raise PermissionDeniedError(
                details={"required_scope": f"{SCOPE_NEGOTIATION}:write | promotion:write"}
            )
        _, doc = await self._doc(actor, header)
        require(actor, doc, "comment")
        note = (text or "").strip() or None
        if note is None and image is None:
            raise LineCommentEmptyError()
        if image is not None:
            filename, mime_type, data = image
            problem = check_image_payload(
                data=data,
                mime_type=mime_type,
                filename=filename,
                max_bytes=COMMENT_IMAGE_MAX_BYTES,
                label="留言图片",
            )
            # mime_type 为 None 时 check_image_payload 必然已给出原因；一并判断是为了收窄类型
            if problem is not None or mime_type is None:
                raise LineCommentImageInvalidError(problem)

        promotion, negotiation = header.promotion, header.negotiation
        if promotion is not None:
            promotion_id, negotiation_id = promotion.id, None
        elif negotiation is not None:
            promotion_id, negotiation_id = None, negotiation.id
        else:  # 第 1 层已挡住：线上至少有一张单
            raise LineNotFoundError()

        # (附件 id, r2_key)：rollback 之后 ORM 属性已过期，要先取出来
        stored: tuple[UUID, str] | None = None
        try:
            if image is not None:
                attachment = await self._store_image(image, user)
                stored = (attachment.id, attachment.r2_key)
            event = await self._events.append(
                tenant_id=user.tenant_id,
                line_id=line_id,
                event_type=LineEventType.LINE_COMMENT,
                actor_id=user.id,
                note=note,
                attachment_id=stored[0] if stored is not None else None,
                promotion_id=promotion_id,
                negotiation_id=negotiation_id,
            )
            seq, created_at = event.seq, event.created_at
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if stored is not None:
                await self._delete_object(stored[1], stored[0])
            raise
        attachments = await self._signed_attachments({stored[0]}) if stored is not None else {}
        names = await self._lines.actor_names({user.id})
        return TimelineEvent(
            seq=seq,
            event_type=LineEventType.LINE_COMMENT.value,
            group=GROUP_LABELS["comment"],
            created_at=created_at,
            actor=TimelineActor(id=user.id, display_name=names.get(user.id, "")),
            from_state=None,
            to_state=None,
            note=note,
            attachment=attachments.get(stored[0]) if stored is not None else None,
            payload={},
        )

    async def _store_image(
        self, image: tuple[str | None, str | None, bytes], user: User
    ) -> Attachment:
        """代传留言贴图到私有桶（调用方已校验），返回 ``ready`` 的附件行。"""
        filename, mime_type, data = image
        attachment, _ = await self._attachment_service.create_upload_record(
            session=self._session,
            tenant_id=user.tenant_id,
            created_by=user.id,
            bucket="private",
            purpose=COMMENT_IMAGE_PURPOSE,
            filename=filename,
            mime_type=str(mime_type),
            size_bytes=len(data),
        )
        await run_in_threadpool(
            self._attachment_service.upload_bytes,
            data,
            bucket="private",
            key=attachment.r2_key,
            content_type=str(mime_type),
        )
        return await self._attachment_service.mark_uploaded(
            session=self._session, attachment_id=attachment.id, tenant_id=user.tenant_id
        )

    async def _delete_object(self, key: str, attachment_id: UUID) -> None:
        """事务回滚后删掉已传的对象，不留孤儿；删不掉只记日志，原错误照常抛。"""
        try:
            await run_in_threadpool(self._attachment_service.delete, "private", key)
        except Exception:
            log.warning(
                "line_comment_image_compensation_delete_failed",
                extra={"attachment_id": str(attachment_id)},
            )

    async def read(self, line_id: UUID, user: User) -> LineTimelineResponse:
        actor, header, sees_negotiation, sees_promotion_head = await self._visible_line(
            line_id, user
        )
        tenant_id = user.tenant_id
        promotion, negotiation = header.promotion, header.negotiation

        rows = await self._events.list_by_line(
            tenant_id=tenant_id, line_id=line_id, newest_first=True, limit=TIMELINE_LIMIT + 1
        )
        truncated = len(rows) > TIMELINE_LIMIT
        rows = rows[:TIMELINE_LIMIT]
        # legacy 的截止点：倒序取的，最后一条就是第一条事件；截断了才另查
        if truncated:
            first_event_at = await self._lines.first_event_at(tenant_id=tenant_id, line_id=line_id)
        else:
            first_event_at = rows[-1].created_at if rows else None

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
        # 线上已有推广单、他又看不到推广单头：不给下一步（谈款「已定稿」那格本来也是 None）
        step = (
            None
            if promotion is not None and not sees_promotion_head
            else next_step(
                promotion_stage=stage,
                negotiation_status=(
                    negotiation.status if negotiation is not None and sees_negotiation else None
                ),
            )
        )
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
                if promotion is not None and stage is not None and sees_promotion_head
                else None
            ),
            next_step=(
                TimelineNextStep(text=step.text, who=step.who) if step is not None else None
            ),
            events=events,
            hidden_segments=hidden_segments,
            legacy=legacy_points(
                header,
                first_event_at=first_event_at,
                ctx=ctx,
                sees_negotiation=sees_negotiation,
            ),
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
                retro_status=promotion.retro_status,
                metrics_recorded_at=promotion.metrics_recorded_at,
                actual_publish_date=promotion.actual_publish_date,
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


__all__ = [
    "COMMENT_IMAGE_MAX_BYTES",
    "COMMENT_IMAGE_PURPOSE",
    "GROUP_LABELS",
    "HIDDEN_NEGOTIATION_REASON",
    "TIMELINE_LIMIT",
    "LineEventService",
    "legacy_points",
]
