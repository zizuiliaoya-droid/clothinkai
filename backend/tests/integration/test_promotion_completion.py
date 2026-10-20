"""完结五个触发点、幂等、冻结和真实HTTP只读门。"""

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    PromotionReviewRequest,
    RetrospectiveWriteRequest,
)
from app.modules.promotion.service import PromotionService
from tests.flow_event_helpers import events_of
from tests.integration.test_metrics_gate import _SHOT, _FakeS3
from tests.integration.test_settlement_routing import _call, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def completion_context(tenant_a: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.attachment import attachment_service

    monkeypatch.setattr(attachment_service, "_client", _FakeS3())
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.mark.parametrize("last", ["review", "chat", "metrics", "retro", "recall"])
async def test_every_last_step_completes_once(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    last: str,
) -> None:
    kw: dict[str, Any] = {
        "settlement_status": "已结款",
        "retro_status": "已完成",
        "metrics_recorded_at": datetime.now(UTC),
        "quote_amount": 0,
    }
    if last == "review":
        kw["settlement_status"] = "待核查"
    elif last == "chat":
        kw["settlement_status"] = "已付款"
    elif last == "metrics":
        kw["metrics_recorded_at"] = None
    elif last == "retro":
        kw["retro_status"] = "待复盘"
    else:
        kw.update(
            settlement_status="未核查",
            retro_status="未开始",
            metrics_recorded_at=None,
            recall_status="召回中",
        )
    promo = await _pending_review(
        flow_users, product_factory, blogger_factory, promotion_factory, **kw
    )
    if last == "recall":
        promo.publish_status = "已取消"
        await session.flush()
    pid, line_id = promo.id, promo.line_id
    assert promo.completed_at is None
    svc = PromotionService(session)
    if last == "review":
        await svc.review(pid, PromotionReviewRequest(action="approve"), flow_users.pr_manager)
    elif last == "chat":
        await svc.upload_settlement_chat(
            pid, user=flow_users.pr, filename=_SHOT[0], mime_type=_SHOT[1], data=_SHOT[2]
        )
    elif last == "metrics":
        await svc.record_metrics(
            pid,
            PromotionMetricsRequest(like_count=17, collect_count=2, comment_count=3),
            flow_users.pr,
            screenshot=_SHOT,
        )
    elif last == "retro":
        await svc.write_retrospective(
            pid, RetrospectiveWriteRequest(content="最后一步复盘"), flow_users.pr_manager
        )
    else:
        await svc.recall_success(pid, flow_users.pr)
    await session.refresh(promo)
    completed_at = promo.completed_at
    assert completed_at is not None, "FLOW7_FAULT_EXPECTED:completion_missing"
    await svc.maybe_complete(pid, flow_users.admin)
    await svc.maybe_complete(pid, flow_users.admin)
    await session.refresh(promo)
    assert promo.completed_at == completed_at, "FLOW7_FAULT_EXPECTED:completion_repeated"
    events = await events_of(session, line_id=line_id)
    assert sum(e.event_type == LineEventType.PROMOTION_COMPLETED.value for e in events) == 1
    completed_events = [
        e for e in events if e.event_type == LineEventType.PROMOTION_COMPLETED.value
    ]
    assert completed_events[0].payload["completion_kind"] == (
        "召回" if last == "recall" else "正常"
    )


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("PATCH", "", {"remark": "越过只读"}),
        ("DELETE", "", None),
        (
            "POST",
            "/publish",
            {"publish_url": "https://example.test/p", "actual_publish_date": "2026-01-01"},
        ),
        ("POST", "/cancel", {"cancel_reason": "只读"}),
        ("POST", "/recall/start", {}),
        ("POST", "/recall/success", {}),
        ("POST", "/recall/failure", {}),
        ("POST", "/retrospective", {"content": "已完结"}),
        ("POST", "/review", {"action": "approve"}),
        ("POST", "/resubmit", {"note": "只读"}),
    ],
)
async def test_completed_http_writes_are_409(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    method: str,
    suffix: str,
    body: Any,
) -> None:
    promo = await _pending_review(flow_users, product_factory, blogger_factory, promotion_factory)
    promo.completed_at = datetime.now(UTC)
    await session.flush()
    resp = await _call(
        session, flow_users.pr_manager, method, f"/api/promotions/{promo.id}{suffix}", body
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "PROMOTION_COMPLETED_READONLY"


async def test_completed_admin_edit_does_not_reopen_and_collector_freezes(
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
        settlement_status="已结款",
        retro_status="已完成",
        metrics_recorded_at=datetime.now(UTC),
    )
    svc = PromotionService(session)
    await svc.maybe_complete(promo.id, flow_users.admin)
    timestamp = promo.completed_at
    assert timestamp is not None
    response = await _call(
        session, flow_users.admin, "PATCH", f"/api/promotions/{promo.id}", {"remark": "补充说明"}
    )
    assert response.status_code == 200, response.text
    await session.refresh(promo)
    assert promo.completed_at == timestamp and promo.remark == "补充说明"
    before = promo.like_count
    blocked = False
    try:
        await svc.update_like_count(
            promotion_id=promo.id, tenant_id=promo.tenant_id, like_count=999
        )
    except AppException as exc:
        assert exc.code == "PROMOTION_NOT_FOUND"
        blocked = True
    assert blocked, "FLOW7_FAULT_EXPECTED:collector"
    await session.refresh(promo)
    assert promo.like_count == before


async def test_no_cooperation_is_derived_without_completion(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    promo = await _pending_review(flow_users, product_factory, blogger_factory, promotion_factory)
    promo.publish_status = "已取消"
    await session.flush()
    await PromotionService(session).maybe_complete(promo.id, flow_users.admin)
    assert promo.completed_at is None
    assert (await PromotionService(session)._compute_stage(promo)) == "已完结 · 不合作"


@pytest.mark.parametrize("suffix", ["/payment-qr/upload", "/brand-comment", "/settlement-chat"])
async def test_completed_uploads_are_409_before_storage(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    suffix: str,
) -> None:
    promo = await _pending_review(
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        completed_at=datetime.now(UTC),
    )
    resp = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{promo.id}{suffix}",
        files={"file": ("data.png", _SHOT[2], "image/png")},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "PROMOTION_COMPLETED_READONLY"


async def test_completed_import_rejects_real_export_row_even_for_admin(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    from app.modules.importer.adapters.settlement import SettlementImportAdapter
    from app.modules.importer.exceptions import RowValidationError

    promo = await _pending_review(
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        settlement_status="未核查",
        completed_at=datetime.now(UTC),
    )
    adapter = SettlementImportAdapter()
    parsed = adapter.parse_row(
        {
            "推广编号": promo.internal_code,
            "结算日期": "2026-10-01",
            "金额": "1,200.00",
            "总金额": "1,200.00",
            "付款金额": "1,200.00",
            "付款日期": "2026-10-01",
            "结算状态": "已付款",
        },
        None,
    )
    assert adapter.validate(parsed) == []
    blocked = False
    try:
        await adapter.upsert(
            parsed, session=session, tenant_id=promo.tenant_id, actor_id=flow_users.admin.id
        )
    except RowValidationError as exc:
        assert "已完结" in str(exc)
        blocked = True
    assert blocked, "FLOW7_FAULT_EXPECTED:import"


async def test_admin_role_without_true_star_cannot_edit_completed(
    session: AsyncSession,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    from sqlalchemy import select

    from app.modules.auth.models import Permission, UserPermissionOverride

    permissions = (
        (
            await session.execute(
                select(Permission).where(Permission.scope.in_(["*", "promotion.*:*"]))
            )
        )
        .scalars()
        .all()
    )
    assert {p.scope for p in permissions} == {"*", "promotion.*:*"}
    for permission in permissions:
        session.add(
            UserPermissionOverride(
                tenant_id=flow_users.tenant.id,
                user_id=flow_users.admin.id,
                permission_id=permission.id,
                effect="revoke" if permission.scope == "*" else "grant",
            )
        )
    await session.flush()
    promo = await _pending_review(
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        completed_at=datetime.now(UTC),
    )
    resp = await _call(
        session,
        flow_users.admin,
        "PATCH",
        f"/api/promotions/{promo.id}",
        {"remark": "角色名不是特权"},
    )
    assert resp.status_code == 409, resp.text
