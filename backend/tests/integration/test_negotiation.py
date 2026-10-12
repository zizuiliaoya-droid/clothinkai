"""谈款集成测试（PRD V1.4 模块一；流程线 PR-3 起 7 态两级审核）。

守的不变量：
- 置换模式博主服务费强制 0（PRD 要求接口也校验，不能只靠前端禁用）
- 只有草稿与老板退回能改、能提交（N2、N3）；审核驳回是终态，重谈走「复制为新谈款」
- 禁止审核自己提交的单
- 主管通过只进「待老板审核」、老板定稿只进「待补收货地址」，两步都不建推广单

角色用 ``flow_users``（迁移 seed 的真实角色）：持 ``*`` 的管理员不能建谈款（N1、11-1）。
两级审核规则、定稿建单的完整用例在 ``test_negotiation_two_level.py``。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.negotiation.enums import NegotiationFinalReviewAction, NegotiationReviewAction
from app.modules.negotiation.exceptions import NegotiationSelfReviewForbiddenError
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationListFilters,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.schemas import GoodsItemIn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _setup(
    flow_users: Any, product_factory: Any, blogger_factory: Any, *, code: str
) -> tuple[Any, Any, Any, Any, Any]:
    """PR、主管、单品款式（带 1 个 SKU）、博主。审核要两个人 —— 自审会被拒。"""
    style = await product_factory.style(style_code=code)
    sku = await product_factory.sku(style)
    blogger = await blogger_factory.blogger(quote=Decimal("500.00"))
    return flow_users.pr, flow_users.pr_manager, style, sku, blogger


async def _create(
    svc: NegotiationService,
    pr: Any,
    style: Any,
    sku: Any,
    blogger: Any,
    *,
    mode: CooperationMode = CooperationMode.GIFT,
    **kw: Any,
) -> Any:
    """一张提交必填项都齐的草稿（颜色尺码、约定发布时间）。"""
    kw.setdefault("scheduled_publish_date", date(2026, 8, 1))
    return await svc.create(
        NegotiationCreate(
            blogger_id=blogger.id,
            style_id=style.id,
            cooperation_mode=mode,
            items=[GoodsItemIn(style_id=style.id, sku_id=sku.id)],
            **kw,
        ),
        pr,
    )


@pytest.mark.usefixtures("tenant_ctx")
class TestCreateNegotiation:
    async def test_create_lands_as_draft(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        pr, _, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_DRAFT"
        )
        resp = await _create(NegotiationService(session), pr, style, sku, blogger)
        assert resp.status == "草稿"
        assert resp.promotion_id is None
        assert resp.pr_id == pr.id
        assert resp.blogger_nickname == blogger.nickname
        assert resp.style_code == "NG_DRAFT"
        assert [(i.style_id, i.sku_id) for i in resp.items] == [(style.id, sku.id)]
        # 没传报价 → 取 blogger.quote
        assert resp.quote_amount == Decimal("500.00")

    async def test_barter_forces_quote_to_zero(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """置换：前端硬塞报价也要被压回 0（PRD 要求接口校验防绕过）。"""
        pr, _, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_BARTER"
        )
        resp = await _create(
            NegotiationService(session),
            pr,
            style,
            sku,
            blogger,
            mode=CooperationMode.BARTER,
            quote_amount=Decimal("800.00"),
        )
        assert resp.cooperation_mode == "置换"
        assert resp.quote_amount == Decimal("0.00")

    async def test_switching_to_barter_on_update_zeroes_quote(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """草稿阶段把模式改成置换，服务费要跟着归零。"""
        pr, _, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_SWITCH"
        )
        svc = NegotiationService(session)
        created = await _create(svc, pr, style, sku, blogger)
        assert created.quote_amount == Decimal("500.00")

        updated = await svc.update(
            created.id, NegotiationUpdate(cooperation_mode=CooperationMode.BARTER), pr
        )
        assert updated.cooperation_mode == "置换"
        assert updated.quote_amount == Decimal("0.00")


@pytest.mark.usefixtures("tenant_ctx")
class TestSubmitAndReview:
    async def test_two_levels_before_receiver_no_promotion(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """主管通过 → 待老板审核；老板定稿 → 待补收货地址。两步都不建推广单（确认收货信息时才建，N9）。"""
        pr, manager, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_APPROVE"
        )
        svc = NegotiationService(session)
        created = await _create(
            svc,
            pr,
            style,
            sku,
            blogger,
            mode=CooperationMode.CONSIGNMENT,
            scheduled_publish_date=date(2026, 8, 10),
        )
        submitted = await svc.submit(created.id, pr)
        assert submitted.status == "主管待审"
        assert submitted.submitted_at is not None

        approved = await svc.review(
            created.id,
            NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
            manager,
        )
        assert (approved.status, approved.reviewed_by, approved.promotion_id) == (
            "待老板审核",
            manager.id,
            None,
        )
        finalized = await svc.final_review(
            created.id,
            NegotiationFinalReviewRequest(action=NegotiationFinalReviewAction.APPROVE),
            flow_users.admin,
        )
        assert (finalized.status, finalized.final_reviewed_by, finalized.promotion_id) == (
            "待补收货地址",
            flow_users.admin.id,
            None,
        )
        count = (
            await session.execute(
                sa_text("SELECT COUNT(*) FROM promotion WHERE blogger_id = :b"),
                {"b": blogger.id},
            )
        ).scalar_one()
        assert count == 0

    async def test_rejected_is_terminal(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """驳回 = 终态「审核驳回」（Q1）：不能再改、不能再提交，重谈走「复制为新谈款」（N10）。"""
        pr, manager, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_REJECT"
        )
        svc = NegotiationService(session)
        created = await _create(svc, pr, style, sku, blogger)
        await svc.submit(created.id, pr)
        rejected = await svc.review(
            created.id,
            NegotiationReviewRequest(
                action=NegotiationReviewAction.REJECT, review_opinion="报价偏高，再谈一轮"
            ),
            manager,
        )
        assert rejected.status == "审核驳回"
        assert rejected.review_opinion == "报价偏高，再谈一轮"
        assert rejected.promotion_id is None

        with pytest.raises(IllegalStateTransitionError) as edit_exc:
            await svc.update(created.id, NegotiationUpdate(quote_amount=Decimal("300.00")), pr)
        assert edit_exc.value.code == "ILLEGAL_STATE_TRANSITION"
        with pytest.raises(IllegalStateTransitionError) as submit_exc:
            await svc.submit(created.id, pr)
        assert submit_exc.value.code == "ILLEGAL_STATE_TRANSITION"
        again = await svc.get(created.id, pr)
        assert (again.status, again.review_opinion) == ("审核驳回", "报价偏高，再谈一轮")

    async def test_reject_requires_opinion(self) -> None:
        """驳回必须给审核意见 —— PR 得知道要改什么。schema 层就该挡住。"""
        with pytest.raises(PydanticValidationError):
            NegotiationReviewRequest(action=NegotiationReviewAction.REJECT)

    async def test_self_review_forbidden(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """自己谈的款自己批，审核这道关就没意义了：主管自建的谈款自己审不了。"""
        _, manager, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_SELF"
        )
        svc = NegotiationService(session)
        created = await _create(svc, manager, style, sku, blogger)
        await svc.submit(created.id, manager)
        with pytest.raises(NegotiationSelfReviewForbiddenError) as exc:
            await svc.review(
                created.id,
                NegotiationReviewRequest(action=NegotiationReviewAction.APPROVE),
                manager,
            )
        assert exc.value.code == "NEGOTIATION_SELF_REVIEW_FORBIDDEN"


@pytest.mark.usefixtures("tenant_ctx")
class TestListAndHistory:
    async def test_list_puts_pending_first(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        """主管待审排最前 —— 主管打开页面就该先看到要处理的。"""
        pr, _, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_SORT"
        )
        other_style = await product_factory.style(style_code="NG_SORT2")
        other_sku = await product_factory.sku(other_style)
        svc = NegotiationService(session)
        await _create(svc, pr, style, sku, blogger)
        to_submit = await _create(svc, pr, other_style, other_sku, blogger)
        await svc.submit(to_submit.id, pr)

        page = await svc.list_negotiations(user=pr, filters=NegotiationListFilters())
        assert page.total == 2
        assert page.items[0].status == "主管待审"
        assert page.items[1].status == "草稿"

        counts = await svc.status_counts(pr)
        assert counts["草稿"] == 1
        assert counts["主管待审"] == 1

    async def test_blogger_history_comes_from_promotions(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """hover 卡的历史合作取自推广单，草稿谈款不算。

        草稿和被驳回的谈款没真推出去，不该出现在「历史合作款式」里。
        """
        pr, _manager, style, sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_HIST"
        )
        draft_style = await product_factory.style(style_code="NG_HIST_DRAFT")
        draft_sku = await product_factory.sku(draft_style)
        svc = NegotiationService(session)

        # 一张定稿生成的推广单（定稿建单走 confirm-receiver，这里直接造推广单）
        await promotion_factory.promotion(
            style=style, blogger=blogger, pr=pr, cooperation_mode="送拍"
        )
        # 一张留在草稿 → 不该出现在历史里
        await _create(svc, pr, draft_style, draft_sku, blogger)

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

    async def test_blogger_history_cpl_uses_total_cost_after_7day_data(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """hover 卡的单赞成本 = 总推广成本 ÷ 7 天点赞，录过 7 天数据才有值（业务方 10-02）。

        以前分子只用博主服务费、也不看 7 天数据录没录。
        """
        pr, _manager, style, _sku, blogger = await _setup(
            flow_users, product_factory, blogger_factory, code="NG_CPL"
        )
        recorded = await promotion_factory.promotion(
            tenant=tenant_a,
            style=style,
            blogger=blogger,
            internal_code="DECPLREC0001",
            cooperation_date=date(2026, 9, 1),
            quote_amount=Decimal("300.00"),
            cost_snapshot=Decimal("50.00"),
            return_shipping_fee=Decimal("20.00"),
            like_count=100,
            platform="小红书",
        )
        recorded.metrics_recorded_at = datetime.now(UTC)
        # 点赞数是编辑 / 采集写进来的，7 天数据还没录：不算
        await promotion_factory.promotion(
            tenant=tenant_a,
            style=style,
            blogger=blogger,
            internal_code="DECPLNOREC01",
            cooperation_date=date(2026, 8, 1),
            quote_amount=Decimal("300.00"),
            like_count=100,
            platform="小红书",
        )
        await session.flush()

        history = await NegotiationService(session).blogger_history(blogger.id, pr, limit=5)
        by_code = {i.internal_code: i for i in history.items}
        # (300 + 50 + 20) / 100
        assert by_code["DECPLREC0001"].cpl == Decimal("3.7000")
        assert by_code["DECPLNOREC01"].like_count == 100
        assert by_code["DECPLNOREC01"].cpl is None
