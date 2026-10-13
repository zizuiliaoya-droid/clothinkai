"""推广审核按应付分流（流程线 PR-6 S6；设计 3.4 R2 / R3 / R2 ⑥、7.3 review 行、9.3 D 块）。

- 应付分流（D1 / D2）：合作模式 × 服务费 × 收款码。服务费 0 → 已结款、写 ``settled_at``、不发 ``SettlementRequested``、
  没有结款单（寄拍、送拍也一样）；300 没码 → 422 缺 ``payment_qr``、状态不变；300 有码 → 待付款，结款单直接建在待付款、
  ``amount = 300``；响应的 ``review_outcome`` 跟着去向
- 推广审核权（D4）：路由挂 ``promotion_review:approve``，PR 在路由层 403；主管审自己负责的单 → 403 ``SELF_REVIEW_FORBIDDEN``
- 已有结款单（D13）：导入的已付款单 → 推广单记已付款、不发事件、结款单仍 1 张；其他状态 → 409 ``SETTLEMENT_ALREADY_EXISTS``
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.exceptions import FlowGateMissingError
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.enums import RejectReasonCategory, ReviewAction
from app.modules.promotion.schemas import PromotionReviewRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.flow_event_helpers import events_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_APPROVE = PromotionReviewRequest(action=ReviewAction.APPROVE)


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any = None) -> Any:
    """真实路由 + 用户的有效权限（同 ``test_negotiation_api._call``）。"""
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _pending_review(
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    *,
    pr: Any | None = None,
    **kw: Any,
) -> Any:
    """已发布、默认待核查（阶段 待主管审核）的推广单；发布日在 10 天前（满 7 天）。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("actual_publish_date", get_today() - timedelta(days=10))
    kw.setdefault("settlement_status", "待核查")
    return await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=pr or flow_users.pr,
        publish_status="已发布",
        **kw,
    )


async def _settlement_rows(session: AsyncSession, promotion_id: UUID) -> list[Any]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT settlement_status, amount FROM settlement "
                    "WHERE promotion_id = CAST(:p AS uuid)"
                ),
                {"p": promotion_id},
            )
        ).all()
    )


async def _promotion_state(session: AsyncSession, promotion_id: UUID) -> tuple[str, Any]:
    row = (
        await session.execute(
            text("SELECT settlement_status, settled_at FROM promotion WHERE id = CAST(:p AS uuid)"),
            {"p": promotion_id},
        )
    ).one()
    return row[0], row[1]


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestPayableRouting:
    @pytest.mark.parametrize("has_qr", [True, False], ids=["有码", "无码"])
    @pytest.mark.parametrize(
        "fee", [Decimal("0.00"), Decimal("300.00")], ids=["服务费0", "服务费300"]
    )
    @pytest.mark.parametrize(
        "mode", ["寄拍", "送拍", "置换", None], ids=["寄拍", "送拍", "置换", "空"]
    )
    async def test_route_by_payable(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        mode: str | None,
        fee: Decimal,
        has_qr: bool,
    ) -> None:
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode=mode,
            quote_amount=fee,
            payment_qr=has_qr,
            # 寄拍要有寄回单号（★¹⁵），这里只测分流
            return_waybill="SF1234567890" if mode == "寄拍" else None,
        )
        pid, line_id = promo.id, promo.line_id
        svc = PromotionService(session)
        if fee == 0:
            resp = await svc.review(pid, _APPROVE, flow_users.pr_manager)
            assert resp.settlement_status == "已结款"
            assert resp.review_outcome == "settled_directly"
            assert resp.settled_at is not None
            assert await _settlement_rows(session, pid) == [], "应付 0 不建结款单"
            settled = await events_of(
                session, line_id=line_id, event_type=LineEventType.PROMOTION_SETTLED
            )
            assert [(e.from_state, e.to_state) for e in settled] == [("待核查", "已结款")]
            # 场景有效：满 7 天、已结款、未录 → 可录角标
            assert "metrics_due" in resp.badges
        elif not has_qr:
            with pytest.raises(FlowGateMissingError) as exc:
                await svc.review(pid, _APPROVE, flow_users.pr_manager)
            assert exc.value.status_code == 422
            assert [m["key"] for m in exc.value.details["missing"]] == ["payment_qr"]
            assert await _promotion_state(session, pid) == ("待核查", None)
            assert await _settlement_rows(session, pid) == []
        else:
            resp = await svc.review(pid, _APPROVE, flow_users.pr_manager)
            assert resp.settlement_status == "待付款"
            assert resp.review_outcome == "settlement_created"
            assert resp.settled_at is None
            assert await _settlement_rows(session, pid) == [("待付款", Decimal("300.00"))]

    async def test_no_settlement_requested_when_fee_zero(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        event_capture: list[Any],
    ) -> None:
        """应付 0 不发事件（不是「发了没人接」：结款单监听也注册着）。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="寄拍",
            quote_amount=Decimal("0.00"),
            return_waybill="SF1",
        )
        await PromotionService(session).review(promo.id, _APPROVE, flow_users.pr_manager)
        assert [e for e in event_capture if e.event_type == "SettlementRequested"] == []

    async def test_consignment_needs_return_waybill(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """寄拍缺寄回单号与缺码一起报（缺项顺序 = 矩阵 Gate 顺序）；驳回不卡。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="寄拍",
            quote_amount=Decimal("300.00"),
        )
        svc = PromotionService(session)
        with pytest.raises(FlowGateMissingError) as exc:
            await svc.review(promo.id, _APPROVE, flow_users.pr_manager)
        assert [m["key"] for m in exc.value.details["missing"]] == ["return_waybill", "payment_qr"]
        rejected = await svc.review(
            promo.id,
            PromotionReviewRequest(
                action=ReviewAction.REJECT,
                review_reason="延迟",
                review_reason_category=RejectReasonCategory.LATE_PUBLISH,
            ),
            flow_users.pr_manager,
        )
        assert rejected.settlement_status == "已驳回"
        assert rejected.review_outcome is None

    async def test_metrics_available_from_and_missing_qr_badge(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        published = date(2026, 5, 20)
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="送拍",
            quote_amount=Decimal("300.00"),
            actual_publish_date=published,
        )
        detail = await _call(session, flow_users.pr_manager, "GET", f"/api/promotions/{promo.id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["metrics_available_from"] == "2026-05-27"
        assert body["badges"][0] == "missing_qr"
        assert body["settled_at"] is None and body["review_outcome"] is None
        assert body["ui"]["actions"]["review"]["missing"] == [
            {"key": "payment_qr", "label": "收款码"}
        ]


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestReviewPermission:
    async def test_pr_denied_at_route(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """D4：PR 持 promotion.*:*，也调不了推广审核（独立一级域）；挡在路由层，不进 service。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            pr=flow_users.pr2,
            payment_qr=True,
        )
        path = f"/api/promotions/{promo.id}/review"
        for who in ("pr", "finance", "operations"):
            resp = await _call(
                session, getattr(flow_users, who), "POST", path, {"action": "approve"}
            )
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), who
            assert resp.json()["details"] == {
                "required_scope": "promotion_review",
                "required_action": "approve",
            }, who
        assert (await _promotion_state(session, promo.id))[0] == "待核查"
        resp = await _call(session, flow_users.admin, "POST", path, {"action": "approve"})
        assert resp.status_code == 200, resp.text
        assert (resp.json()["settlement_status"], resp.json()["review_outcome"]) == (
            "待付款",
            "settlement_created",
        )

    async def test_manager_self_review_forbidden(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            pr=flow_users.pr_manager,
            payment_qr=True,
        )
        resp = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/promotions/{promo.id}/review",
            {"action": "approve"},
        )
        assert (resp.status_code, resp.json()["code"]) == (403, "SELF_REVIEW_FORBIDDEN"), resp.text
        assert (await _promotion_state(session, promo.id))[0] == "待核查"

    async def test_not_pending_review_is_illegal(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """状态机先判（3.1）：已驳回的单再审 → 422 ILLEGAL_STATE_TRANSITION。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            payment_qr=True,
            settlement_status="已驳回",
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).review(promo.id, _APPROVE, flow_users.pr_manager)
        assert (exc.value.status_code, exc.value.code) == (422, "ILLEGAL_STATE_TRANSITION")


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestExistingSettlement:
    async def test_imported_paid_settlement_marks_paid(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """D13：已有导入的已付款结款单 → 推广单记已付款（等 R9）、不发事件、结款单仍 1 张。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            publish_status="已发布",
            settlement_status="待核查",
            quote_amount=Decimal("300.00"),
            payment_qr=True,
        )
        await settlement_factory.settlement(
            style=style,
            blogger=blogger,
            promotion=promo,
            pr=flow_users.pr,
            settlement_status="已付款",
            amount=Decimal("280.00"),
            total_amount=Decimal("280.00"),
        )
        resp = await PromotionService(session).review(promo.id, _APPROVE, flow_users.pr_manager)
        assert (resp.settlement_status, resp.review_outcome) == (
            "已付款",
            "imported_settlement_paid",
        )
        assert await _settlement_rows(session, promo.id) == [("已付款", Decimal("280.00"))]
        approved = await events_of(
            session, line_id=promo.line_id, event_type=LineEventType.REVIEW_APPROVED
        )
        assert [e.payload["destination"] for e in approved] == ["已付款"]
        created = await events_of(
            session, line_id=promo.line_id, event_type=LineEventType.SETTLEMENT_CREATED
        )
        assert created == []

    @pytest.mark.parametrize("status", ["待核查", "待付款", "待财务付款", "已驳回"])
    async def test_other_settlement_conflicts(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        status: str,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            publish_status="已发布",
            settlement_status="待核查",
            quote_amount=Decimal("300.00"),
            payment_qr=True,
        )
        settlement = await settlement_factory.settlement(
            style=style,
            blogger=blogger,
            promotion=promo,
            pr=flow_users.pr,
            settlement_status=status,
        )
        resp = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/promotions/{promo.id}/review",
            {"action": "approve"},
        )
        assert (resp.status_code, resp.json()["code"]) == (
            409,
            "SETTLEMENT_ALREADY_EXISTS",
        ), resp.text
        details = resp.json()["details"]
        assert (details["settlement_no"], details["settlement_status"]) == (
            settlement.settlement_no,
            status,
        )
        assert await _promotion_state(session, promo.id) == ("待核查", None)
        assert len(await _settlement_rows(session, promo.id)) == 1
