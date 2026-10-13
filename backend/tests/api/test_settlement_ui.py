"""结款页单据信息、结款单行级 ``ui`` 与推广详情的结款块（流程线 PR-6 S11；设计 7.5、7.3、4.6、8.6）。

- 结款单列表 / 详情带推广单信息（7d-3）：推广编号、合作模式、发布链接、推广备注、推广驳回原因与重提说明、
  收款码签名（``promotion.payment_qr`` 规则，财务可见）、调整说明、品名、线号；不带收件与发货信息（Q4）
- 行级 ``ui.actions`` 由结款单矩阵按状态与角色算（D10）；自己提交的结款单驳回是「读」
- 推广详情的 ``settlement`` 块：PR 看得到应付、追加项、实付与调整说明（11-17），运营整块为 null；列表不带
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

_HIDDEN_KEYS = (
    "receiver_name",
    "receiver_phone",
    "receiver_address",
    "ship_status",
    "ship_courier",
    "ship_waybill",
    "shipped_at",
)


async def _call(session: AsyncSession, user: Any, path: str) -> Any:
    """真实路由 + 用户的有效权限（照 test_promotion_settlement_ui._call）。"""
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
            return await c.get(path)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core import attachment as att_mod
    from tests.integration.test_settlement_routing import _FakeS3

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


async def _settled_pair(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
    *,
    settlement_status: str = "待财务付款",
    promotion_status: str = "待付款",
    pr: Any | None = None,
    extra_item: bool = False,
) -> tuple[Any, Any]:
    """已发布、带收款码与收件信息的推广单 + 它的结款单（应付 500、实付 400 带调整说明）。"""
    from app.modules.finance.models import SettlementExtraItem

    owner = pr or flow_users.pr
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=owner,
        publish_status="已发布",
        settlement_status=promotion_status,
        actual_publish_date=date(2026, 5, 20),
        cooperation_mode="寄拍",
        publish_url="https://www.xiaohongshu.com/explore/abc",
        remark="博主要求周末发",
        review_reason="链接打不开",
        resubmit_note="已换成新链接",
        payment_qr=True,
        receiver_name="张三",
        receiver_phone="13812345678",
        receiver_address="杭州市某路 1 号",
        return_waybill="SF1234567890",
    )
    settlement = await settlement_factory.settlement(
        style=style,
        blogger=blogger,
        promotion=promo,
        pr=owner,
        settlement_status=settlement_status,
        payment_amount=Decimal("400.00"),
        payment_adjust_note="限流，原价 500、实付 400",
        review_action="reject" if settlement_status == "已驳回" else None,
        review_reason="收款码不对" if settlement_status == "已驳回" else None,
    )
    if extra_item:
        session.add(
            SettlementExtraItem(
                id=uuid4(),
                tenant_id=flow_users.tenant.id,
                settlement_id=settlement.id,
                item_type="赞奖",
                amount=Decimal("50.00"),
                remark="爆文奖励",
                created_by=flow_users.pr_manager.id,
            )
        )
        await session.flush()
    return promo, settlement


@pytest.mark.usefixtures("tenant_ctx")
class TestSettlementDocInfo:
    async def test_list_and_detail_carry_promotion_info(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: Any,
    ) -> None:
        promo, settlement = await _settled_pair(
            session,
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
        )
        page = await _call(
            session, flow_users.finance, f"/api/settlements/?promotion_id={promo.id}"
        )
        assert page.status_code == 200, page.text
        [row] = page.json()["items"]
        detail = await _call(session, flow_users.finance, f"/api/settlements/{settlement.id}")
        assert detail.status_code == 200, detail.text
        for body in (row, detail.json()):
            assert body["promotion_internal_code"] == promo.internal_code
            assert body["cooperation_mode"] == "寄拍"
            assert body["publish_url"] == "https://www.xiaohongshu.com/explore/abc"
            assert body["promotion_remark"] == "博主要求周末发"
            assert body["promotion_review_reason"] == "链接打不开"
            assert body["promotion_resubmit_note"] == "已换成新链接"
            assert body["payment_adjust_note"] == "限流，原价 500、实付 400"
            assert body["display_short_name"] == promo.style_short_name_snapshot
            assert body["line_id"] == str(promo.line_id)
            assert (body["payment_qr_signed_url"] or "").startswith("https://fake-r2.local/")
            # 财务看不到收件与发货信息（Q4）：响应里根本没有这些键
            assert not set(_HIDDEN_KEYS) & set(body), body.keys()
        assert row["ui"]["actions"] == {
            "reject": {"state": "enabled"},
            "upload_proof": {"state": "enabled"},
        }
        assert detail.json()["ui"]["actions"] == row["ui"]["actions"]
        # 页级 import 照旧（PR-5）
        assert page.json()["ui"]["actions"] == {"import": {"state": "enabled"}}

    async def test_list_one_query_for_promotion_info(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: Any,
    ) -> None:
        """单据信息整页一次批量取（7.5）：三张结款单各有各的推广编号与收款码，互不串。"""
        pairs = [
            await _settled_pair(
                session,
                flow_users,
                product_factory,
                blogger_factory,
                promotion_factory,
                settlement_factory,
                settlement_status=status,
            )
            for status in ("待付款", "待财务付款", "已付款")
        ]
        page = await _call(session, flow_users.admin, "/api/settlements/?page_size=100")
        assert page.status_code == 200, page.text
        by_id = {r["id"]: r for r in page.json()["items"]}
        for promo, settlement in pairs:
            row = by_id[str(settlement.id)]
            assert row["promotion_internal_code"] == promo.internal_code
            assert row["line_id"] == str(promo.line_id)
            assert str(promo.payment_qr_attachment_id) in row["payment_qr_signed_url"]
        actions = {r["settlement_status"]: set(r["ui"]["actions"]) for r in by_id.values()}
        assert actions["待付款"] == {"reject", "extra_item", "fill_payment"}
        assert actions["待财务付款"] == {"reject", "upload_proof"}
        assert actions["已付款"] == set()

    async def test_self_submitted_reject_is_read(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: Any,
    ) -> None:
        """主管负责的推广单的结款单：驳回按钮禁用，原因同 service 的自审拒绝；主管看得到调整说明。"""
        _promo, settlement = await _settled_pair(
            session,
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="待付款",
            pr=flow_users.pr_manager,
        )
        detail = await _call(session, flow_users.pr_manager, f"/api/settlements/{settlement.id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["ui"]["actions"]["reject"] == {
            "state": "disabled",
            "reason": "不能审核自己提交的推广对应的结款单",
        }
        assert body["ui"]["actions"]["fill_payment"] == {"state": "enabled"}
        assert body["payment_adjust_note"] == "限流，原价 500、实付 400"
        assert body["payment_qr_signed_url"] is not None


@pytest.mark.usefixtures("tenant_ctx")
class TestPromotionSettlementBlock:
    async def test_detail_block_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: Any,
    ) -> None:
        promo, settlement = await _settled_pair(
            session,
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            extra_item=True,
        )
        expected = {
            "settlement_id": str(settlement.id),
            "status": "待财务付款",
            "amount": "500.00",
            "extra_items": [{"item_type": "赞奖", "amount": "50.00", "remark": "爆文奖励"}],
            "total_amount": "500.00",
            "payment_amount": "400.00",
            "payment_adjust_note": "限流，原价 500、实付 400",
            "payment_date": None,
            "reject_reason": None,
        }
        for who in ("pr", "pr_manager", "admin"):
            detail = await _call(session, getattr(flow_users, who), f"/api/promotions/{promo.id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["settlement"] == expected, who
        # 运营：矩阵「结款单」一行隐（金额规则也不含运营），整块 null
        ops = await _call(session, flow_users.operations, f"/api/promotions/{promo.id}")
        assert ops.status_code == 200, ops.text
        assert ops.json()["settlement"] is None
        # 列表不带结款块（7.3：只在详情）
        page = await _call(
            session, flow_users.pr, f"/api/promotions/?page_size=20&keyword={promo.internal_code}"
        )
        [row] = [r for r in page.json()["items"] if r["id"] == str(promo.id)]
        assert row["settlement"] is None

    async def test_block_reject_reason_and_absent(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: Any,
    ) -> None:
        promo, _settlement = await _settled_pair(
            session,
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="已驳回",
            promotion_status="结款驳回",
        )
        detail = await _call(session, flow_users.pr, f"/api/promotions/{promo.id}")
        assert detail.status_code == 200, detail.text
        block = detail.json()["settlement"]
        assert (block["status"], block["reject_reason"]) == ("已驳回", "收款码不对")
        # 还没有结款单：null
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        bare = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            publish_status="已发布",
            settlement_status="待核查",
            actual_publish_date=date(2026, 5, 20),
        )
        resp = await _call(session, flow_users.pr, f"/api/promotions/{bare.id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["settlement"] is None
