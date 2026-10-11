"""谈款审核服务层（PRD V1.4 模块一）。

状态流转：

    草稿 --submit--> 待审核 --approve--> 审核通过（生成推广单，终态）
                        |
                        \\--reject--> 审核驳回 --submit--> 待审核
                                          |
                                          \\--update--> 草稿

几条规则刻意放在 service 而不是只靠前端：

- 置换模式博主服务费强制 0（PRD：接口也要校验防止绕过前端传值）
- 只有草稿与被驳回的单据能改、能提交
- 禁止审核自己提交的单
- 审核通过与生成推广单必须同一个事务 —— 否则会出现「审核通过但没有推广单」
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.security.field_permissions import build_field_perm_context, can_read_field
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.repository import BloggerRepository
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.matrix import FlowActor, Level, View, cell_level, ui_for
from app.modules.negotiation.enums import NegotiationReviewAction, NegotiationStatus
from app.modules.negotiation.exceptions import (
    InvalidNegotiationBloggerError,
    InvalidNegotiationGoodsError,
    InvalidNegotiationStyleError,
    NegotiationNotEditableError,
    NegotiationNotFoundError,
    NegotiationNotReviewableError,
    NegotiationNotSubmittableError,
    NegotiationReviewOpinionRequiredError,
    NegotiationSelfReviewForbiddenError,
)
from app.modules.negotiation.flow_doc import build_negotiation_doc
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.permissions import SCOPE_NEGOTIATION, SCOPE_NEGOTIATION_ALL
from app.modules.negotiation.repository import NegotiationItemView, NegotiationRepository
from app.modules.negotiation.schemas import (
    BloggerCooperationHistory,
    BloggerCooperationItem,
    NegotiationCreate,
    NegotiationItemResponse,
    NegotiationListFilters,
    NegotiationListResponse,
    NegotiationResponse,
    NegotiationReviewRequest,
    NegotiationUpdate,
    ReceiverSuggestion,
)
from app.modules.promotion.display_name import normalize_goods_short_name
from app.modules.promotion.enums import CooperationMode, ShipStatus
from app.modules.promotion.flow_doc import goods_items_complete
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
)
from app.modules.promotion.receiver import RECEIVER_FIELDS
from app.modules.promotion.repository import PromotionItemRepository
from app.modules.promotion.schemas import PromotionCreate
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import DEFAULT_TENANT_TZ

log = logging.getLogger("app.modules.negotiation.service")

_EDITABLE_STATUSES = (NegotiationStatus.DRAFT.value, NegotiationStatus.REJECTED.value)

OVERDUE_AFTER = timedelta(hours=36)
"""主管待审超过这么久算超时（7c-6，按实际时长比）。"""
OVERDUE_COUNT_KEY = "主管待审超时"
"""``status-counts`` 里超时数的键（7.2）。"""
STAR_CREATE_REASON = "管理员建的谈款没人能定稿"
"""持 ``*`` 不能建谈款（N1、11-1）：页级新建按钮的禁用原因，也是 ``star_create`` 的文案。"""


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


def _item_responses(
    views: builtins.list[NegotiationItemView],
) -> builtins.list[NegotiationItemResponse]:
    """明细行 → 响应（品名规则同推广单 ``_item_responses``；主图签名失败只记日志、回 null）。"""
    from app.core.attachment import attachment_service

    responses: builtins.list[NegotiationItemResponse] = []
    for view in views:
        image_url: str | None = None
        if view.style_main_image_key:
            try:
                image_url = attachment_service.get_signed_url(
                    "private", view.style_main_image_key, expires_in=3600
                )
            except Exception:
                log.warning(
                    "negotiation_item_style_image_url_failed",
                    extra={"style_id": str(view.style_id)},
                )
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


def _suggestion_label(row: Mapping[str, Any]) -> str:
    day = row["at"].astimezone(DEFAULT_TENANT_TZ).strftime("%m-%d")
    if row["source"] == "promotion":
        return f"推广单 {row['internal_code']} · {day}"
    return f"谈款定稿 · {day}"


class NegotiationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = NegotiationRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._items_repo = PromotionItemRepository(session)
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
    # create / update
    # ------------------------------------------------------------------ #

    async def create(self, payload: NegotiationCreate, user: User) -> NegotiationResponse:
        await self._validate_refs(
            blogger_id=payload.blogger_id,
            style_id=payload.style_id,
            goods_main_id=payload.goods_main_id,
        )
        quote = await self._resolve_quote(
            mode=payload.cooperation_mode,
            quote_amount=payload.quote_amount,
            blogger_id=payload.blogger_id,
        )
        negotiation = Negotiation(
            tenant_id=user.tenant_id,
            blogger_id=payload.blogger_id,
            style_id=payload.style_id,
            goods_main_id=payload.goods_main_id,
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
        await self._session.commit()
        return await self.get(negotiation.id, user)

    async def update(
        self, negotiation_id: UUID, payload: NegotiationUpdate, user: User
    ) -> NegotiationResponse:
        negotiation = await self._repo.get_by_id(negotiation_id)
        if negotiation is None:
            raise NegotiationNotFoundError("谈款单不存在")
        if negotiation.status not in _EDITABLE_STATUSES:
            raise NegotiationNotEditableError(
                f"{negotiation.status}的谈款单不能修改",
                details={"status": negotiation.status},
            )

        before = {
            "cooperation_mode": negotiation.cooperation_mode,
            "status": negotiation.status,
        }
        fields = payload.model_fields_set
        if "blogger_id" in fields and payload.blogger_id is not None:
            negotiation.blogger_id = payload.blogger_id
        if "style_id" in fields and payload.style_id is not None:
            negotiation.style_id = payload.style_id
        if "goods_main_id" in fields:
            negotiation.goods_main_id = payload.goods_main_id
        await self._validate_refs(
            blogger_id=negotiation.blogger_id,
            style_id=negotiation.style_id,
            goods_main_id=negotiation.goods_main_id,
        )
        if "cooperation_mode" in fields and payload.cooperation_mode is not None:
            negotiation.cooperation_mode = payload.cooperation_mode.value
        if "platform" in fields and payload.platform is not None:
            negotiation.platform = payload.platform
        if "scheduled_publish_date" in fields:
            negotiation.scheduled_publish_date = payload.scheduled_publish_date
        if "remark" in fields:
            negotiation.remark = payload.remark
        if "quote_amount" in fields:
            negotiation.quote_amount = await self._resolve_quote(
                mode=CooperationMode(negotiation.cooperation_mode),
                quote_amount=payload.quote_amount,
                blogger_id=negotiation.blogger_id,
            )
        else:
            # 模式可能刚被改成置换，服务费要跟着归零
            negotiation.quote_amount = await self._resolve_quote(
                mode=CooperationMode(negotiation.cooperation_mode),
                quote_amount=negotiation.quote_amount,
                blogger_id=negotiation.blogger_id,
            )

        # 被驳回的单据改完回到草稿，重新走一遍提交
        if negotiation.status == NegotiationStatus.REJECTED.value:
            negotiation.status = NegotiationStatus.DRAFT.value

        await self._session.flush()
        await self._audit.log(
            action="negotiation.update",
            resource="negotiation",
            resource_id=negotiation.id,
            before=before,
            after={
                "cooperation_mode": negotiation.cooperation_mode,
                "status": negotiation.status,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation.id, user)

    # ------------------------------------------------------------------ #
    # 提交审核
    # ------------------------------------------------------------------ #

    async def submit(self, negotiation_id: UUID, user: User) -> NegotiationResponse:
        negotiation = await self._repo.get_by_id(negotiation_id)
        if negotiation is None:
            raise NegotiationNotFoundError("谈款单不存在")
        if negotiation.status not in _EDITABLE_STATUSES:
            raise NegotiationNotSubmittableError(
                f"{negotiation.status}的谈款单不能提交审核",
                details={"status": negotiation.status},
            )
        negotiation.status = NegotiationStatus.PENDING.value
        negotiation.submitted_at = _utcnow()
        # 重新提交时清掉上一轮的审核痕迹，避免界面上显示旧的驳回意见
        negotiation.reviewed_by = None
        negotiation.reviewed_at = None
        negotiation.review_opinion = None
        await self._session.flush()
        await self._audit.log(
            action="negotiation.submit",
            resource="negotiation",
            resource_id=negotiation.id,
            after={"status": negotiation.status},
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation.id, user)

    # ------------------------------------------------------------------ #
    # 审核
    # ------------------------------------------------------------------ #

    async def review(
        self, negotiation_id: UUID, payload: NegotiationReviewRequest, user: User
    ) -> NegotiationResponse:
        """主管审核。通过则在**同一事务**里生成推广单。"""
        negotiation = await self._repo.get_by_id(negotiation_id)
        if negotiation is None:
            raise NegotiationNotFoundError("谈款单不存在")
        if negotiation.status != NegotiationStatus.PENDING.value:
            raise NegotiationNotReviewableError(
                f"{negotiation.status}的谈款单不能审核",
                details={"status": negotiation.status},
            )
        if negotiation.pr_id == user.id:
            raise NegotiationSelfReviewForbiddenError(
                "不能审核自己提交的谈款单",
                details={"negotiation_id": str(negotiation_id)},
            )

        now = _utcnow()
        if payload.action is NegotiationReviewAction.REJECT:
            if not payload.review_opinion:
                raise NegotiationReviewOpinionRequiredError("驳回时审核意见必填")
            negotiation.status = NegotiationStatus.REJECTED.value
            negotiation.reviewed_by = user.id
            negotiation.reviewed_at = now
            negotiation.review_opinion = payload.review_opinion
            await self._session.flush()
            await self._audit.log(
                action="negotiation.review.reject",
                resource="negotiation",
                resource_id=negotiation.id,
                after={"status": negotiation.status},
                user_id=user.id,
            )
            await self._session.commit()
            return await self.get(negotiation.id, user)

        # ---- 通过：建推广单 ----
        #
        # 关键点：create_promotion 传 autocommit=False，让「改谈款状态 + 建推广单」
        # 落在一个事务里。否则中途失败会留下一张「审核通过但 promotion_id 为空」的单据，
        # 而表上的 CHECK 约束不允许这种状态。
        #
        # 建单人记在谈款单的 PR 身上（pr_id），不是审核人 —— 这单是 PR 谈下来的，
        # 后续催发、结款都该找他。
        pr_user = await self._session.get(User, negotiation.pr_id)
        if pr_user is None:
            # pr_id 是 RESTRICT 外键，正常不会走到这；真发生了说明数据被手工动过
            raise NegotiationNotReviewableError(
                "谈款单的对接 PR 已不存在，无法生成推广单",
                details={"pr_id": str(negotiation.pr_id)},
            )

        # 过渡期（流程线 PR-2，S1）：谈款还是旧 4 态，审核通过即定稿，建的单直接进待发货、等主管确认推送仓库
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
            ),
            pr_user,
            autocommit=False,
            ship_status=ShipStatus.PENDING,
            # 推广单继承谈款的主线号，promotion.created 记来源 = 谈款定稿（流程线 6.2）
            line_id=negotiation.line_id,
            negotiation_id=negotiation.id,
            actor_id=user.id,
        )

        negotiation.status = NegotiationStatus.APPROVED.value
        negotiation.reviewed_by = user.id
        negotiation.reviewed_at = now
        negotiation.review_opinion = payload.review_opinion
        negotiation.promotion_id = promotion.id
        await self._session.flush()
        await self._audit.log(
            action="negotiation.review.approve",
            resource="negotiation",
            resource_id=negotiation.id,
            after={
                "status": negotiation.status,
                "promotion_id": str(promotion.id),
                "promotion_internal_code": promotion.internal_code,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return await self.get(negotiation.id, user)

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
        return BloggerCooperationHistory(
            blogger_id=blogger_id, total_cooperations=total, items=items
        )


__all__ = [
    "OVERDUE_AFTER",
    "OVERDUE_COUNT_KEY",
    "STAR_CREATE_REASON",
    "NegotiationService",
    "can_see_negotiation",
]
