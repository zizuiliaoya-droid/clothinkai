"""7a-4 驳回后重新提交（service.resubmit）。

走真实路径：已发布 + 待核查 → 主管 review(reject) → PR resubmit，不手写驳回后的行。
注意 ``StateTransitionConflictError`` 是 ``IllegalStateTransitionError`` 的子类，
「状态机拒绝」的断言必须比 code，只 ``pytest.raises`` 分不出两者。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog
from app.modules.promotion.enums import RejectReasonCategory, ReviewAction
from app.modules.promotion.exceptions import PublishDateInFutureError
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import (
    PromotionListFilters,
    PromotionResubmitRequest,
    PromotionReviewRequest,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today

_OLD_URL = "https://www.xiaohongshu.com/note/old"
_NEW_URL = "https://www.xiaohongshu.com/note/new"
_REJECT_REASON = "发文晚了 5 天"


class _Ctx:
    def __init__(self, svc: PromotionService, pr: Any, reviewer: Any, promotion: Any) -> None:
        self.svc = svc
        self.pr = pr
        self.reviewer = reviewer
        self.promotion = promotion


async def _make(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    pr_role: Any,
    pr_manager_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    *,
    reject: bool = True,
    **kw: Any,
) -> _Ctx:
    pr = await factory.user(tenant_a, roles=[pr_role])
    reviewer = await factory.user(tenant_a, roles=[pr_manager_role])
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("publish_status", "已发布")
    kw.setdefault("settlement_status", "待核查")
    promotion = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=pr,
        publish_url=_OLD_URL,
        actual_publish_date=get_today() - timedelta(days=10),
        **kw,
    )
    svc = PromotionService(session)
    if reject:
        resp = await svc.review(
            promotion.id,
            PromotionReviewRequest(
                action=ReviewAction.REJECT,
                review_reason=_REJECT_REASON,
                review_reason_category=RejectReasonCategory.LATE_PUBLISH,
            ),
            reviewer,
        )
        assert resp.settlement_status == "已驳回"
    return _Ctx(svc, pr, reviewer, promotion)


async def _reload(session: AsyncSession, promotion_id: Any) -> Promotion:
    row = await session.get(Promotion, promotion_id, populate_existing=True)
    assert row is not None
    return row


@pytest.mark.integration
@pytest.mark.asyncio
class TestResubmit:
    async def test_resubmit_back_to_pending_review_keeps_reject_reason(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """① 回到待核查、两列有值、上一轮驳回原因保留、写 audit。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            resp = await ctx.svc.resubmit(
                ctx.promotion.id, PromotionResubmitRequest(note="  已补发，链接不变  "), ctx.pr
            )
            assert resp.settlement_status == "待核查"
            assert resp.resubmit_note == "已补发，链接不变"
            assert resp.resubmitted_at is not None
            assert resp.review_reason == _REJECT_REASON
            assert resp.review_reason_category == "延迟发文"
            assert resp.review_action == "reject"
            assert resp.reviewed_by == ctx.reviewer.id
            # 没传就不动
            assert resp.publish_url == _OLD_URL

            row = await _reload(session, ctx.promotion.id)
            assert row.settlement_status == "待核查"
            assert row.resubmit_note == "已补发，链接不变"
            assert row.review_reason_category == "延迟发文"

            audits = (
                (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.resource_id == str(ctx.promotion.id),
                            AuditLog.action == "promotion.resubmit",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audits) == 1
            assert audits[0].before == {"settlement_status": "已驳回"}
            assert audits[0].after == {"settlement_status": "待核查", "has_note": True}
            assert audits[0].user_id == ctx.pr.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_resubmit_can_change_url_and_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """② 同时改链接与日期（昨天）落库，audit 带新旧值。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            old_date = ctx.promotion.actual_publish_date
            yesterday = get_today() - timedelta(days=1)
            resp = await ctx.svc.resubmit(
                ctx.promotion.id,
                PromotionResubmitRequest(
                    note="换了链接", publish_url=_NEW_URL, actual_publish_date=yesterday
                ),
                ctx.pr,
            )
            assert resp.publish_url == _NEW_URL
            assert resp.actual_publish_date == yesterday

            row = await _reload(session, ctx.promotion.id)
            assert row.publish_url == _NEW_URL
            assert row.actual_publish_date == yesterday

            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.resource_id == str(ctx.promotion.id),
                        AuditLog.action == "promotion.resubmit",
                    )
                )
            ).scalar_one()
            assert audit.before == {
                "settlement_status": "已驳回",
                "publish_url": _OLD_URL,
                "actual_publish_date": old_date.isoformat(),
            }
            assert audit.after == {
                "settlement_status": "待核查",
                "publish_url": _NEW_URL,
                "actual_publish_date": yesterday.isoformat(),
                "has_note": True,
            }
        finally:
            tenant_id_ctx.reset(token)

    async def test_same_url_and_date_not_logged_as_change(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """②b 传了与现值相同的链接 / 日期：不算改动，audit 只记状态。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            resp = await ctx.svc.resubmit(
                ctx.promotion.id,
                PromotionResubmitRequest(
                    note="原样重提",
                    publish_url=_OLD_URL,
                    actual_publish_date=ctx.promotion.actual_publish_date,
                ),
                ctx.pr,
            )
            assert resp.settlement_status == "待核查"
            assert resp.publish_url == _OLD_URL

            audit = (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.resource_id == str(ctx.promotion.id),
                        AuditLog.action == "promotion.resubmit",
                    )
                )
            ).scalar_one()
            assert audit.before == {"settlement_status": "已驳回"}
            assert audit.after == {"settlement_status": "待核查", "has_note": True}
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize("status", ["未核查", "待核查", "待付款", "已付款"])
    async def test_only_rejected_can_resubmit(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        status: str,
    ) -> None:
        """③ 非已驳回 → ILLEGAL_STATE_TRANSITION（不是 409 并发冲突），单据不变。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                reject=False,
                settlement_status=status,
            )
            with pytest.raises(IllegalStateTransitionError) as exc_info:
                await ctx.svc.resubmit(
                    ctx.promotion.id, PromotionResubmitRequest(note="重提"), ctx.pr
                )
            assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
            assert exc_info.value.status_code == 422

            row = await _reload(session, ctx.promotion.id)
            assert row.settlement_status == status
            assert row.resubmit_note is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_state_machine_checked_before_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """④ 待核查 + 明天：报状态错，不报日期错。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                reject=False,
            )
            with pytest.raises(IllegalStateTransitionError) as exc_info:
                await ctx.svc.resubmit(
                    ctx.promotion.id,
                    PromotionResubmitRequest(
                        note="重提", actual_publish_date=get_today() + timedelta(days=1)
                    ),
                    ctx.pr,
                )
            assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
        finally:
            tenant_id_ctx.reset(token)

    async def test_future_date_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑤ 已驳回 + 明天 → PUBLISH_DATE_IN_FUTURE，仍已驳回、没写重提说明。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            with pytest.raises(PublishDateInFutureError) as exc_info:
                await ctx.svc.resubmit(
                    ctx.promotion.id,
                    PromotionResubmitRequest(
                        note="重提", actual_publish_date=get_today() + timedelta(days=1)
                    ),
                    ctx.pr,
                )
            assert exc_info.value.code == "PUBLISH_DATE_IN_FUTURE"

            row = await _reload(session, ctx.promotion.id)
            assert row.settlement_status == "已驳回"
            assert row.resubmit_note is None
            assert row.resubmitted_at is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_requires_published(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """已驳回但不是已发布（历史数据）：进了待核查也批不了，直接拒。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                reject=False,
                publish_status="异常",
                settlement_status="已驳回",
            )
            with pytest.raises(IllegalStateTransitionError) as exc_info:
                await ctx.svc.resubmit(
                    ctx.promotion.id, PromotionResubmitRequest(note="重提"), ctx.pr
                )
            assert exc_info.value.code == "PROMOTION_STATE_CONFLICT"
            row = await _reload(session, ctx.promotion.id)
            assert row.settlement_status == "已驳回"
        finally:
            tenant_id_ctx.reset(token)

    async def test_resubmit_then_approve_requests_settlement(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        event_capture: list[Any],
    ) -> None:
        """⑥a 重提后主管通过 → 待付款 + SettlementRequested。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            await ctx.svc.resubmit(ctx.promotion.id, PromotionResubmitRequest(note="补了"), ctx.pr)
            resp = await ctx.svc.review(
                ctx.promotion.id,
                PromotionReviewRequest(action=ReviewAction.APPROVE),
                ctx.reviewer,
            )
            assert resp.settlement_status == "待付款"
            events = [e for e in event_capture if e.event_type == "SettlementRequested"]
            assert [e.promotion_id for e in events] == [ctx.promotion.id]
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize(
        ("cooperation_mode", "approve_reason", "expected_status"),
        [
            (None, None, "待付款"),
            ("置换", None, "已付款"),
            # 接口允许通过时带说明（前端不传）：同样不覆盖上一轮的驳回说明
            (None, "通过备注", "待付款"),
        ],
    )
    async def test_approve_after_resubmit_keeps_last_reject_reason(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        event_capture: list[Any],
        cooperation_mode: str | None,
        approve_reason: str | None,
        expected_status: str,
    ) -> None:
        """⑥c 驳回 → 重提 → 通过：上一轮驳回说明、分类与重提说明都还在（响应、库、列表）。

        「最近一轮的驳回原因与重提说明」要留到结款环节（7d-3）；结算状态列在待付款 /
        已付款上靠 review_reason_category 显示「上轮驳回」。通过时清掉就再也找不回来了：
        驳回的 audit 只记分类，不记说明文字。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                cooperation_mode=cooperation_mode,
            )
            await ctx.svc.resubmit(ctx.promotion.id, PromotionResubmitRequest(note="补了"), ctx.pr)
            resp = await ctx.svc.review(
                ctx.promotion.id,
                PromotionReviewRequest(action=ReviewAction.APPROVE, review_reason=approve_reason),
                ctx.reviewer,
            )
            assert resp.settlement_status == expected_status
            # 审核人 / 动作 / 时间是这一次通过的
            assert resp.review_action == "approve"
            assert resp.reviewed_by == ctx.reviewer.id
            # 驳回说明与分类是上一轮驳回的
            assert resp.review_reason == _REJECT_REASON
            assert resp.review_reason_category == "延迟发文"
            assert resp.resubmit_note == "补了"
            assert resp.resubmitted_at is not None
            settlement_events = [
                e.promotion_id for e in event_capture if e.event_type == "SettlementRequested"
            ]
            assert settlement_events == ([] if expected_status == "已付款" else [ctx.promotion.id])

            row = await _reload(session, ctx.promotion.id)
            assert row.settlement_status == expected_status
            assert row.review_reason == _REJECT_REASON
            assert row.review_reason_category == "延迟发文"
            assert row.resubmit_note == "补了"

            page = await ctx.svc.list_promotions(
                filters=PromotionListFilters(keyword=ctx.promotion.internal_code),
                page=1,
                page_size=20,
                user=ctx.pr,
            )
            items = [p for p in page.items if p.id == ctx.promotion.id]
            assert len(items) == 1
            item = items[0]
            assert item.settlement_status == expected_status
            assert item.review_reason == _REJECT_REASON
            assert item.review_reason_category == "延迟发文"
            assert item.resubmit_note == "补了"
        finally:
            tenant_id_ctx.reset(token)

    async def test_resubmit_then_reject_again_overwrites_reason_keeps_note(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑥b 重提后再驳回：新原因覆盖，重提说明保留上一轮。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            await ctx.svc.resubmit(
                ctx.promotion.id, PromotionResubmitRequest(note="第一轮重提"), ctx.pr
            )
            resp = await ctx.svc.review(
                ctx.promotion.id,
                PromotionReviewRequest(
                    action=ReviewAction.REJECT,
                    review_reason="流量还是不行",
                    review_reason_category=RejectReasonCategory.TRAFFIC_REDO,
                ),
                ctx.reviewer,
            )
            assert resp.settlement_status == "已驳回"
            assert resp.review_reason == "流量还是不行"
            assert resp.review_reason_category == "流量差补发"
            assert resp.resubmit_note == "第一轮重提"
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_returns_resubmit_fields(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑦ 列表也返回 resubmit_note / resubmitted_at。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            ctx = await _make(
                session,
                tenant_a,
                factory,
                pr_role,
                pr_manager_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            await ctx.svc.resubmit(
                ctx.promotion.id, PromotionResubmitRequest(note="列表可见"), ctx.pr
            )
            page = await ctx.svc.list_promotions(
                filters=PromotionListFilters(keyword=ctx.promotion.internal_code),
                page=1,
                page_size=20,
                user=ctx.pr,
            )
            items = [p for p in page.items if p.id == ctx.promotion.id]
            assert len(items) == 1
            assert items[0].resubmit_note == "列表可见"
            assert items[0].resubmitted_at is not None
            assert items[0].review_reason_category == "延迟发文"
        finally:
            tenant_id_ctx.reset(token)
