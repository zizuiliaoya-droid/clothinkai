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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.security.field_permissions import build_field_perm_context, can_read_field
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.repository import BloggerRepository
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
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.repository import NegotiationRepository
from app.modules.negotiation.schemas import (
    BloggerCooperationHistory,
    BloggerCooperationItem,
    NegotiationCreate,
    NegotiationListFilters,
    NegotiationResponse,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
)
from app.modules.promotion.schemas import PromotionCreate
from app.modules.promotion.service import PromotionService

_EDITABLE_STATUSES = (NegotiationStatus.DRAFT.value, NegotiationStatus.REJECTED.value)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NegotiationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = NegotiationRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
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

    async def _can_see_quote(self, user: User) -> bool:
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        return can_read_field("promotion", "quote_amount", ctx)

    @staticmethod
    def _row_to_response(row: dict[str, Any], *, can_see_quote: bool) -> NegotiationResponse:
        data = dict(row)
        if not can_see_quote:
            data["quote_amount"] = None
        return NegotiationResponse.model_validate(data)

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
        row = await self._repo.get_detail(negotiation_id)
        if row is None:
            raise NegotiationNotFoundError("谈款单不存在")
        return self._row_to_response(dict(row), can_see_quote=await self._can_see_quote(user))

    async def list_negotiations(
        self,
        *,
        user: User,
        filters: NegotiationListFilters,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[NegotiationResponse], int]:
        rows, total = await self._repo.list_detailed(
            tenant_id=user.tenant_id,
            status=filters.status.value if filters.status else None,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            pr_id=filters.pr_id,
            cooperation_mode=(filters.cooperation_mode.value if filters.cooperation_mode else None),
            keyword=filters.keyword,
            page=page,
            page_size=page_size,
        )
        can_see = await self._can_see_quote(user)
        return [self._row_to_response(dict(r), can_see_quote=can_see) for r in rows], total

    async def status_counts(self, user: User) -> dict[str, int]:
        return await self._repo.count_by_status(tenant_id=user.tenant_id)

    # ------------------------------------------------------------------ #
    # 博主历史合作（hover 卡）
    # ------------------------------------------------------------------ #

    async def blogger_history(
        self, blogger_id: UUID, user: User, *, limit: int = 5
    ) -> BloggerCooperationHistory:
        """某博主最近 N 次合作款式。

        PRD 改动 3 还要「当时 ROI」，但博主维度 ROI 系统里没有定义过（现有 ROI 都是
        款式/商品维度，还要先定「发布后多少天」这个窗口）。这里先给已有口径的 CPL
        与点赞数，同样能看出推得怎么样。
        """
        from app.core.attachment import attachment_service

        rows, total = await self._repo.blogger_cooperations(
            tenant_id=user.tenant_id, blogger_id=blogger_id, limit=limit
        )
        can_see_quote = await self._can_see_quote(user)
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
            # promotion.quote_amount 是 NOT NULL，这里的 None 分支只为满足类型检查
            raw_quote = r.get("quote_amount")
            cpl = (
                calculate_cpl(
                    quote_amount=Decimal(str(raw_quote)), effective_like_count=effective_like
                )
                if raw_quote is not None
                else None
            )
            items.append(
                BloggerCooperationItem(
                    promotion_id=r["promotion_id"],
                    internal_code=str(r["internal_code"]),
                    style_id=r["style_id"],
                    style_code=str(r["style_code"]),
                    style_name=r.get("style_name"),
                    style_main_image_url=image_url,
                    cooperation_date=r["cooperation_date"],
                    cooperation_mode=r.get("cooperation_mode"),
                    publish_status=str(r["publish_status"]),
                    actual_publish_date=r.get("actual_publish_date"),
                    like_count=r.get("like_count"),
                    cpl=cpl if can_see_quote else None,
                    quote_amount=r.get("quote_amount") if can_see_quote else None,
                )
            )
        return BloggerCooperationHistory(
            blogger_id=blogger_id, total_cooperations=total, items=items
        )


__all__ = ["NegotiationService"]
