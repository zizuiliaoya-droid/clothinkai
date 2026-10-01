"""谈款审核集成测试（PRD V1.4 模块一）。

守的不变量：
- 置换模式博主服务费强制 0（PRD 要求接口也校验，不能只靠前端禁用）
- 只有草稿与被驳回的单据能改、能提交
- 审核通过与生成推广单在同一事务 —— 表上的 CHECK 约束不允许「通过但没推广单」
- 禁止审核自己提交的单
- 推广单的建单人是谈款单的对接 PR，不是审核人
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.negotiation.enums import NegotiationReviewAction
from app.modules.negotiation.exceptions import (
    NegotiationNotEditableError,
    NegotiationNotReviewableError,
    NegotiationNotSubmittableError,
    NegotiationSelfReviewForbiddenError,
)
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationListFilters,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _setup(
    tenant: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    *,
    code: str,
) -> tuple[Any, Any, Any, Any]:
    """建 PR、主管、款式、博主。审核要两个人 —— 自审会被拒。"""
    pr_user = await factory.user(tenant, roles=[admin_role])
    manager = await factory.user(tenant, roles=[admin_role])
    style = await product_factory.style(style_code=code)
    blogger = await blogger_factory.blogger(quote=Decimal("500.00"))
    return pr_user, manager, style, blogger


class TestCreateNegotiation:
    async def test_create_lands_as_draft(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, _, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_DRAFT"
            )
            resp = await NegotiationService(session).create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                    scheduled_publish_date=date(2026, 8, 1),
                ),
                pr,
            )
            assert resp.status == "草稿"
            assert resp.promotion_id is None
            assert resp.pr_id == pr.id
            assert resp.blogger_nickname == blogger.nickname
            assert resp.style_code == "NG_DRAFT"
            # 没传报价 → 取 blogger.quote
            assert resp.quote_amount == Decimal("500.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_barter_forces_quote_to_zero(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """置换：前端硬塞报价也要被压回 0（PRD 要求接口校验防绕过）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, _, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_BARTER"
            )
            resp = await NegotiationService(session).create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.BARTER,
                    quote_amount=Decimal("800.00"),
                ),
                pr,
            )
            assert resp.cooperation_mode == "置换"
            assert resp.quote_amount == Decimal("0.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_switching_to_barter_on_update_zeroes_quote(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """草稿阶段把模式改成置换，服务费要跟着归零。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, _, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_SWITCH"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            assert created.quote_amount == Decimal("500.00")

            updated = await svc.update(
                created.id,
                NegotiationUpdate(cooperation_mode=CooperationMode.BARTER),
                pr,
            )
            assert updated.cooperation_mode == "置换"
            assert updated.quote_amount == Decimal("0.00")
        finally:
            tenant_id_ctx.reset(token)


class TestSubmitAndReview:
    async def test_approve_creates_promotion_in_same_transaction(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """审核通过 → 生成推广单，两者同事务。

        推广单的建单人必须是谈款单的对接 PR（这单是他谈的，后续催发结款都找他），
        不是审核人。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, manager, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_APPROVE"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.CONSIGNMENT,
                    scheduled_publish_date=date(2026, 8, 10),
                ),
                pr,
            )
            submitted = await svc.submit(created.id, pr)
            assert submitted.status == "待审核"
            assert submitted.submitted_at is not None

            approved = await svc.review(
                created.id,
                NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                manager,
            )
            assert approved.status == "审核通过"
            assert approved.promotion_id is not None
            assert approved.promotion_internal_code is not None
            assert approved.reviewed_by == manager.id

            row = (
                await session.execute(
                    sa_text(
                        "SELECT pr_id, cooperation_mode, platform, scheduled_publish_date,"
                        " cooperation_date, cost_snapshot"
                        " FROM promotion WHERE id = :pid"
                    ),
                    {"pid": approved.promotion_id},
                )
            ).one()
            assert row[0] == pr.id, "建单人应是对接 PR，不是审核人"
            assert row[1] == "寄拍"
            assert row[3] == date(2026, 8, 10)
            # 合作日期 = 建单当天（审核通过那天）
            assert row[4] == get_today()
            # 寄拍样品成本恒为 0
            assert row[5] == Decimal("0.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_goes_back_and_update_returns_to_draft(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """驳回 → PR 改完回到草稿 → 可以重新提交。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, manager, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_REJECT"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            await svc.submit(created.id, pr)
            rejected = await svc.review(
                created.id,
                NegotiationReviewRequest(
                    action=NegotiationReviewAction.REJECT,
                    review_opinion="报价偏高，再谈一轮",
                ),
                manager,
            )
            assert rejected.status == "审核驳回"
            assert rejected.review_opinion == "报价偏高，再谈一轮"
            assert rejected.promotion_id is None

            # 被驳回的单据可以改，改完回到草稿
            updated = await svc.update(
                created.id,
                NegotiationUpdate(quote_amount=Decimal("300.00")),
                pr,
            )
            assert updated.status == "草稿"
            assert updated.quote_amount == Decimal("300.00")

            # 重新提交会清掉上一轮的驳回意见
            resubmitted = await svc.submit(created.id, pr)
            assert resubmitted.status == "待审核"
            assert resubmitted.review_opinion is None
            assert resubmitted.reviewed_by is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_requires_opinion(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        """驳回必须给审核意见 —— PR 得知道要改什么。schema 层就该挡住。"""
        with pytest.raises(PydanticValidationError):
            NegotiationReviewRequest(action=NegotiationReviewAction.REJECT)

    async def test_self_review_forbidden(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """自己谈的款自己批，审核这道关就没意义了。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, _, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_SELF"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            await svc.submit(created.id, pr)
            with pytest.raises(NegotiationSelfReviewForbiddenError):
                await svc.review(
                    created.id,
                    NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                    pr,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_approved_is_terminal(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """审核通过后不能再改、不能再审、不能再提交。

        推广单已经建出来了，改谈款信息同步不过去。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, manager, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_TERM"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            await svc.submit(created.id, pr)
            await svc.review(
                created.id,
                NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                manager,
            )

            with pytest.raises(NegotiationNotEditableError):
                await svc.update(created.id, NegotiationUpdate(remark="还想改"), pr)
            with pytest.raises(NegotiationNotSubmittableError):
                await svc.submit(created.id, pr)
            with pytest.raises(NegotiationNotReviewableError):
                await svc.review(
                    created.id,
                    NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                    manager,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_draft_cannot_be_reviewed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """没提交的草稿不能直接审 —— 否则 PR 还没填完就被批了。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, manager, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_NODRAFT"
            )
            svc = NegotiationService(session)
            created = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            with pytest.raises(NegotiationNotReviewableError):
                await svc.review(
                    created.id,
                    NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                    manager,
                )
        finally:
            tenant_id_ctx.reset(token)


class TestListAndHistory:
    async def test_list_puts_pending_first(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """待审核排最前 —— 主管打开页面就该先看到要处理的。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, _, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_SORT"
            )
            other_style = await product_factory.style(style_code="NG_SORT2")
            svc = NegotiationService(session)
            await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            to_submit = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=other_style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            await svc.submit(to_submit.id, pr)

            items, total = await svc.list_negotiations(user=pr, filters=NegotiationListFilters())
            assert total == 2
            assert items[0].status == "待审核"
            assert items[1].status == "草稿"

            counts = await svc.status_counts(pr)
            assert counts["草稿"] == 1
            assert counts["待审核"] == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_blogger_history_comes_from_promotions(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """hover 卡的历史合作取自推广单，草稿谈款不算。

        草稿和被驳回的谈款没真推出去，不该出现在「历史合作款式」里。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr, manager, style, blogger = await _setup(
                tenant_a, factory, admin_role, product_factory, blogger_factory, code="NG_HIST"
            )
            draft_style = await product_factory.style(style_code="NG_HIST_DRAFT")
            svc = NegotiationService(session)

            # 一张走到审核通过 → 会生成推广单
            approved = await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )
            await svc.submit(approved.id, pr)
            await svc.review(
                approved.id,
                NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                manager,
            )
            # 一张留在草稿 → 不该出现在历史里
            await svc.create(
                NegotiationCreate(
                    blogger_id=blogger.id,
                    style_id=draft_style.id,
                    cooperation_mode=CooperationMode.GIFT,
                ),
                pr,
            )

            history = await svc.blogger_history(blogger.id, pr, limit=5)
            assert history.total_cooperations == 1
            assert len(history.items) == 1
            item = history.items[0]
            assert item.style_code == "NG_HIST"
            assert item.cooperation_mode == "送拍"
            assert item.publish_status == "未发布"
            # 还没录点赞数，CPL 算不出来
            assert item.like_count is None
            assert item.cpl is None
        finally:
            tenant_id_ctx.reset(token)
