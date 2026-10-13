"""推广审核按应付分流（流程线 PR-6 S6；设计 3.4 R2 / R3 / R2 ⑥、7.3 review 行、9.3 D 块）。

- 应付分流（D1 / D2）：合作模式 × 服务费 × 收款码。服务费 0 → 已结款、写 ``settled_at``、不发 ``SettlementRequested``、
  没有结款单（寄拍、送拍也一样）；300 没码 → 422 缺 ``payment_qr``、状态不变；300 有码 → 待付款，结款单直接建在待付款、
  ``amount = 300``；响应的 ``review_outcome`` 跟着去向
- 推广审核权（D4）：路由挂 ``promotion_review:approve``，PR 在路由层 403；主管审自己负责的单 → 403 ``SELF_REVIEW_FORBIDDEN``
- 已有结款单（D13）：导入的已付款单 → 推广单记已付款、不发事件、结款单仍 1 张；其他状态 → 409 ``SETTLEMENT_ALREADY_EXISTS``
- 结款聊天截图（R9，S8）：已付款 → 已结款、写 ``settled_at`` 与 ``promotion.settled``（附件 = 截图）；未付款时传 → 422、
  不传对象；条件 UPDATE 落空 → 409 且删掉已传的对象；路由挂 ``promotion:write``（财务、运营 403）
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


async def _call(
    session: AsyncSession,
    user: Any,
    method: str,
    path: str,
    json: Any = None,
    *,
    files: Any = None,
) -> Any:
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
            return await c.request(method, path, json=json, files=files)
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


async def _existing_status_in_list_and_detail(
    session: AsyncSession, user: Any, promotion_id: UUID
) -> tuple[dict[str, Any], dict[str, Any]]:
    """同一个人看列表行与详情，各返回这张推广单的响应体。"""
    listed = await _call(session, user, "GET", "/api/promotions/?page_size=100")
    assert listed.status_code == 200, listed.text
    rows = [r for r in listed.json()["items"] if r["id"] == str(promotion_id)]
    assert len(rows) == 1, "列表里要有这张单"
    detail = await _call(session, user, "GET", f"/api/promotions/{promotion_id}")
    assert detail.status_code == 200, detail.text
    return rows[0], detail.json()


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestExistingSettlementStatusField:
    """L4：推广响应的 ``existing_settlement_status``（审核确认框写去向）。列表与详情都给，
    取自已批量查的结款引用；结款分组对当前用户是隐（运营）时给 null。"""

    async def _imported_paid(
        self,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> Any:
        """R2 ⑥：待主管审核、服务费 300，已有导入的已付款结款单。"""
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
        return promo

    async def test_imported_paid_settlement_in_list_and_detail(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        promo = await self._imported_paid(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        row, detail = await _existing_status_in_list_and_detail(
            session, flow_users.pr_manager, promo.id
        )
        assert row["existing_settlement_status"] == "已付款"
        assert detail["existing_settlement_status"] == "已付款"
        # 场景有效：仍在待主管审核、审核可点；结款分组照旧灰（D 列不因新字段改显示）
        assert row["settlement_status"] == "待核查"
        assert row["ui"]["actions"]["review"]["state"] == "enabled"
        assert detail["ui"]["fields"]["settlement"]["state"] == "grey"
        assert detail["settlement"] is None

    async def test_no_settlement_is_null(
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
            cooperation_mode="送拍",
            quote_amount=Decimal("300.00"),
            payment_qr=True,
        )
        row, detail = await _existing_status_in_list_and_detail(
            session, flow_users.pr_manager, promo.id
        )
        # 键一直在，没有结款单为 null（读不到的角色同样给 null，沿用其他隐藏字段的写法）
        assert "existing_settlement_status" in row and row["existing_settlement_status"] is None
        assert (
            "existing_settlement_status" in detail and detail["existing_settlement_status"] is None
        )
        assert await _settlement_rows(session, promo.id) == []

    async def test_operations_cannot_see(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        promo = await self._imported_paid(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        row, detail = await _existing_status_in_list_and_detail(
            session, flow_users.operations, promo.id
        )
        assert row["existing_settlement_status"] is None
        assert detail["existing_settlement_status"] is None
        # 场景有效：运营的结款分组是隐；同一张单主管看得到已付款
        assert "settlement" not in detail["ui"]["fields"]
        manager_row, _ = await _existing_status_in_list_and_detail(
            session, flow_users.pr_manager, promo.id
        )
        assert manager_row["existing_settlement_status"] == "已付款"


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _FakeS3:
    """只替掉要联网的三个调用（同 test_brand_comment_amount_log），记下传了 / 删了哪些 key。"""

    def __init__(self) -> None:
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def put_object(self, **kw: Any) -> dict[str, Any]:
        self.puts.append(kw["Key"])
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


async def _chat_state(session: AsyncSession, promotion_id: UUID) -> tuple[str, Any, Any]:
    row = (
        await session.execute(
            text(
                "SELECT settlement_status, settled_at, settlement_chat_attachment_id "
                "FROM promotion WHERE id = CAST(:p AS uuid)"
            ),
            {"p": promotion_id},
        )
    ).one()
    return row[0], row[1], row[2]


async def _chat_attachment_count(session: AsyncSession, promotion_id: UUID) -> int:
    """这张单所在租户里结款聊天截图用途的附件行数（每条用例的租户都是新的）。"""
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM attachment a JOIN promotion p ON p.tenant_id = a.tenant_id "
                    "WHERE p.id = CAST(:p AS uuid) AND a.purpose = 'settlement_chat_screenshot'"
                ),
                {"p": promotion_id},
            )
        ).scalar_one()
    )


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestSettlementChat:
    async def test_paid_to_settled(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """R9：已付款 → 已结款，写截图与 ``settled_at``；时间线一条 promotion.settled，附件 = 截图。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="送拍",
            quote_amount=Decimal("300.00"),
            payment_qr=True,
            settlement_status="已付款",
        )
        pid, line_id = promo.id, promo.line_id
        resp = await PromotionService(session).upload_settlement_chat(
            pid, filename="chat.png", mime_type="image/png", data=_PNG, user=flow_users.pr
        )
        assert resp.settlement_status == "已结款"
        assert resp.settled_at is not None
        status, settled_at, chat_id = await _chat_state(session, pid)
        assert (status, settled_at) == ("已结款", resp.settled_at)
        assert chat_id is not None
        assert len(fake_r2.puts) == 1 and fake_r2.deletes == []
        assert resp.settlement_chat_signed_url == f"https://fake-r2.local/{fake_r2.puts[0]}"
        purpose, att_status = (
            await session.execute(
                text("SELECT purpose, status FROM attachment WHERE id = CAST(:a AS uuid)"),
                {"a": chat_id},
            )
        ).one()
        assert (purpose, att_status) == ("settlement_chat_screenshot", "ready")
        settled = await events_of(
            session, line_id=line_id, event_type=LineEventType.PROMOTION_SETTLED
        )
        assert [(e.from_state, e.to_state, e.attachment_id) for e in settled] == [
            ("已付款", "已结款", chat_id)
        ]
        assert settled[0].actor_id == flow_users.pr.id
        # 场景有效：满 7 天、已结款、未录 → 可录角标；上传按钮跟着消失
        assert "metrics_due" in resp.badges
        assert resp.ui is not None and "settlement_chat" not in resp.ui["actions"]

    @pytest.mark.parametrize("status", ["待核查", "待付款", "结款驳回", "已结款"])
    async def test_not_paid_is_illegal(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
        status: str,
    ) -> None:
        """未付款（及已结款后）传 → 422 ILLEGAL_STATE_TRANSITION，状态不变、不建附件、不传对象。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            cooperation_mode="送拍",
            quote_amount=Decimal("300.00"),
            payment_qr=True,
            settlement_status=status,
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).upload_settlement_chat(
                promo.id, filename="chat.png", mime_type="image/png", data=_PNG, user=flow_users.pr
            )
        assert (exc.value.status_code, exc.value.code) == (422, "ILLEGAL_STATE_TRANSITION")
        assert await _chat_state(session, promo.id) == (status, None, None)
        assert await _chat_attachment_count(session, promo.id) == 0
        assert fake_r2.puts == []

    async def test_fake_image_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            quote_amount=Decimal("300.00"),
            payment_qr=True,
            settlement_status="已付款",
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).upload_settlement_chat(
                promo.id,
                filename="x.png",
                mime_type="image/png",
                data=b"not a png",
                user=flow_users.pr,
            )
        assert (exc.value.status_code, exc.value.code) == (
            422,
            "SETTLEMENT_CHAT_SCREENSHOT_REQUIRED",
        )
        assert await _chat_state(session, promo.id) == ("已付款", None, None)
        assert fake_r2.puts == []

    async def test_conflict_rolls_back_and_deletes_object(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """条件 UPDATE 落空（并发已被改走）→ 409，附件行回滚、已传的对象补偿删除。"""
        from app.modules.promotion.repository import PromotionRepository

        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            quote_amount=Decimal("300.00"),
            payment_qr=True,
            settlement_status="已付款",
        )
        pid = promo.id
        await session.commit()

        async def _lost(self: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(PromotionRepository, "update_state", _lost)
        with pytest.raises(AppException) as exc:
            await PromotionService(session).upload_settlement_chat(
                pid, filename="chat.png", mime_type="image/png", data=_PNG, user=flow_users.pr
            )
        assert exc.value.status_code == 409
        assert len(fake_r2.puts) == 1, "场景有效：对象已经传上去了"
        assert fake_r2.deletes == fake_r2.puts
        assert await _chat_state(session, pid) == ("已付款", None, None)
        assert await _chat_attachment_count(session, pid) == 0

    async def test_route_scope_and_visibility(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """路由挂 promotion:write：财务、运营 403；PR 传成功。截图签名跟收款码的可见性，运营看不到。"""
        promo = await _pending_review(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            quote_amount=Decimal("300.00"),
            payment_qr=True,
            settlement_status="已付款",
        )
        path = f"/api/promotions/{promo.id}/settlement-chat"

        def _files() -> dict[str, Any]:
            return {"file": ("chat.png", _PNG, "image/png")}

        for who in ("finance", "operations"):
            resp = await _call(session, getattr(flow_users, who), "POST", path, files=_files())
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), who
        assert (await _chat_state(session, promo.id))[0] == "已付款"
        assert fake_r2.puts == []

        resp = await _call(session, flow_users.pr, "POST", path, files=_files())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["settlement_status"] == "已结款"
        assert body["settlement_chat_signed_url"] is not None

        detail = await _call(session, flow_users.operations, "GET", f"/api/promotions/{promo.id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["settlement_chat_signed_url"] is None
        detail = await _call(session, flow_users.pr_manager, "GET", f"/api/promotions/{promo.id}")
        assert detail.json()["settlement_chat_signed_url"] is not None
