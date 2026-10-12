"""谈款服务层（PRD V1.4 模块一；流程线 PR-3 起 7 态两级审核，设计 3.2）。

状态流转（转移表 N1 ~ N12，状态机在 ``flow_doc.TRANSITIONS``，谁能动在 ``flow_matrix``）：

    草稿 --提交--> 主管待审 --主管通过--> 待老板审核 --老板定稿--> 待补收货地址 --确认收货--> 已定稿
                       \\--主管驳回--> 审核驳回（终态，重谈用「复制为新谈款」）
                                              \\--老板退回--> 老板退回 --PR 改完重提--> 主管待审

几条规则刻意放在 service 而不是只靠前端：

- 每个动作的顺序固定（7.1）：取单，看不到 → 404；``flow.matrix.require`` 判状态机 422 → 规则 403 → ★ 422；
  条件 UPDATE 0 行 → 409 ``NEGOTIATION_STATE_CONFLICT``。事件与业务写入同一事务
- 置换模式博主服务费强制 0（PRD：接口也要校验防止绕过前端传值）
- 持 ``*`` 的账号不能建谈款（管理员自己建的谈款没人能定稿，11-1）
"""

from __future__ import annotations

import builtins
import dataclasses
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import FieldPermissionDenied, IllegalStateTransitionError, ValidationError
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.repository import BloggerRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.exceptions import FlowActionForbiddenError
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import (
    FlowActor,
    Level,
    View,
    cell_level,
    ensure_gates,
    require,
    ui_for,
)
from app.modules.negotiation.enums import (
    NegotiationFinalReviewAction,
    NegotiationReviewAction,
    NegotiationStatus,
)
from app.modules.negotiation.exceptions import (
    InvalidNegotiationAssigneeError,
    InvalidNegotiationBloggerError,
    InvalidNegotiationGoodsError,
    InvalidNegotiationStyleError,
    NegotiationNotFoundError,
    NegotiationSelfReviewForbiddenError,
    NegotiationStateConflictError,
)
from app.modules.negotiation.flow_doc import NegotiationDoc, build_negotiation_doc
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.permissions import SCOPE_NEGOTIATION, SCOPE_NEGOTIATION_ALL
from app.modules.negotiation.repository import NegotiationItemView, NegotiationRepository
from app.modules.negotiation.schemas import (
    BloggerCooperationHistory,
    BloggerCooperationItem,
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationGoodsMembers,
    NegotiationItemResponse,
    NegotiationListFilters,
    NegotiationListResponse,
    NegotiationReassignRequest,
    NegotiationReceiverConfirm,
    NegotiationReceiverUpdate,
    NegotiationRejectionItem,
    NegotiationResponse,
    NegotiationReviewRequest,
    NegotiationUpdate,
    OthersInProgressItem,
    ReceiverSuggestion,
)
from app.modules.promotion.display_name import normalize_goods_short_name
from app.modules.promotion.enums import CooperationMode, ShipStatus
from app.modules.promotion.exceptions import InvalidReceiverPhoneError, InvalidSkuReferenceError
from app.modules.promotion.flow_doc import goods_items_complete
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
)
from app.modules.promotion.receiver import RECEIVER_FIELDS, normalize_receiver_phone
from app.modules.promotion.repository import PromotionItemRepository, PromotionRepository
from app.modules.promotion.schemas import GoodsItemIn, GoodsMemberResponse, PromotionCreate
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import DEFAULT_TENANT_TZ

log = logging.getLogger("app.modules.negotiation.service")

# 能编辑 / 提交的状态（N2、N3）：草稿、老板退回。状态机本身在 flow_doc.TRANSITIONS，这里只给条件 UPDATE 用
_OWNER_EDITABLE = (NegotiationStatus.DRAFT.value, NegotiationStatus.FINAL_RETURNED.value)

OVERDUE_AFTER = timedelta(hours=36)
"""主管待审超过这么久算超时（7c-6，按实际时长比）。"""
OVERDUE_COUNT_KEY = "主管待审超时"
"""``status-counts`` 里超时数的键（7.2）。"""
STAR_CREATE_REASON = "管理员建的谈款没人能定稿"
"""持 ``*`` 不能建谈款（N1、11-1）：页级新建按钮的禁用原因，也是 ``star_create`` 的文案。"""
FINALIZED_CONFLICT_MESSAGE = "已定稿，推广单已生成"
"""重复确认收货信息（N9）：已定稿，或并发里输给 ``uq_promotion_line`` 的那个请求 → 409 的文案。"""
_PROMOTION_LINE_UQ = "uq_promotion_line"
# 管理员能转交的状态（N12）：要 PR 动手或还在推进的 4 个；草稿只有本人看得到（取不到 → 404），终态不转
_REASSIGNABLE = (
    NegotiationStatus.PENDING.value,
    NegotiationStatus.FINAL_PENDING.value,
    NegotiationStatus.FINAL_RETURNED.value,
    NegotiationStatus.RECEIVER_PENDING.value,
)
ADMIN_ONLY_REASON = "只有管理员可以转交谈款"
REJECTIONS_LIMIT = 5
"""博主卡片「谈款驳回记录」最多几条（7c-7、8.7）。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _sees_all(actor: FlowActor) -> bool:
    """持 ``negotiation_all:read``（管理员靠 ``*``）：看得到别人草稿之外的谈款。"""
    return actor.perms.has(SCOPE_NEGOTIATION_ALL, "read")


def can_see_negotiation(actor: FlowActor, *, pr_id: UUID, status: str) -> bool:
    """可见范围（3.2）：本人；或 ``negotiation_all:read`` 看草稿之外的。与仓储 ``_VISIBLE`` 同一口径。"""
    return pr_id == actor.user_id or (status != NegotiationStatus.DRAFT.value and _sees_all(actor))


def _sees_overdue_badge(actor: FlowActor) -> bool:
    """能不能看超时高亮 / 超时数（矩阵 overdue_badge：读/读/读/隐/隐）。拿本人的主管待审单探一格。"""
    probe = build_negotiation_doc(
        {
            "status": NegotiationStatus.PENDING.value,
            "pr_id": actor.user_id,
            "reviewed_by": None,
            "blogger_id": None,
            "style_id": None,
            "cooperation_mode": None,
            "platform": None,
            "scheduled_publish_date": None,
            "quote_amount": None,
            "receiver_name": None,
            "receiver_phone": None,
            "receiver_address": None,
        },
        items_complete=False,
    )
    return cell_level(actor, probe, "field", "overdue_badge") >= Level.READ


def _items_complete(
    views: builtins.list[NegotiationItemView], members: builtins.list[UUID]
) -> bool:
    """颜色尺码齐（N3）：款式集合 = 商品的启用成员，且每行 sku 有、未删、属于该行款式。"""
    return goods_items_complete((v.style_id for v in views), members) and all(
        v.sku_id is not None and v.sku_style_id == v.style_id and not v.sku_is_deleted
        for v in views
    )


def _style_image_url(key: str | None, *, style_id: UUID) -> str | None:
    """款式主图签名 URL；没有图或签名失败（只记日志）回 null。"""
    from app.core.attachment import attachment_service

    if not key:
        return None
    try:
        return attachment_service.get_signed_url("private", key, expires_in=3600)
    except Exception:
        log.warning("negotiation_item_style_image_url_failed", extra={"style_id": str(style_id)})
        return None


def _item_responses(
    views: builtins.list[NegotiationItemView],
) -> builtins.list[NegotiationItemResponse]:
    """明细行 → 响应（品名规则同推广单 ``_item_responses``；主图签名失败只记日志、回 null）。"""
    responses: builtins.list[NegotiationItemResponse] = []
    for view in views:
        image_url = _style_image_url(view.style_main_image_key, style_id=view.style_id)
        responses.append(
            NegotiationItemResponse(
                id=view.id,
                style_id=view.style_id,
                style_code=view.style_code,
                display_short_name=normalize_goods_short_name(view.style_short_name)
                or view.style_name,
                goods_title=view.style_name,
                sku_id=view.sku_id,
                color=view.color,
                size=view.size,
                style_main_image_url=image_url,
            )
        )
    return responses


def _submit_snapshot(
    negotiation: Negotiation, views: builtins.list[NegotiationItemView]
) -> dict[str, Any]:
    """``negotiation.submitted`` 的 ``snapshot``（6.3）：博主、商品、颜色尺码、模式、平台、约定发布时间、服务费。

    只放 id 与值（显示名读时再取）；金额存字符串、固定两位，免得 JSON 浮点误差与 ``0`` / ``0.00`` 假差异。
    """
    quote = negotiation.quote_amount
    return {
        "blogger_id": str(negotiation.blogger_id),
        "style_id": str(negotiation.style_id),
        "goods_main_id": str(negotiation.goods_main_id) if negotiation.goods_main_id else None,
        "goods_items": [
            [str(v.style_id), str(v.sku_id) if v.sku_id is not None else None] for v in views
        ],
        "cooperation_mode": negotiation.cooperation_mode,
        "platform": negotiation.platform,
        "scheduled_publish_date": (
            negotiation.scheduled_publish_date.isoformat()
            if negotiation.scheduled_publish_date
            else None
        ),
        "quote_amount": str(quote.quantize(Decimal("0.01"))) if quote is not None else None,
    }


def _snapshot_diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """只列改了的键，形状同审计 diff：``{键: {"before": 旧, "after": 新}}``。"""
    return {
        key: {"before": before.get(key), "after": value}
        for key, value in after.items()
        if before.get(key) != value
    }


def _suggestion_label(row: Mapping[str, Any]) -> str:
    day = row["at"].astimezone(DEFAULT_TENANT_TZ).strftime("%m-%d")
    if row["source"] == "promotion":
        return f"推广单 {row['internal_code']} · {day}"
    return f"谈款定稿 · {day}"


def _clean_receiver(field: str, value: str | None, *, strict: bool) -> str | None:
    """收件一项入库前的值：去空白后空串 = None；电话走 ``normalize_receiver_phone``（与推广单同一个函数）。
    ``strict=False``（确认收货）时电话格式不对原样留着，交给 ★⁴ 与另两项一起报缺项。
    """
    if value is None or not value.strip():
        return None
    if field != "receiver_phone":
        return value.strip()
    try:
        return normalize_receiver_phone(value)
    except InvalidReceiverPhoneError:
        if strict:
            raise
        return value.strip()


def _finalize_items(
    views: builtins.list[NegotiationItemView], members: builtins.list[UUID]
) -> builtins.list[GoodsItemIn] | None:
    """定稿时复制给推广单的明细（偏差 5）：款式集合 = 当前成员且每行 sku 有效才复制；否则 None，推送仓库时补选。"""
    if not _items_complete(views, members):
        return None
    return [GoodsItemIn(style_id=v.style_id, sku_id=v.sku_id) for v in views]


class NegotiationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = NegotiationRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._items_repo = PromotionItemRepository(session)
        self._promotion_repo = PromotionRepository(session)
        self._events = LineEventRepository(session)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #

    async def _validate_refs(
        self, *, blogger_id: UUID, style_id: UUID, goods_main_id: UUID | None
    ) -> None:
        blogger = await self._blogger_repo.get_by_id(blogger_id)
        if blogger is None:
            raise InvalidNegotiationBloggerError(
                "博主不存在或已删除", details={"blogger_id": str(blogger_id)}
            )
        if not await self._repo.style_exists(style_id):
            raise InvalidNegotiationStyleError(
                "款式不存在或已删除", details={"style_id": str(style_id)}
            )
        if goods_main_id is not None and not await self._repo.goods_contains_style(
            goods_main_id=goods_main_id, style_id=style_id
        ):
            raise InvalidNegotiationGoodsError(
                "该商品不包含这个款式",
                details={"goods_main_id": str(goods_main_id), "style_id": str(style_id)},
            )

    async def _resolve_quote(
        self, *, mode: CooperationMode, quote_amount: Decimal | None, blogger_id: UUID
    ) -> Decimal:
        """博主服务费：置换强制 0，其余不传则取 blogger.quote。

        PRD 原文「选择置换，表单控件 disabled，值固定 0，接口也要做校验防止绕过前端传值」，
        所以这里是改写而不是报错。
        """
        if mode is CooperationMode.BARTER:
            return Decimal("0")
        if quote_amount is not None:
            return quote_amount
        blogger = await self._blogger_repo.get_by_id(blogger_id)
        if blogger is not None and blogger.quote is not None:
            return blogger.quote
        return Decimal("0")

    # ------------------------------------------------------------------ #
    # 出参
    # ------------------------------------------------------------------ #

    async def _flow_actor(self, user: User) -> FlowActor:
        return await load_flow_actor(user.id, self._roles, self._perms)

    @staticmethod
    def _to_response(
        row: Mapping[str, Any],
        *,
        actor: FlowActor,
        views: builtins.list[NegotiationItemView],
        members: builtins.list[UUID],
        now: datetime,
        view: View,
    ) -> NegotiationResponse:
        """查询行 → 响应：矩阵 ``ui``、字段规则裁剪（服务费、收件三项逐项）、明细、超 36 小时。

        快照用裁剪前的原值（卡点看的是单据本身），响应才裁剪。
        """
        doc = build_negotiation_doc(row, items_complete=_items_complete(views, members))
        state = ui_for(actor, doc)
        ctx = actor.field_ctx
        data = dict(row)
        if not can_read_field("promotion", "quote_amount", ctx):
            data["quote_amount"] = None
        for field in RECEIVER_FIELDS:
            if not can_read_field("promotion", field, ctx):
                data[field] = None
        submitted_at = row.get("submitted_at")
        if (
            row["status"] == NegotiationStatus.PENDING.value
            and submitted_at is not None
            and "overdue_badge" in state.fields
        ):
            waited = now - submitted_at
            data["overdue"] = waited > OVERDUE_AFTER
            data["overdue_hours"] = int(waited.total_seconds() // 3600)
        data["items"] = _item_responses(views)
        data["ui"] = state.to_dict(view=view)
        return NegotiationResponse.model_validate(data)

    @staticmethod
    def _page_ui(actor: FlowActor) -> dict[str, Any]:
        """页级 ``ui.actions.create``（偏差 8）：持 ``negotiation:write`` 才出现；持 ``*`` 灰掉（N1、11-1）。"""
        actions: dict[str, Any] = {}
        if actor.perms.has(SCOPE_NEGOTIATION, "write"):
            actions["create"] = (
                {"state": "disabled", "reason": STAR_CREATE_REASON}
                if actor.is_star
                else {"state": "enabled"}
            )
        return {"actions": actions}

    async def _receiver_suggestion(
        self, *, tenant_id: UUID, blogger_id: UUID
    ) -> ReceiverSuggestion | None:
        row = await self._repo.receiver_suggestion(tenant_id=tenant_id, blogger_id=blogger_id)
        if row is None:
            return None
        return ReceiverSuggestion(
            receiver_name=row["receiver_name"],
            receiver_phone=row["receiver_phone"],
            receiver_address=row["receiver_address"],
            source=row["source"],
            source_label=_suggestion_label(row),
        )

    # ------------------------------------------------------------------ #
    # 商品与明细（N1、N2）
    # ------------------------------------------------------------------ #

    async def _resolve_goods(
        self, *, style_id: UUID, goods_main_id: UUID | None, explicit: bool
    ) -> UUID | None:
        """``goods_main_id``：显式传了（含传 null）用传的；没传按现有规则推定主商品（非套装优先），之后以写回的值为准。"""
        if explicit:
            return goods_main_id
        return await self._promotion_repo.resolve_owner_goods_id(style_id)

    async def _item_rows(
        self,
        *,
        goods_main_id: UUID | None,
        style_id: UUID,
        items: builtins.list[GoodsItemIn] | None,
    ) -> builtins.list[tuple[UUID, UUID | None]]:
        """明细应有的行（7.2）：不传 = 按商品成员建空行；传了款式集合要与成员完全相同，``sku_id`` 可空。

        顺序：同一款式两次 → 款式集合 ≠ 成员（多出 / 缺少都列出）→ 422 ``VALIDATION_ERROR``；
        给了的 sku 不存在、已删或不属于该行款式 → 422 ``INVALID_SKU_REFERENCE``（与推广单入口同一口径）。
        """
        members = await self._items_repo.members_of(goods_main_id=goods_main_id, style_id=style_id)
        if items is None:
            return [(member, None) for member in members]
        given = [item.style_id for item in items]
        duplicates = [s for s in dict.fromkeys(given) if given.count(s) > 1]
        if duplicates:
            raise ValidationError(
                "同一款式只能有一行颜色尺码",
                details={"duplicate_style_ids": [str(s) for s in duplicates]},
            )
        missing = [s for s in members if s not in given]
        extra = [s for s in given if s not in members]
        if missing or extra:
            raise ValidationError(
                "颜色尺码明细要与商品的成员款式一一对应",
                details={
                    "missing_style_ids": [str(s) for s in missing],
                    "extra_style_ids": [str(s) for s in extra],
                },
            )
        skus = await self._items_repo.skus_by_ids(
            [item.sku_id for item in items if item.sku_id is not None]
        )
        for item in items:
            if item.sku_id is None:
                continue
            sku = skus.get(item.sku_id)
            if sku is None or sku.is_deleted or sku.style_id != item.style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {item.sku_id} 不存在、已删除或不属于款式 {item.style_id}",
                    details={"style_id": str(item.style_id), "sku_id": str(item.sku_id)},
                )
        return [(item.style_id, item.sku_id) for item in items]

    async def goods_members(
        self, *, style_id: UUID, goods_main_id: UUID | None
    ) -> NegotiationGoodsMembers:
        """``GET /goods-members``（偏差 3）：新建 / 换商品时明细该有哪几行，口径 = N1 推定 + ``members_of``。

        PR 没有 ``product.goods:read``，拿不到套装成员，所以由谈款自己的接口给（同 PR-2 偏差 11）。
        """
        if not await self._repo.style_exists(style_id):
            raise InvalidNegotiationStyleError(
                "款式不存在或已删除", details={"style_id": str(style_id)}
            )
        goods_id = await self._resolve_goods(
            style_id=style_id, goods_main_id=goods_main_id, explicit=goods_main_id is not None
        )
        if goods_id is not None and not await self._repo.goods_contains_style(
            goods_main_id=goods_id, style_id=style_id
        ):
            raise InvalidNegotiationGoodsError(
                "该商品不包含这个款式",
                details={"goods_main_id": str(goods_id), "style_id": str(style_id)},
            )
        members = await self._items_repo.members_of(goods_main_id=goods_id, style_id=style_id)
        names = await self._items_repo.style_names(members)
        return NegotiationGoodsMembers(
            goods_main_id=goods_id,
            members=[
                GoodsMemberResponse(
                    style_id=member,
                    display_short_name=normalize_goods_short_name(names[member][1])
                    or names[member][0],
                    goods_title=names[member][0],
                    style_main_image_url=_style_image_url(names[member][2], style_id=member),
                )
                for member in members
            ],
        )

    async def _others_in_progress(
        self, *, tenant_id: UUID, blogger_id: UUID, viewer_id: UUID
    ) -> builtins.list[OthersInProgressItem]:
        rows = await self._repo.others_in_progress(
            tenant_id=tenant_id, blogger_id=blogger_id, viewer_id=viewer_id
        )
        return [OthersInProgressItem(**r) for r in rows]

    async def _load_for_action(
        self, negotiation_id: UUID, user: User
    ) -> tuple[FlowActor, Negotiation, builtins.list[NegotiationItemView], builtins.list[UUID]]:
        """写动作的第一步：取单，看不到（别人的草稿、没有看全部时别人的单）→ 404；连同明细与成员（组快照用）。"""
        actor = await self._flow_actor(user)
        negotiation = await self._repo.get_by_id(negotiation_id)
        if (
            negotiation is None
            or negotiation.tenant_id != user.tenant_id
            or not can_see_negotiation(actor, pr_id=negotiation.pr_id, status=negotiation.status)
        ):
            raise NegotiationNotFoundError("谈款单不存在")
        views = (await self._repo.list_items([negotiation.id])).get(negotiation.id, [])
        members = await self._items_repo.members_of(
            goods_main_id=negotiation.goods_main_id, style_id=negotiation.style_id
        )
        return actor, negotiation, views, members

    # ------------------------------------------------------------------ #
    # N1 新建 / N2 编辑
    # ------------------------------------------------------------------ #

    async def create(self, payload: NegotiationCreate, user: User) -> NegotiationResponse:
        """N1：落草稿。持 ``*`` → 403 ``star_create``；发 ``line_id``；不传商品就推定并写回；按商品建明细。"""
        actor = await self._flow_actor(user)
        if actor.is_star:
            raise FlowActionForbiddenError(rule="star_create", reason=STAR_CREATE_REASON)
        if not await self._repo.style_exists(payload.style_id):
            raise InvalidNegotiationStyleError(
                "款式不存在或已删除", details={"style_id": str(payload.style_id)}
            )
        goods_main_id = await self._resolve_goods(
            style_id=payload.style_id,
            goods_main_id=payload.goods_main_id,
            explicit="goods_main_id" in payload.model_fields_set,
        )
        await self._validate_refs(
            blogger_id=payload.blogger_id, style_id=payload.style_id, goods_main_id=goods_main_id
        )
        rows = await self._item_rows(
            goods_main_id=goods_main_id, style_id=payload.style_id, items=payload.items
        )
        quote = await self._resolve_quote(
            mode=payload.cooperation_mode,
            quote_amount=payload.quote_amount,
            blogger_id=payload.blogger_id,
        )
        # line_id 显式发号：同一事务马上要写事件，不等库默认值回读（6.2）
        negotiation = Negotiation(
            tenant_id=user.tenant_id,
            line_id=uuid4(),
            blogger_id=payload.blogger_id,
            style_id=payload.style_id,
            goods_main_id=goods_main_id,
            pr_id=user.id,
            cooperation_mode=payload.cooperation_mode.value,
            platform=payload.platform,
            scheduled_publish_date=payload.scheduled_publish_date,
            quote_amount=quote,
            remark=payload.remark,
            status=NegotiationStatus.DRAFT.value,
        )
        self._repo.add(negotiation)
        await self._session.flush()
        await self._repo.replace_items(
            tenant_id=user.tenant_id, negotiation_id=negotiation.id, rows=rows
        )
        await self._events.append(
            tenant_id=user.tenant_id,
            line_id=negotiation.line_id,
            event_type=LineEventType.NEGOTIATION_CREATED,
            actor_id=user.id,
            negotiation_id=negotiation.id,
            to_state=NegotiationStatus.DRAFT.value,
        )
        await self._audit.log(
            action="negotiation.create",
            resource="negotiation",
            resource_id=negotiation.id,
            after={
                "status": negotiation.status,
                "cooperation_mode": negotiation.cooperation_mode,
                "blogger_id": str(negotiation.blogger_id),
                "style_id": str(negotiation.style_id),
            },
            user_id=user.id,
        )
        negotiation_id, blogger_id = negotiation.id, negotiation.blogger_id
        await self._session.commit()
        response = await self.get(negotiation_id, user)
        response.others_in_progress = await self._others_in_progress(
            tenant_id=user.tenant_id, blogger_id=blogger_id, viewer_id=user.id
        )
        return response

    async def update(
        self, negotiation_id: UUID, payload: NegotiationUpdate, user: User
    ) -> NegotiationResponse:
        """N2：只在草稿 / 老板退回、本人；状态不变（老板退回改完仍是老板退回，重提走 N3）。

        换款式 / 商品时同 N1 推定并写回，明细整组重建（新行 sku 为空）；传了 ``items`` 就按传的整组替换。
        条件 UPDATE 带 ``status IN (草稿, 老板退回) AND pr_id = 我``，0 行 → 409。
        """
        actor, negotiation, _views, _members = await self._load_for_action(negotiation_id, user)
        require(
            actor, build_negotiation_doc(negotiation, items_complete=False), "edit", gates=False
        )

        fields = payload.model_fields_set
        blogger_id = (
            payload.blogger_id if payload.blogger_id is not None else negotiation.blogger_id
        )
        style_id = payload.style_id if payload.style_id is not None else negotiation.style_id
        if not await self._repo.style_exists(style_id):
            raise InvalidNegotiationStyleError(
                "款式不存在或已删除", details={"style_id": str(style_id)}
            )
        if "goods_main_id" in fields or style_id != negotiation.style_id:
            goods_main_id = await self._resolve_goods(
                style_id=style_id,
                goods_main_id=payload.goods_main_id,
                explicit="goods_main_id" in fields,
            )
        else:
            goods_main_id = negotiation.goods_main_id
        await self._validate_refs(
            blogger_id=blogger_id, style_id=style_id, goods_main_id=goods_main_id
        )
        goods_changed = (style_id, goods_main_id) != (
            negotiation.style_id,
            negotiation.goods_main_id,
        )
        rows: builtins.list[tuple[UUID, UUID | None]] | None = None
        if payload.items is not None or goods_changed:
            rows = await self._item_rows(
                goods_main_id=goods_main_id, style_id=style_id, items=payload.items
            )

        mode = (
            payload.cooperation_mode
            if payload.cooperation_mode is not None
            else CooperationMode(negotiation.cooperation_mode)
        )
        # 没传服务费时也重算一次：模式可能刚被改成置换，服务费要跟着归零
        quote = await self._resolve_quote(
            mode=mode,
            quote_amount=(
                payload.quote_amount if "quote_amount" in fields else negotiation.quote_amount
            ),
            blogger_id=blogger_id,
        )
        values: dict[str, Any] = {
            "blogger_id": blogger_id,
            "style_id": style_id,
            "goods_main_id": goods_main_id,
            "cooperation_mode": mode.value,
            "quote_amount": quote,
        }
        if "platform" in fields and payload.platform is not None:
            values["platform"] = payload.platform
        if "scheduled_publish_date" in fields:
            values["scheduled_publish_date"] = payload.scheduled_publish_date
        if "remark" in fields:
            values["remark"] = payload.remark

        before = {"cooperation_mode": negotiation.cooperation_mode, "status": negotiation.status}
        tenant_id = negotiation.tenant_id
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=_OWNER_EDITABLE,
            values=values,
            pr_id=user.id,
        )
        if updated is None:
            raise NegotiationStateConflictError(details={"negotiation_id": str(negotiation_id)})
        # 条件 UPDATE 先过、再动明细：输了并发的请求在这里就停下（同推送 A8）
        if rows is not None:
            await self._repo.replace_items(
                tenant_id=tenant_id, negotiation_id=negotiation_id, rows=rows
            )
        await self._audit.log(
            action="negotiation.update",
            resource="negotiation",
            resource_id=negotiation_id,
            before=before,
            after={"cooperation_mode": updated.cooperation_mode, "status": updated.status},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation_id, user)

    # ------------------------------------------------------------------ #
    # N3 提交
    # ------------------------------------------------------------------ #

    async def _last_submit_snapshot(
        self, *, tenant_id: UUID, line_id: UUID, negotiation_id: UUID
    ) -> dict[str, Any] | None:
        """这张谈款上一次 ``negotiation.submitted`` 的快照（没有提交过为 None）。"""
        events = await self._events.list_by_line(tenant_id=tenant_id, line_id=line_id)
        for event in reversed(events):
            if (
                event.event_type == LineEventType.NEGOTIATION_SUBMITTED.value
                and event.negotiation_id == negotiation_id
            ):
                snapshot = (event.payload or {}).get("snapshot")
                return dict(snapshot) if isinstance(snapshot, Mapping) else None
        return None

    async def submit(self, negotiation_id: UUID, user: User) -> NegotiationResponse:
        """N3：草稿 / 老板退回 → 主管待审，本人；★¹ 缺项一次全报（``FLOW_GATE_MISSING``）。

        写 ``submitted_at``、清空本轮两级审核（旧值已在事件里）；事件带本次快照与上次提交的 ``diff``。
        """
        actor, negotiation, views, members = await self._load_for_action(negotiation_id, user)
        require(
            actor,
            build_negotiation_doc(negotiation, items_complete=_items_complete(views, members)),
            "submit",
        )
        from_status = negotiation.status
        tenant_id, line_id = negotiation.tenant_id, negotiation.line_id
        blogger_id = negotiation.blogger_id
        snapshot = _submit_snapshot(negotiation, views)
        previous = await self._last_submit_snapshot(
            tenant_id=tenant_id, line_id=line_id, negotiation_id=negotiation_id
        )
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=(from_status,),
            to_status=NegotiationStatus.PENDING.value,
            values={
                "submitted_at": _utcnow(),
                "reviewed_by": None,
                "reviewed_at": None,
                "review_opinion": None,
                "final_reviewed_by": None,
                "final_reviewed_at": None,
                "final_review_opinion": None,
            },
            pr_id=user.id,
        )
        if updated is None:
            raise NegotiationStateConflictError(details={"negotiation_id": str(negotiation_id)})
        payload: dict[str, Any] = {"snapshot": snapshot}
        if previous is not None:
            payload["diff"] = _snapshot_diff(previous, snapshot)
        await self._events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=LineEventType.NEGOTIATION_SUBMITTED,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            from_state=from_status,
            to_state=NegotiationStatus.PENDING.value,
            payload=payload,
        )
        await self._audit.log(
            action="negotiation.submit",
            resource="negotiation",
            resource_id=negotiation_id,
            before={"status": from_status},
            after={"status": NegotiationStatus.PENDING.value},
            user_id=user.id,
        )
        await self._session.commit()
        response = await self.get(negotiation_id, user)
        response.others_in_progress = await self._others_in_progress(
            tenant_id=tenant_id, blogger_id=blogger_id, viewer_id=user.id
        )
        return response

    # ------------------------------------------------------------------ #
    # N4 ~ N7 两级审核
    # ------------------------------------------------------------------ #

    @staticmethod
    def _require_review(
        actor: FlowActor, doc: NegotiationDoc, action: str, negotiation_id: UUID
    ) -> None:
        """``require()``；自审（``self_review``）改抛 ``NEGOTIATION_SELF_REVIEW_FORBIDDEN``（7.1 保留原 code，两级都是）。"""
        try:
            require(actor, doc, action)
        except FlowActionForbiddenError as exc:
            if exc.rule == "self_review":
                raise NegotiationSelfReviewForbiddenError(
                    exc.reason,
                    details={
                        "negotiation_id": str(negotiation_id),
                        "rule": exc.rule,
                        "reason": exc.reason,
                    },
                ) from exc
            raise

    async def _decide(
        self,
        negotiation: Negotiation,
        *,
        user: User,
        from_status: str,
        to_status: str,
        values: dict[str, Any],
        event_type: LineEventType,
        opinion: str | None,
        audit_action: str,
    ) -> NegotiationResponse:
        """两级审核落库：条件 UPDATE（0 行 → 409）→ 同事务写事件（意见进 ``note``）与审计 → 提交。"""
        negotiation_id = negotiation.id
        tenant_id, line_id = negotiation.tenant_id, negotiation.line_id
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=(from_status,),
            to_status=to_status,
            values=values,
        )
        if updated is None:
            raise NegotiationStateConflictError(details={"negotiation_id": str(negotiation_id)})
        await self._events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=event_type,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            from_state=from_status,
            to_state=to_status,
            note=opinion,
        )
        await self._audit.log(
            action=audit_action,
            resource="negotiation",
            resource_id=negotiation_id,
            before={"status": from_status},
            after={"status": to_status},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation_id, user)

    async def review(
        self, negotiation_id: UUID, payload: NegotiationReviewRequest, user: User
    ) -> NegotiationResponse:
        """N4 / N5 主管通过 / 驳回：主管待审 → 待老板审核 / 审核驳回（终态，意见留在行上，博主卡片读它）。

        通过**不建推广单**（老板定稿后确认收货信息时才建，N9）。持 ``*`` → 403 ``star_first_level``
        （管理员不能代做第一级）；审自己谈的 → 403 ``NEGOTIATION_SELF_REVIEW_FORBIDDEN``；驳回意见必填在 schema 层。
        """
        actor, negotiation, views, members = await self._load_for_action(negotiation_id, user)
        self._require_review(
            actor,
            build_negotiation_doc(negotiation, items_complete=_items_complete(views, members)),
            "manager_review",
            negotiation_id,
        )
        approve = payload.action is NegotiationReviewAction.APPROVE
        opinion = payload.review_opinion or None
        to_status = NegotiationStatus.FINAL_PENDING if approve else NegotiationStatus.REJECTED
        return await self._decide(
            negotiation,
            user=user,
            from_status=NegotiationStatus.PENDING.value,
            to_status=to_status.value,
            values={"reviewed_by": user.id, "reviewed_at": _utcnow(), "review_opinion": opinion},
            event_type=(
                LineEventType.NEGOTIATION_MANAGER_APPROVED
                if approve
                else LineEventType.NEGOTIATION_MANAGER_REJECTED
            ),
            opinion=opinion,
            audit_action="negotiation.review.approve" if approve else "negotiation.review.reject",
        )

    async def final_review(
        self, negotiation_id: UUID, payload: NegotiationFinalReviewRequest, user: User
    ) -> NegotiationResponse:
        """N6 / N7 老板定稿 / 退回：待老板审核 → 待补收货地址 / 老板退回（PR 改完重提，N2、N3）。

        审核人 ≠ 谈款人（自审 → ``NEGOTIATION_SELF_REVIEW_FORBIDDEN``）、≠ 本轮主管（403 ``same_reviewer``）；
        退回意见必填在 schema 层。定稿也不建推广单，确认收货信息时才建（N9）。
        """
        actor, negotiation, views, members = await self._load_for_action(negotiation_id, user)
        self._require_review(
            actor,
            build_negotiation_doc(negotiation, items_complete=_items_complete(views, members)),
            "final_review",
            negotiation_id,
        )
        approve = payload.action is NegotiationFinalReviewAction.APPROVE
        opinion = payload.opinion or None
        to_status = (
            NegotiationStatus.RECEIVER_PENDING if approve else NegotiationStatus.FINAL_RETURNED
        )
        return await self._decide(
            negotiation,
            user=user,
            from_status=NegotiationStatus.FINAL_PENDING.value,
            to_status=to_status.value,
            values={
                "final_reviewed_by": user.id,
                "final_reviewed_at": _utcnow(),
                "final_review_opinion": opinion,
            },
            event_type=(
                LineEventType.NEGOTIATION_FINAL_APPROVED
                if approve
                else LineEventType.NEGOTIATION_FINAL_RETURNED
            ),
            opinion=opinion,
            audit_action=(
                "negotiation.final_review.approve" if approve else "negotiation.final_review.return"
            ),
        )

    # ------------------------------------------------------------------ #
    # N8 保存收货信息 / N9 确认收货信息（= 定稿建推广单）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _require_receiver_write(actor: FlowActor, fields: tuple[str, ...]) -> None:
        """收件三项逐项过字段写权限（``promotion.receiver_*``，4.6），越权的一次列全（同推广单 ``_normalize_receiver``）。"""
        denied = [f for f in fields if not can_write_field("promotion", f, actor.field_ctx)]
        if denied:
            raise FieldPermissionDenied(fields=denied, entity="promotion")

    async def save_receiver(
        self, negotiation_id: UUID, payload: NegotiationReceiverUpdate, user: User
    ) -> NegotiationResponse:
        """N8：待补收货地址时保存收货三项，状态不变。本人，或持 ``negotiation.review:approve`` 的人
        （主管、临时代理人；管理员靠 ``*``）——PR 不在时有人能推进（Q4）；看得到但不是这些人 → 403 ``not_owner``。
        只改这次传了的项；电话传了就校验并规范化；没有改动不写库、不写事件。事件 ``receiver_saved`` 带 ``diff``。
        """
        actor, negotiation, views, members = await self._load_for_action(negotiation_id, user)
        require(
            actor,
            build_negotiation_doc(negotiation, items_complete=_items_complete(views, members)),
            "receiver_save",
            gates=False,
        )
        fields = tuple(f for f in RECEIVER_FIELDS if f in payload.model_fields_set)
        self._require_receiver_write(actor, fields)
        diff: dict[str, dict[str, str | None]] = {}
        for field in fields:
            before: str | None = getattr(negotiation, field)
            after = _clean_receiver(field, getattr(payload, field), strict=True)
            if before != after:
                diff[field] = {"before": before, "after": after}
        if not diff:
            return await self.get(negotiation_id, user)
        tenant_id, line_id, pr_id = negotiation.tenant_id, negotiation.line_id, negotiation.pr_id
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=(NegotiationStatus.RECEIVER_PENDING.value,),
            values={field: change["after"] for field, change in diff.items()},
            pr_id=pr_id,  # 读完之后被转交（N12）→ 0 行 409（评审 L1）
        )
        if updated is None:
            raise NegotiationStateConflictError(details={"negotiation_id": str(negotiation_id)})
        await self._events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=LineEventType.NEGOTIATION_RECEIVER_SAVED,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            payload={"diff": diff},
        )
        # 审计只记改了哪几项，收件人 / 电话 / 地址的值不进审计（值在事件 diff 里，按字段规则读）
        await self._audit.log(
            action="negotiation.receiver.save",
            resource="negotiation",
            resource_id=negotiation_id,
            after={"fields": list(diff)},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation_id, user)

    async def confirm_receiver(
        self, negotiation_id: UUID, payload: NegotiationReceiverConfirm, user: User
    ) -> NegotiationResponse:
        """N9：确认收货信息 = 定稿，同事务建推广单（3.2「定稿建推广单与防重复建单」四步）。
        谁能调同 N8；收货三项以 body 为准，★⁴（三项非空、电话格式）一次全报；已定稿 → 409「已定稿，推广单已生成」。
        推广单带谈款的 8 项、``line_id``、收件三项、明细（齐了才复制，偏差 5）、``ship_status = 待发货``；
        建单人仍是谈款 PR，``receiver_confirmed_by`` 与事件动作人记实际确认的人。
        两人同时确认：后到的在插推广单时卡在 ``uq_promotion_line`` 上，先到的提交后报唯一冲突 → 回滚、409；
        条件 UPDATE 0 行同样 409，推广单跟着回滚。
        """
        actor, negotiation, views, members = await self._load_for_action(negotiation_id, user)
        if negotiation.status == NegotiationStatus.APPROVED.value:
            raise NegotiationStateConflictError(
                FINALIZED_CONFLICT_MESSAGE,
                details={
                    "negotiation_id": str(negotiation_id),
                    "promotion_id": str(negotiation.promotion_id),
                },
            )
        doc = build_negotiation_doc(negotiation, items_complete=_items_complete(views, members))
        require(actor, doc, "receiver_confirm", gates=False)
        self._require_receiver_write(actor, RECEIVER_FIELDS)
        name = _clean_receiver("receiver_name", payload.receiver_name, strict=False)
        phone = _clean_receiver("receiver_phone", payload.receiver_phone, strict=False)
        address = _clean_receiver("receiver_address", payload.receiver_address, strict=False)
        # ★⁴ 按 body 重组快照再判（以 body 为准）；过了之后电话就是规范化后的值
        ensure_gates(
            actor,
            dataclasses.replace(
                doc, receiver_name=name, receiver_phone=phone, receiver_address=address
            ),
            "receiver_confirm",
        )
        pr_user = await self._session.get(User, negotiation.pr_id)
        if pr_user is None:  # pr_id 是 RESTRICT 外键，正常走不到；真发生了说明数据被手工动过
            raise ValidationError(
                "谈款的对接 PR 已不存在，无法生成推广单",
                details={"pr_id": str(negotiation.pr_id)},
            )
        items = _finalize_items(views, members)
        tenant_id, line_id, pr_id = negotiation.tenant_id, negotiation.line_id, negotiation.pr_id
        try:
            # 建单人 = 谈款 PR（这单是他谈的，后续催发、结款找他）；promotion.created 的动作人 = 确认的人
            promotion = await PromotionService(self._session).create_promotion(
                PromotionCreate(
                    style_id=negotiation.style_id,
                    goods_main_id=negotiation.goods_main_id,
                    blogger_id=negotiation.blogger_id,
                    cooperation_mode=CooperationMode(negotiation.cooperation_mode),
                    platform=negotiation.platform,
                    scheduled_publish_date=negotiation.scheduled_publish_date,
                    quote_amount=negotiation.quote_amount,
                    remark=negotiation.remark,
                    receiver_name=name,
                    receiver_phone=phone,
                    receiver_address=address,
                    items=items,
                ),
                pr_user,
                autocommit=False,
                ship_status=ShipStatus.PENDING,
                line_id=line_id,
                negotiation_id=negotiation_id,
                actor_id=user.id,
            )
        except IntegrityError as exc:
            # 事务已失效：先回滚再抛；要用的 id 都是调用前取出的
            await self._session.rollback()
            if _PROMOTION_LINE_UQ in str(exc):
                raise NegotiationStateConflictError(
                    FINALIZED_CONFLICT_MESSAGE, details={"negotiation_id": str(negotiation_id)}
                ) from exc
            raise
        promotion_id = promotion.id
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=(NegotiationStatus.RECEIVER_PENDING.value,),
            to_status=NegotiationStatus.APPROVED.value,
            pr_id=pr_id,  # 读完之后被转交（N12）→ 0 行 409，推广单跟着回滚（评审 L1）
            values={
                "receiver_name": name,
                "receiver_phone": phone,
                "receiver_address": address,
                "promotion_id": promotion_id,
                "receiver_confirmed_at": _utcnow(),
                "receiver_confirmed_by": user.id,
            },
        )
        if updated is None:
            await self._session.rollback()
            raise NegotiationStateConflictError(
                FINALIZED_CONFLICT_MESSAGE, details={"negotiation_id": str(negotiation_id)}
            )
        await self._events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=LineEventType.NEGOTIATION_FINALIZED,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            from_state=NegotiationStatus.RECEIVER_PENDING.value,
            to_state=NegotiationStatus.APPROVED.value,
            payload={"promotion_id": str(promotion_id)},
        )
        await self._audit.log(
            action="negotiation.receiver.confirm",
            resource="negotiation",
            resource_id=negotiation_id,
            before={"status": NegotiationStatus.RECEIVER_PENDING.value},
            after={"status": NegotiationStatus.APPROVED.value, "promotion_id": str(promotion_id)},
            user_id=user.id,
        )
        if items is None:
            log.warning(
                "negotiation_finalize_items_not_copied",
                extra={"negotiation_id": str(negotiation_id), "promotion_id": str(promotion_id)},
            )
        await self._session.commit()
        return await self.get(negotiation_id, user)

    # ------------------------------------------------------------------ #
    # N10 复制为新谈款 / N12 管理员转交
    # ------------------------------------------------------------------ #

    async def copy(self, negotiation_id: UUID, user: User) -> NegotiationResponse:
        """N10：审核驳回、本人 → 新草稿（201）。复制博主、商品、明细、模式、平台、服务费、备注、约定发布时间，
        不复制审核与收货；新 ``line_id``、``copied_from_id`` 指回原单。两条线各写一条事件互相引用（6.2、6.3）：
        新线 ``negotiation.copied``、原线 ``negotiation.copied_out``。原单不动（终态）。
        明细原样复制（sku 已删 / 商品成员变了的，提交时 ★¹ 照常拦）。
        """
        actor, source, views, _members = await self._load_for_action(negotiation_id, user)
        require(actor, build_negotiation_doc(source, items_complete=False), "copy", gates=False)
        tenant_id, source_line = source.tenant_id, source.line_id
        negotiation = Negotiation(
            tenant_id=tenant_id,
            line_id=uuid4(),
            copied_from_id=source.id,
            blogger_id=source.blogger_id,
            style_id=source.style_id,
            goods_main_id=source.goods_main_id,
            pr_id=user.id,
            cooperation_mode=source.cooperation_mode,
            platform=source.platform,
            scheduled_publish_date=source.scheduled_publish_date,
            quote_amount=source.quote_amount,
            remark=source.remark,
            status=NegotiationStatus.DRAFT.value,
        )
        self._repo.add(negotiation)
        await self._session.flush()
        new_id, new_line = negotiation.id, negotiation.line_id
        await self._repo.replace_items(
            tenant_id=tenant_id,
            negotiation_id=new_id,
            rows=[(v.style_id, v.sku_id) for v in views],
        )
        await self._events.append(
            tenant_id=tenant_id,
            line_id=new_line,
            event_type=LineEventType.NEGOTIATION_COPIED,
            actor_id=user.id,
            negotiation_id=new_id,
            to_state=NegotiationStatus.DRAFT.value,
            payload={"from_negotiation_id": str(negotiation_id), "from_line_id": str(source_line)},
        )
        await self._events.append(
            tenant_id=tenant_id,
            line_id=source_line,
            event_type=LineEventType.NEGOTIATION_COPIED_OUT,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            payload={"to_negotiation_id": str(new_id), "to_line_id": str(new_line)},
        )
        await self._audit.log(
            action="negotiation.copy",
            resource="negotiation",
            resource_id=new_id,
            after={"status": NegotiationStatus.DRAFT.value, "copied_from_id": str(negotiation_id)},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(new_id, user)

    async def _check_assignee(self, negotiation: Negotiation, pr_id: UUID) -> User:
        """N12 新 PR 的资格（7.2）：启用账号、同租户、持 ``negotiation:write``；≠ 原 PR、≠ 本轮两级审核人（否则等于自审）；
        不持 ``*``（管理员当谈款人就没人能定稿，同 N1、11-1）。不合规 → 422 ``INVALID_NEGOTIATION_ASSIGNEE``。
        """

        def invalid(message: str) -> InvalidNegotiationAssigneeError:
            return InvalidNegotiationAssigneeError(message, details={"pr_id": str(pr_id)})

        if pr_id == negotiation.pr_id:
            raise invalid("新 PR 就是现在的对接 PR")
        if pr_id in (negotiation.reviewed_by, negotiation.final_reviewed_by):
            raise invalid("不能转给本轮的审核人（否则等于自己审自己的谈款）")
        target = await self._session.get(User, pr_id)
        if (
            target is None
            or target.tenant_id != negotiation.tenant_id
            or target.deleted_at is not None
            or target.status != "active"
        ):
            raise invalid("新 PR 不存在或已停用")
        target_actor = await load_flow_actor(target.id, self._roles, self._perms)
        if target_actor.is_star:
            raise invalid(STAR_CREATE_REASON)
        if not target_actor.perms.has(SCOPE_NEGOTIATION, "write"):
            raise invalid("新 PR 没有谈款编辑权限")
        return target

    async def reassign(
        self, negotiation_id: UUID, payload: NegotiationReassignRequest, user: User
    ) -> NegotiationResponse:
        """N12（11-57）：PR 请假或离职时管理员把谈款转给另一位 PR，状态不变。不进矩阵、首版不出按钮。
        顺序：看不到（草稿）→ 404；不持 ``*`` → 403 ``admin_only``；状态不在 4 个里 → 422；新 PR 不合规 → 422；
        条件 UPDATE ``pr_id``（状态与原 PR 都没变才改，0 行 → 409）；事件 ``negotiation.reassigned`` 带前后 PR 与原因。
        之后「本人」与两级审核的「≠ 谈款人」都按新 PR 判（都读 ``pr_id``）。
        """
        actor, negotiation, _views, _members = await self._load_for_action(negotiation_id, user)
        if not actor.is_star:
            raise FlowActionForbiddenError(rule="admin_only", reason=ADMIN_ONLY_REASON)
        status = negotiation.status
        if status not in _REASSIGNABLE:
            raise IllegalStateTransitionError(
                f"当前状态「{status}」不允许此操作",
                details={"from_state": status, "action": "reassign"},
            )
        target = await self._check_assignee(negotiation, payload.pr_id)
        tenant_id, line_id, from_pr_id = (
            negotiation.tenant_id,
            negotiation.line_id,
            negotiation.pr_id,
        )
        from_pr = await self._session.get(User, from_pr_id)
        from_name = (from_pr.display_name or from_pr.username) if from_pr is not None else None
        to_id, to_name = target.id, target.display_name or target.username
        updated = await self._repo.update_state(
            negotiation_id=negotiation_id,
            tenant_id=tenant_id,
            from_statuses=(status,),
            values={"pr_id": to_id},
            pr_id=from_pr_id,
        )
        if updated is None:
            raise NegotiationStateConflictError(details={"negotiation_id": str(negotiation_id)})
        await self._events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            event_type=LineEventType.NEGOTIATION_REASSIGNED,
            actor_id=user.id,
            negotiation_id=negotiation_id,
            note=payload.reason,
            payload={
                "from_pr_id": str(from_pr_id),
                "from_pr_name": from_name,
                "to_pr_id": str(to_id),
                "to_pr_name": to_name,
            },
        )
        await self._audit.log(
            action="negotiation.reassign",
            resource="negotiation",
            resource_id=negotiation_id,
            before={"pr_id": str(from_pr_id)},
            after={"pr_id": str(to_id)},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation_id, user)

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    async def get(self, negotiation_id: UUID, user: User) -> NegotiationResponse:
        """详情。看不到（别人的草稿、没有 ``negotiation_all:read`` 时别人的单）→ 404。"""
        actor = await self._flow_actor(user)
        row = await self._repo.get_detail(
            negotiation_id, viewer_id=user.id, see_all=_sees_all(actor)
        )
        if row is None:
            raise NegotiationNotFoundError("谈款单不存在")
        views = (await self._repo.list_items([negotiation_id])).get(negotiation_id, [])
        members = await self._items_repo.members_of(
            goods_main_id=row["goods_main_id"], style_id=row["style_id"]
        )
        response = self._to_response(
            row, actor=actor, views=views, members=members, now=_utcnow(), view="detail"
        )
        # 待补收货地址、且能录收货信息（N8：本人 / 主管 / 代理人 / 管理员）时预填
        save = (response.ui or {}).get("actions", {}).get("receiver_save")
        if (
            row["status"] == NegotiationStatus.RECEIVER_PENDING.value
            and save is not None
            and save.get("state") == "enabled"
        ):
            response.receiver_suggestion = await self._receiver_suggestion(
                tenant_id=user.tenant_id, blogger_id=row["blogger_id"]
            )
        return response

    async def list_negotiations(
        self,
        *,
        user: User,
        filters: NegotiationListFilters,
        page: int = 1,
        page_size: int = 20,
    ) -> NegotiationListResponse:
        """列表（可见范围同详情）。``ui`` 只用本页数据算：当前用户、明细、套装成员各一次查（D7）。"""
        actor = await self._flow_actor(user)
        now = _utcnow()
        rows, total = await self._repo.list_detailed(
            tenant_id=user.tenant_id,
            viewer_id=user.id,
            see_all=_sees_all(actor),
            overdue_cutoff=now - OVERDUE_AFTER,
            status=filters.status.value if filters.status else None,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            pr_id=filters.pr_id,
            cooperation_mode=(filters.cooperation_mode.value if filters.cooperation_mode else None),
            keyword=filters.keyword,
            overdue_only=filters.overdue_only,
            page=page,
            page_size=page_size,
        )
        items = await self._repo.list_items([r["id"] for r in rows])
        members_by_goods = await self._items_repo.members_by_goods(
            list({r["goods_main_id"] for r in rows if r["goods_main_id"] is not None})
        )
        responses = [
            self._to_response(
                r,
                actor=actor,
                views=items.get(r["id"], []),
                members=(
                    members_by_goods.get(r["goods_main_id"])
                    if r["goods_main_id"] is not None
                    else None
                )
                or [r["style_id"]],
                now=now,
                view="list",
            )
            for r in rows
        ]
        return NegotiationListResponse(
            items=responses,
            total=total,
            page=page,
            page_size=page_size,
            ui=self._page_ui(actor),
        )

    async def status_counts(self, user: User) -> dict[str, int]:
        """7 个状态 + 「主管待审超时」，与列表同一可见范围；没有单据的也回 0。

        超时数只给看得到超时高亮的人（矩阵 overdue_badge），财务、运营恒为 0。
        """
        actor = await self._flow_actor(user)
        counts, overdue = await self._repo.count_by_status(
            tenant_id=user.tenant_id,
            viewer_id=user.id,
            see_all=_sees_all(actor),
            overdue_cutoff=_utcnow() - OVERDUE_AFTER,
        )
        out = {s.value: counts.get(s.value, 0) for s in NegotiationStatus}
        out[OVERDUE_COUNT_KEY] = overdue if _sees_overdue_badge(actor) else 0
        return out

    # ------------------------------------------------------------------ #
    # 博主历史合作（hover 卡）
    # ------------------------------------------------------------------ #

    async def blogger_history(
        self, blogger_id: UUID, user: User, *, limit: int = 5
    ) -> BloggerCooperationHistory:
        """某博主最近 N 次合作款式。

        PRD 改动 3 原文有「当时 ROI」。业务方 10-02 确认博主卡片**不算 ROI**，只看单篇
        点赞成本，而且要录完 7 天数据之后才算（口径见 ``calculate_cpl``）。
        """
        from app.core.attachment import attachment_service

        rows, total = await self._repo.blogger_cooperations(
            tenant_id=user.tenant_id, blogger_id=blogger_id, limit=limit
        )
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_quote = can_read_field("promotion", "quote_amount", ctx)
        # 单赞成本的分子含样品成本与寄回运费，乘回点赞数就能反推 —— 两个读权限都要有
        can_see_cpl = can_see_quote and can_read_field("promotion", "cost_snapshot", ctx)
        items: builtins.list[BloggerCooperationItem] = []
        for r in rows:
            image_url: str | None = None
            key = r.get("style_main_image_key")
            if key:
                try:
                    image_url = attachment_service.get_signed_url(
                        "private", str(key), expires_in=3600
                    )
                except Exception:
                    image_url = None
            effective_like = calculate_effective_like_count(
                platform=str(r["platform"]), like_count=r.get("like_count")
            )
            raw_cost = r.get("total_promo_cost")
            cpl = calculate_cpl(
                total_promo_cost=Decimal(str(raw_cost)) if raw_cost is not None else None,
                effective_like_count=effective_like,
                metrics_recorded_at=r.get("metrics_recorded_at"),
            )
            items.append(
                BloggerCooperationItem(
                    promotion_id=r["promotion_id"],
                    internal_code=str(r["internal_code"]),
                    style_id=r["style_id"],
                    style_code=str(r["style_code"]),
                    style_name=r.get("style_name"),
                    display_short_name=r.get("display_short_name"),
                    goods_title=r.get("goods_title"),
                    style_main_image_url=image_url,
                    cooperation_date=r["cooperation_date"],
                    cooperation_mode=r.get("cooperation_mode"),
                    publish_status=str(r["publish_status"]),
                    actual_publish_date=r.get("actual_publish_date"),
                    like_count=r.get("like_count"),
                    cpl=cpl if can_see_cpl else None,
                    quote_amount=r.get("quote_amount") if can_see_quote else None,
                )
            )
        # 谈款驳回记录（7c-7）：跨 PR、不按可见范围过滤；negotiation_id 只给看得到那张的人，服务费按字段规则
        actor = await self._flow_actor(user)
        rejection_rows, total_rejections = await self._repo.blogger_rejections(
            tenant_id=user.tenant_id, blogger_id=blogger_id, limit=REJECTIONS_LIMIT
        )
        rejections = [
            NegotiationRejectionItem(
                negotiation_id=(
                    r["negotiation_id"]
                    if can_see_negotiation(actor, pr_id=r["pr_id"], status=str(r["status"]))
                    else None
                ),
                pr_name=r.get("pr_name"),
                rejected_at=r["rejected_at"],
                style_code=r.get("style_code"),
                display_short_name=r.get("display_short_name"),
                platform=r.get("platform"),
                quote_amount=r.get("quote_amount") if can_see_quote else None,
                opinion=r.get("opinion"),
            )
            for r in rejection_rows
        ]
        return BloggerCooperationHistory(
            blogger_id=blogger_id,
            total_cooperations=total,
            items=items,
            rejections=rejections,
            total_rejections=total_rejections,
        )

    async def others_in_progress(
        self, blogger_id: UUID, user: User, *, exclude_negotiation_id: UUID | None = None
    ) -> builtins.list[OthersInProgressItem]:
        """``GET /blogger/{id}/others-in-progress``（7c-16）：同博主别的 PR 在谈的单，只给姓名、状态、更新时间；
        不受可见范围限制。``exclude_negotiation_id``：编辑时排除自己这张。"""
        rows = await self._repo.others_in_progress(
            tenant_id=user.tenant_id,
            blogger_id=blogger_id,
            viewer_id=user.id,
            exclude_negotiation_id=exclude_negotiation_id,
        )
        return [OthersInProgressItem(**r) for r in rows]


__all__ = [
    "ADMIN_ONLY_REASON",
    "OVERDUE_AFTER",
    "FINALIZED_CONFLICT_MESSAGE",
    "OVERDUE_COUNT_KEY",
    "REJECTIONS_LIMIT",
    "STAR_CREATE_REASON",
    "NegotiationService",
    "can_see_negotiation",
]
