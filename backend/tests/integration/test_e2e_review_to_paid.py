"""推广审核到已结款的端到端旅程（流程线 PR-6，设计 3.4 R2 ~ R9 / F1 ~ F6、3.7 的 7 天门槛）。

全程走真实路由 + 迁移 seed 的真实角色，跨模块监听用真实 handler（``cross_unit_event_bus``）：

主管审核通过（应付 > 0，有收款码）→ 结款单直接建在待付款（F1）
→ 财务填实付（F3）后、付款前驳回（F5 → R6：推广单结款驳回）
→ PR 结款重提（R7 → F6：两边回到待付款，上一轮的实付 / 说明清空）
→ 财务重填实付 ≠ 应付 + 调整说明（F3）→ 财务传付款凭证（F4，付款日期 = 北京时间当天 → R8 推广单已付款）
→ PR 传结款聊天截图（R9 → 已结款，写 ``settled_at``）→ PR 录 7 天数据（只录一次）。

沿途断言每一步的权限边界（PR 审核 403、PR 碰结款单 403）与门槛（缺调整说明 422、未结款录数据 422），
最后核对时间线事件的顺序，并确认重放的 ``SettlementPaid`` 不会把已结款打回已付款（3.4 末段的口径）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import dispatch
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.events import SettlementPaid
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.urge_calculator import get_today
from tests.flow_event_helpers import events_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

# 旅程里应当出现的时间线事件（按发生顺序；驳回前后各填一次实付）
_JOURNEY_EVENTS = (
    LineEventType.REVIEW_APPROVED,
    LineEventType.SETTLEMENT_CREATED,
    LineEventType.SETTLEMENT_PAYMENT_FILLED,
    LineEventType.SETTLEMENT_REJECTED,
    LineEventType.SETTLEMENT_RESUBMITTED,
    LineEventType.SETTLEMENT_PAYMENT_FILLED,
    LineEventType.SETTLEMENT_PAID,
    LineEventType.PROMOTION_SETTLED,
    LineEventType.PROMOTION_METRICS_RECORDED,
)


class _FakeS3:
    """只替掉要联网的三个调用，记下传了 / 删了哪些 key。"""

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


async def _call(
    session: AsyncSession,
    user: Any,
    method: str,
    path: str,
    json: Any = None,
    *,
    data: Any = None,
    files: Any = None,
) -> Any:
    """真实路由 + 用户的有效权限（同 ``test_settlement_routing._call``，多一个表单 ``data``）。"""
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
            return await c.request(method, path, json=json, data=data, files=files)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


def _png(name: str) -> dict[str, Any]:
    return {"file": (name, _PNG, "image/png")}


async def _promotion_row(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT settlement_status, settled_at, settlement_chat_attachment_id, "
                "metrics_recorded_at, like_count, retro_status "
                "FROM promotion WHERE id = CAST(:p AS uuid)"
            ),
            {"p": promotion_id},
        )
    ).one()
    return dict(row._mapping)


async def _settlement_rows(session: AsyncSession, promotion_id: UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, settlement_status, total_amount, payment_amount, "
                "payment_adjust_note, payment_date, payment_proof_attachment_id "
                "FROM settlement WHERE promotion_id = CAST(:p AS uuid)"
            ),
            {"p": promotion_id},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


class TestE2EReviewToSettled:
    async def test_full_journey(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        cross_unit_event_bus: Any,
        fake_r2: _FakeS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=flow_users.pr,
                publish_status="已发布",
                settlement_status="待核查",
                cooperation_mode="送拍",
                quote_amount=Decimal("500.00"),
                # 应付 > 0 审核通过要收款码（R2 ⑤）
                payment_qr=True,
                # 满 7 天：录数据只剩「已结款」一道门槛
                actual_publish_date=get_today() - timedelta(days=10),
            )
            pid, line_id = promotion.id, promotion.line_id
            base = f"/api/promotions/{pid}"

            # 1. 推广审核：PR 没有审核权（独立一级域，PR 的 promotion.*:* 命中不了）
            resp = await _call(
                session, flow_users.pr, "POST", f"{base}/review", {"action": "approve"}
            )
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
            assert (await _promotion_row(session, pid))["settlement_status"] == "待核查"

            # 主管通过 → R2：推广单待付款，结款单直接建在待付款（F1，不再经过核查）
            resp = await _call(
                session, flow_users.pr_manager, "POST", f"{base}/review", {"action": "approve"}
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert (body["settlement_status"], body["review_outcome"]) == (
                "待付款",
                "settlement_created",
            )
            [settlement] = await _settlement_rows(session, pid)
            sid = settlement["id"]
            assert settlement["settlement_status"] == "待付款"
            assert settlement["total_amount"] == Decimal("500.00")
            settle = f"/api/settlements/{sid}"

            # 2. 填实付（F3）：PR 没有结款单 scope；实付 ≠ 应付不写说明 → 422；写了 → 待财务付款
            amount_path = f"{settle}/payment-amount"
            resp = await _call(
                session, flow_users.pr, "PUT", amount_path, {"payment_amount": "480.00"}
            )
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
            resp = await _call(
                session, flow_users.finance, "PUT", amount_path, {"payment_amount": "480.00"}
            )
            assert (resp.status_code, resp.json()["code"]) == (422, "FLOW_GATE_MISSING")
            assert (await _settlement_rows(session, pid))[0]["settlement_status"] == "待付款"
            resp = await _call(
                session,
                flow_users.finance,
                "PUT",
                amount_path,
                {"payment_amount": "450.00", "payment_adjust_note": "先按 450 记"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "待财务付款"

            # 3. 付款前发现收款码不对，财务驳回（F5 → R6）：两张表同事务，推广单 结款驳回
            resp = await _call(
                session,
                flow_users.finance,
                "PUT",
                f"{settle}/review",
                {"action": "reject", "review_reason": "收款码不对"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "已驳回"
            assert (await _promotion_row(session, pid))["settlement_status"] == "结款驳回"

            # 4. PR 结款重提（R7 → F6）：两边回到待付款，上一轮的实付与说明清空（D6）
            resp = await _call(
                session,
                flow_users.pr,
                "POST",
                f"{base}/settlement/resubmit",
                {"note": "换好收款码了"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "待付款"
            [settlement] = await _settlement_rows(session, pid)
            assert (
                settlement["settlement_status"],
                settlement["payment_amount"],
                settlement["payment_adjust_note"],
            ) == ("待付款", None, None)

            # 财务重新填实付 + 说明 → 待财务付款
            resp = await _call(
                session,
                flow_users.finance,
                "PUT",
                amount_path,
                {"payment_amount": "480.00", "payment_adjust_note": "博主少发一张图，扣 20 元"},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "待财务付款"
            # 5. 财务传付款凭证（F4）：客户端日期忽略，付款日期 = 北京时间当天 → R8 推广单已付款
            resp = await _call(
                session,
                flow_users.finance,
                "POST",
                f"{settle}/payment-proof/upload",
                data={"payment_date": (get_today() - timedelta(days=3)).isoformat()},
                files=_png("proof.png"),
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "已付款"
            [settlement] = await _settlement_rows(session, pid)
            assert settlement["settlement_status"] == "已付款"
            assert settlement["payment_amount"] == Decimal("480.00")
            assert settlement["payment_adjust_note"] == "博主少发一张图，扣 20 元"
            assert settlement["payment_date"] == get_today()
            assert settlement["payment_proof_attachment_id"] is not None
            promo = await _promotion_row(session, pid)
            assert (promo["settlement_status"], promo["settled_at"]) == ("已付款", None)

            # 6. 已付款还没结款：录 7 天数据被结款门槛拦住
            metrics_form = {"like_count": "120", "collect_count": "30", "comment_count": "8"}
            resp = await _call(
                session,
                flow_users.pr,
                "POST",
                f"{base}/metrics",
                data=metrics_form,
                files={"screenshot": ("m.png", _PNG, "image/png")},
            )
            assert (resp.status_code, resp.json()["code"]) == (422, "METRICS_NOT_AVAILABLE_YET")
            assert (await _promotion_row(session, pid))["metrics_recorded_at"] is None

            # 7. PR 传结款聊天截图（R9）→ 已结款，写截图与 settled_at
            resp = await _call(
                session, flow_users.pr, "POST", f"{base}/settlement-chat", files=_png("chat.png")
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["settlement_status"] == "已结款"
            promo = await _promotion_row(session, pid)
            assert promo["settlement_status"] == "已结款"
            assert promo["settled_at"] is not None
            assert promo["settlement_chat_attachment_id"] is not None

            # 8. 录 7 天数据：已结款且满 7 天 → 成功；过渡期复盘 未开始 → 待复盘（PR-7 改）
            resp = await _call(
                session,
                flow_users.pr,
                "POST",
                f"{base}/metrics",
                data=metrics_form,
                files={"screenshot": ("m.png", _PNG, "image/png")},
            )
            assert resp.status_code == 200, resp.text
            promo = await _promotion_row(session, pid)
            assert promo["metrics_recorded_at"] is not None
            assert (promo["like_count"], promo["retro_status"]) == (120, "待复盘")

            # 上传三次（凭证、聊天截图、7 天数据截图），都没有补偿删除
            assert len(fake_r2.puts) == 3 and fake_r2.deletes == []

            # 9. 时间线：旅程每一步各一条，顺序与动作一致
            journey = {t.value for t in _JOURNEY_EVENTS}
            events = [
                e for e in await events_of(session, line_id=line_id) if e.event_type in journey
            ]
            assert [e.event_type for e in events] == [t.value for t in _JOURNEY_EVENTS]
            actors = {e.event_type: e.actor_id for e in events}
            assert actors[LineEventType.REVIEW_APPROVED.value] == flow_users.pr_manager.id
            assert actors[LineEventType.SETTLEMENT_REJECTED.value] == flow_users.finance.id
            assert actors[LineEventType.SETTLEMENT_RESUBMITTED.value] == flow_users.pr.id
            assert actors[LineEventType.SETTLEMENT_PAID.value] == flow_users.finance.id
            assert actors[LineEventType.PROMOTION_SETTLED.value] == flow_users.pr.id

            # 10. 重放 SettlementPaid（以后的兜底同步同口径）只推待付款，不把已结款打回已付款
            await dispatch(
                SettlementPaid(
                    event_id=uuid4(),
                    timestamp=datetime.now(UTC),
                    tenant_id=tenant_a.id,
                    settlement_id=sid,
                    promotion_id=pid,
                    payment_amount=Decimal("480.00"),
                    payment_date=get_today(),
                    paid_by=flow_users.finance.id,
                ),
                session=session,
            )
            assert (await _promotion_row(session, pid))["settlement_status"] == "已结款"
        finally:
            tenant_id_ctx.reset(token)
