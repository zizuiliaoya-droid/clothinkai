"""§7.3精确例外的真实HTTP证据；D10/11/14/15/16故障注入必须命中业务断言。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.freight_schemas import FreightSubmitRequest
from app.modules.promotion.freight_service import ReturnFreightService
from tests.integration import test_return_freight as freight_fixtures
from tests.integration import test_settlement_proof_upload as storage_fixtures
from tests.integration.test_settlement_routing import _call

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
freight_promotion = freight_fixtures.freight_promotion
freight_tenant = freight_fixtures.freight_tenant
r2 = storage_fixtures.r2
_PNG = storage_fixtures._PNG


async def test_zero_service_due_recall_can_upload_first_qr(
    session, freight_promotion, flow_users, r2
):
    p = freight_promotion
    p.publish_status, p.recall_status, p.settlement_status = "已发布", "召回中", "已结款"
    p.quote_amount, p.payment_qr_attachment_id = Decimal("0"), None
    await session.flush()
    uploaded = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/payment-qr/upload",
        files={"file": ("qr.png", _PNG, "image/png")},
    )
    assert uploaded.status_code == 200, "FLOW8_FAULT_EXPECTED:zero_due_recall_qr"
    succeeded = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/success",
        {"return_shipping_fee": "30", "return_shipping_payer": "博主垫付"},
    )
    assert succeeded.status_code == 200, succeeded.text
    await session.refresh(p)
    assert p.completed_at is None
    f = await session.scalar(
        select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == p.id)
    )
    assert (
        f is not None
        and f.status == "待审核"
        and f.payment_qr_attachment_id == p.payment_qr_attachment_id
    )


@pytest.mark.parametrize(
    "completed", [False, True], ids=["published_uncompleted", "cancelled_completed"]
)
async def test_rejected_freight_qr_replacement_and_resubmit(
    session, freight_promotion, flow_users, r2, completed
):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    old_qr = f.payment_qr_attachment_id
    p.publish_status = "已取消" if completed else "已发布"
    p.settlement_status = "已结款"
    p.completed_at = datetime.now(UTC) if completed else None
    f.status = "已驳回"
    await session.flush()
    original_completion = p.completed_at
    marker = (
        "FLOW8_FAULT_EXPECTED:cancelled_completed_qr"
        if completed
        else "FLOW8_FAULT_EXPECTED:uncompleted_rejected_qr"
    )
    uploaded = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/payment-qr/upload",
        files={"file": ("new-qr.png", _PNG, "image/png")},
    )
    assert uploaded.status_code == 200, marker
    await session.refresh(p)
    assert p.payment_qr_attachment_id != old_qr
    await session.refresh(f)
    assert f.payment_qr_attachment_id == old_qr
    result = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/return-freight-payments/{f.id}/resubmit",
        {"note": "已更换收款码", "amount": "35"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["payment_qr_attachment_id"] == str(p.payment_qr_attachment_id)
    await session.refresh(p)
    assert p.completed_at == original_completion and p.return_shipping_fee == Decimal("35")


async def test_completed_rejected_resubmit_http(session, freight_promotion, flow_users):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    p.completed_at, f.status = datetime.now(UTC), "已驳回"
    await session.flush()
    completed = p.completed_at
    result = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/return-freight-payments/{f.id}/resubmit",
        {"note": "核实运费", "amount": "35"},
    )
    assert result.status_code == 200, "FLOW8_FAULT_EXPECTED:completed_resubmit"
    await session.refresh(p)
    assert p.return_shipping_fee == Decimal("35") and p.completed_at == completed


async def test_pending_freight_fee_patch_forbidden_http(session, freight_promotion, flow_users):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    f.status = "待付款"
    await session.flush()
    result = await _call(
        session, flow_users.admin, "PATCH", f"/api/promotions/{p.id}", {"return_shipping_fee": "35"}
    )
    assert result.status_code == 409, "FLOW8_FAULT_EXPECTED:pending_fee_lock"
    assert result.json()["code"] == "FREIGHT_STATE_CONFLICT"
    await session.refresh(p)
    assert p.return_shipping_fee == f.amount == Decimal("30")


@pytest.mark.parametrize("op", ["upload-init", "upload", "bind", "delete"])
async def test_rejected_recall_never_bypasses_service_pending_lock(
    session, freight_promotion, flow_users, settlement_factory, product_factory, blogger_factory, op
):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    f.status, p.recall_status, p.publish_status = "已驳回", "召回中", "已发布"
    style, blogger = await product_factory.style(), await blogger_factory.blogger()
    await settlement_factory.settlement(
        style=style, blogger=blogger, promotion=p, pr=flow_users.pr, settlement_status="待财务付款"
    )
    await session.flush()
    base = f"/api/promotions/{p.id}/payment-qr"
    if op == "upload":
        response = await _call(
            session,
            flow_users.pr,
            "POST",
            base + "/upload",
            files={"file": ("qr.png", _PNG, "image/png")},
        )
    elif op == "upload-init":
        response = await _call(
            session,
            flow_users.pr,
            "POST",
            base + "/upload-init",
            {"filename": "qr.png", "mime_type": "image/png", "size_bytes": len(_PNG)},
        )
    elif op == "bind":
        response = await _call(
            session,
            flow_users.pr,
            "PUT",
            base,
            {"payment_qr_attachment_id": str(p.payment_qr_attachment_id)},
        )
    else:
        response = await _call(session, flow_users.pr, "DELETE", base)
    assert response.status_code == 409 and response.json()["code"] == "PAYMENT_QR_LOCKED"
    assert response.json()["details"]["lock"] == "settlement_paying"


@pytest.mark.parametrize("via", ["patch", "delete"])
@pytest.mark.parametrize("status", ["待审核", "待付款", "已驳回", "已付款", None])
async def test_unpaid_freight_prevents_both_deactivation_entries_even_star(
    session, freight_promotion, flow_users, attachment_factory, via, status
):
    from app.modules.promotion.urge_calculator import get_today

    p = freight_promotion
    if status:
        f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
        if status == "已付款":
            proof = await attachment_factory.attachment(purpose="settlement_proof")
            f.payment_proof_attachment_id, f.payment_date = proof.id, get_today()
            f.paid_by, f.paid_at = flow_users.finance.id, datetime.now(UTC)
        # attachment_factory 会 flush；付款必填字段齐全后才能切到已付款。
        f.status = status
    await session.flush()
    response = await _call(
        session,
        flow_users.admin,
        via.upper(),
        f"/api/promotions/{p.id}",
        {"is_active": False} if via == "patch" else None,
    )
    open_freight = status in {"待审核", "待付款", "已驳回"}
    assert response.status_code == (
        403 if open_freight else 200 if via == "patch" else 204
    ), response.text
    await session.refresh(p)
    assert p.is_active is open_freight


async def test_cancelled_freight_cost_is_counted_once_in_work_but_excluded_from_roi(
    session, freight_promotion, flow_users, tenant_a
):
    from app.modules.metric_config.settings import PlatformMetricSettings
    from app.modules.product.goods_models import GoodsMain
    from app.modules.promotion.urge_calculator import UrgeThresholds, get_today
    from app.modules.report.advanced_repository import ProductionRepository, WorkProgressRepository

    p = freight_promotion
    goods = GoodsMain(
        tenant_id=p.tenant_id, goods_code="FLOW8-COST", goods_title="召回成本", is_suit=False
    )
    session.add(goods)
    await session.flush()
    p.goods_main_id, p.cooperation_date = goods.id, get_today()
    await session.flush()
    await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    await session.refresh(p)
    work = await WorkProgressRepository(session).aggregate_by_pr(
        tenant_id=tenant_a.id,
        date_from=get_today(),
        date_to=get_today(),
        today=get_today(),
        thresholds=UrgeThresholds(urge_days=10, important_days=3),
        metric_settings=PlatformMetricSettings(like_coefficients={}, hit_thresholds={}),
        by_goods=False,
    )
    own = next(row for row in work if row["pr_id"] == flow_users.pr.id)
    assert (
        own["promo_cost"]
        == p.total_promo_cost
        == p.quote_amount + (p.cost_snapshot or Decimal("0")) + Decimal("30")
    )
    roi = await ProductionRepository(session).daily_trend_by_goods(
        tenant_id=tenant_a.id, goods_id=goods.id, date_from=get_today(), date_to=get_today()
    )
    assert not roi


async def override(session, user, scope, effect):
    from app.modules.auth.models import Permission, UserPermissionOverride

    permission = await session.scalar(select(Permission).where(Permission.scope == scope))
    if permission is None:
        permission = Permission(
            scope=scope, name=scope, category="field" if scope.startswith("field.") else "function"
        )
        session.add(permission)
        await session.flush()
    existing = await session.scalar(
        select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == user.id,
            UserPermissionOverride.permission_id == permission.id,
        )
    )
    if existing is None:
        session.add(
            UserPermissionOverride(
                tenant_id=user.tenant_id,
                user_id=user.id,
                permission_id=permission.id,
                effect=effect,
            )
        )
    else:
        existing.effect = effect
    await session.flush()


async def test_real_personal_grants_and_field_revokes(session, freight_promotion, flow_users):
    user, p = flow_users.operations, freight_promotion
    denied = await _call(session, user, "POST", f"/api/promotions/{p.id}/return-freight", {})
    assert denied.status_code == 403
    await override(session, user, "promotion:write", "grant")
    await override(session, user, "field.promotion.cost_snapshot:write", "grant")
    accepted = await _call(session, user, "POST", f"/api/promotions/{p.id}/return-freight", {})
    assert accepted.status_code == 200, accepted.text
    # 个人写授权不附带金额/图片读授权；响应依然裁剪。
    assert accepted.json()["amount"] is None and accepted.json()["payment_qr_attachment_id"] is None
    f = await session.scalar(
        select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == p.id)
    )
    f.status = "已驳回"
    await override(session, user, "field.promotion.cost_snapshot:write", "revoke")
    blocked = await _call(
        session,
        user,
        "POST",
        f"/api/return-freight-payments/{f.id}/resubmit",
        {"note": "金额核实", "amount": "35"},
    )
    assert (
        blocked.status_code == 403 and blocked.json()["code"] == "FIELD_PERMISSION_DENIED"
    ), blocked.text
    await override(session, user, "promotion:write", "revoke")
    blocked = await _call(
        session, user, "POST", f"/api/return-freight-payments/{f.id}/resubmit", {"note": "金额核实"}
    )
    assert blocked.status_code == 403 and blocked.json()["code"] == "PERMISSION_DENIED"


async def test_admin_role_name_without_star_cannot_supplement_completed(
    session, freight_promotion, flow_users
):
    from app.modules.auth.service import AuthService

    p, user = freight_promotion, flow_users.admin
    p.completed_at = datetime.now(UTC)
    await override(session, user, "*", "revoke")
    await override(session, user, "promotion:write", "grant")
    assert "*" not in (await AuthService(session).load_effective_permissions(user.id)).scopes
    response = await _call(session, user, "POST", f"/api/promotions/{p.id}/return-freight", {})
    assert (
        response.status_code == 409 and response.json()["code"] == "PROMOTION_COMPLETED_READONLY"
    ), response.text
