"""具体账号报价、服务端快照与全部发布门槛（真实 PostgreSQL）。"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.exceptions import (
    FieldPermissionDenied,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.tenancy import support_readonly_ctx, tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.blogger.account_schemas import AccountGroupInput, AccountQuoteInput
from app.modules.blogger.account_service import AccountGroupService
from app.modules.blogger.claim_models import BloggerClaim
from app.modules.blogger.exceptions import BloggerHasReferenceError
from app.modules.blogger.models import Blogger
from app.modules.blogger.service import BloggerService
from app.modules.finance.models import Settlement
from app.modules.finance.schemas import SettlementPaymentProofRequest, SettlementReviewRequest
from app.modules.finance.service import SettlementService
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationReceiverConfirm,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.fulfillment import (
    account_posts,
    initialize_account_posts,
    require_fulfilled,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PlatformPostIn,
    PromotionReviewRequest,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.integration.test_settlement_routing import _call, _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def group(session, blogger_factory, flow_users):
    a = await blogger_factory.blogger(platform="抖音", quote=Decimal("80"))
    b = await blogger_factory.blogger(platform="抖音", quote=Decimal("90"))
    svc = AccountGroupService(session)
    view = await svc.save_group(
        AccountGroupInput(name="同一位作者", account_ids=[a.id, b.id]), flow_users.pr_manager
    )
    return svc, view.group.id, a, b


async def deny(session, tenant, user, scope):
    permission = await session.scalar(select(Permission).where(Permission.scope == scope))
    assert permission is not None
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=permission.id, effect="revoke"
        )
    )
    await session.flush()


async def test_exact_accounts_price_zero_unknown_and_exclusive_membership(
    session, flow_users, blogger_factory, tenant_b
):
    svc, gid, a, b = await group(session, blogger_factory, flow_users)
    snapshot = await svc.resolve_snapshot(a.id, [b.id, a.id, a.id], flow_users.pr)
    assert snapshot["reference_amount"] is None  # 80+90不能自动当组合价。
    await svc.save_quote(
        gid, AccountQuoteInput(account_ids=[a.id], amount=0), flow_users.pr_manager
    )
    await svc.save_quote(
        gid, AccountQuoteInput(account_ids=[b.id, a.id], amount=120), flow_users.pr_manager
    )
    assert (
        Decimal((await svc.resolve_snapshot(a.id, [a.id], flow_users.pr))["reference_amount"]) == 0
    )
    both = await svc.resolve_snapshot(a.id, [a.id, b.id], flow_users.pr)
    assert Decimal(both["reference_amount"]) == 120
    assert len(both["accounts"]) == 2
    with pytest.raises(ValidationError, match="其他关联组"):
        await svc.save_group(
            AccountGroupInput(name="不能重复关联", account_ids=[a.id]), flow_users.pr_manager
        )
    with pytest.raises(PermissionDeniedError):
        await svc.save_group(
            AccountGroupInput(name="PR不能维护", account_ids=[a.id]), flow_users.pr
        )
    other = await blogger_factory.blogger(tenant=tenant_b)
    with pytest.raises(ResourceNotFoundError):
        await svc.resolve_snapshot(other.id, None, flow_users.pr)
    await svc.save_group(
        AccountGroupInput(name="移除B", account_ids=[a.id]), flow_users.pr_manager, gid
    )
    with pytest.raises(ValidationError, match="同一有效"):
        await svc.resolve_snapshot(a.id, [a.id, b.id], flow_users.pr)
    assert len(both["accounts"]) == 2  # 已生成的快照未被回改。


async def test_each_account_claim_and_personal_amount_deny(
    session, tenant_a, flow_users, blogger_factory
):
    svc, gid, a, b = await group(session, blogger_factory, flow_users)
    tenant_a.blogger_claims_enabled_at = datetime.now(UTC)
    session.add(
        BloggerClaim(
            tenant_id=tenant_a.id,
            blogger_id=a.id,
            pr_id=flow_users.pr.id,
            method="assigned",
            operated_by=flow_users.pr_manager.id,
        )
    )
    await session.flush()
    with pytest.raises(PermissionDeniedError) as exc:
        await svc.resolve_snapshot(a.id, [a.id, b.id], flow_users.pr)
    assert exc.value.code == "BLOGGER_CLAIM_REQUIRED"
    await deny(session, tenant_a, flow_users.pr_manager, "field.blogger.quote:write")
    with pytest.raises(FieldPermissionDenied):
        await svc.save_quote(
            gid, AccountQuoteInput(account_ids=[a.id], amount=10), flow_users.pr_manager
        )
    await deny(session, tenant_a, flow_users.pr, "field.promotion.quote_amount:write")
    with pytest.raises(FieldPermissionDenied):
        await NegotiationService(session).create(
            NegotiationCreate(
                blogger_id=a.id, account_ids=[a.id], cooperation_mode="送拍", quote_amount=0
            ),
            flow_users.pr,
        )
    token = support_readonly_ctx.set(True)
    try:
        with pytest.raises(PermissionDeniedError):
            await svc.save_group(
                AccountGroupInput(name="support不写", account_ids=[a.id]), flow_users.admin, gid
            )
    finally:
        support_readonly_ctx.reset(token)


async def test_group_member_must_be_removed_before_soft_delete(
    session, flow_users, blogger_factory
):
    groups, gid, a, b = await group(session, blogger_factory, flow_users)
    bloggers = BloggerService(session)
    with pytest.raises(BloggerHasReferenceError, match="先从同人账号组移除"):
        await bloggers.soft_delete_blogger(b.id, flow_users.pr_manager)
    assert not b.is_deleted
    await bloggers.disable_blogger(b.id, flow_users.pr_manager)
    view = await groups.view(a.id, flow_users.pr_manager)
    assert set(view.group.account_ids) == {a.id, b.id}
    assert not next(row for row in view.accounts if row.id == b.id).available_for_new_cooperation
    await groups.save_group(
        AccountGroupInput(name="移除停用账号", account_ids=[a.id]), flow_users.pr_manager, gid
    )
    await bloggers.soft_delete_blogger(b.id, flow_users.pr_manager)
    assert b.is_deleted
    assert (await groups.view(a.id, flow_users.pr_manager)).group.account_ids == [a.id]


async def test_draft_server_snapshot_price_change_and_hidden_quote(
    session, tenant_a, flow_users, blogger_factory
):
    svc, gid, a, b = await group(session, blogger_factory, flow_users)
    await svc.save_quote(
        gid, AccountQuoteInput(account_ids=[a.id, b.id], amount=120), flow_users.pr_manager
    )
    negotiations = NegotiationService(session)
    created = await negotiations.create(
        NegotiationCreate.model_validate(
            {
                "blogger_id": a.id,
                "account_ids": [b.id, a.id],
                "cooperation_mode": "送拍",
                "account_snapshot": None,
            }
        ),
        flow_users.pr,
    )
    assert created.quote_amount == 120 and len(created.account_snapshot.accounts) == 2
    assert created.platform == "抖音"
    await svc.save_quote(
        gid, AccountQuoteInput(account_ids=[a.id, b.id], amount=999), flow_users.pr_manager
    )
    updated = await negotiations.update(
        created.id, NegotiationUpdate(remark="不重新定价"), flow_users.pr
    )
    assert updated.quote_amount == 120 and updated.account_snapshot.reference_amount == 120
    changed = await negotiations.update(
        created.id, NegotiationUpdate(account_ids=[a.id]), flow_users.pr
    )
    assert changed.quote_amount is None  # 组内单账号也必须命中其具体组合标价。
    await deny(session, tenant_a, flow_users.pr, "field.blogger.quote:read")
    view = await svc.view(a.id, flow_users.pr)
    assert not view.can_read_quote and all(q.amount is None for q in view.group.quotes)
    fetched = await negotiations.get(created.id, flow_users.pr)
    assert fetched.account_snapshot.reference_amount is None


async def test_approved_account_set_copied_once_without_repricing(
    session, flow_users, blogger_factory, product_factory
):
    svc, gid, a, b = await group(session, blogger_factory, flow_users)
    await svc.save_quote(
        gid, AccountQuoteInput(account_ids=[a.id, b.id], amount=120), flow_users.pr_manager
    )
    style = await product_factory.style()
    sku = await product_factory.sku(style)
    negotiations = NegotiationService(session)
    n = await negotiations.create(
        NegotiationCreate(
            blogger_id=a.id,
            account_ids=[a.id, b.id],
            style_id=style.id,
            cooperation_mode="送拍",
            scheduled_publish_date=get_today(),
            items=[GoodsItemIn(style_id=style.id, sku_id=sku.id)],
        ),
        flow_users.pr,
    )
    await negotiations.submit(n.id, flow_users.pr)
    await negotiations.review(
        n.id, NegotiationReviewRequest(action="approve"), flow_users.pr_manager
    )
    await negotiations.final_review(
        n.id, NegotiationFinalReviewRequest(action="approve"), flow_users.admin
    )
    await svc.save_group(
        AccountGroupInput(name="后续调整分组", account_ids=[a.id]), flow_users.pr_manager, gid
    )
    finalized = await negotiations.confirm_receiver(
        n.id,
        NegotiationReceiverConfirm(
            receiver_name="测试", receiver_phone="13800138000", receiver_address="杭州测试地址"
        ),
        flow_users.pr,
    )
    promotion = await session.get(Promotion, finalized.promotion_id)
    stored = await session.get(Negotiation, n.id)
    assert promotion.account_snapshot == stored.account_snapshot
    assert promotion.quote_amount == 120
    rows = await account_posts(session, promotion)
    assert len(rows) == 2 and len({row.platform for row in rows}) == 1
    assert {row.account_id for row in rows} == {a.id, b.id}
    assert sum(row.is_primary for row in rows) == 1


async def obligation_promotion(
    session, flow_users, product_factory, blogger_factory, promotion_factory, *, amount
):
    promo = await _pending_review(
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        cooperation_mode="送拍",
        cooperation_date=get_today(),
        quote_amount=amount,
        payment_qr=True,
    )
    primary = await session.get(Blogger, promo.blogger_id)
    second = await blogger_factory.blogger(platform=primary.platform)
    svc = AccountGroupService(session)
    await svc.save_group(
        AccountGroupInput(name="履约组", account_ids=[primary.id, second.id]), flow_users.pr_manager
    )
    promo.account_snapshot = await svc.resolve_snapshot(
        primary.id, [primary.id, second.id], flow_users.pr
    )
    promo.publish_url = "https://example.test/primary"
    await session.flush()
    await initialize_account_posts(session, promo, flow_users.pr.id)
    rows = await account_posts(session, promo)
    for row in rows:
        if row.is_primary:
            row.publish_url, row.actual_publish_date = promo.publish_url, promo.actual_publish_date
    await session.flush()
    return promo


@pytest.mark.usefixtures("cross_unit_event_bus")
@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("300")])
async def test_all_accounts_before_business_review_and_one_settlement(
    session, flow_users, product_factory, blogger_factory, promotion_factory, amount
):
    promo = await obligation_promotion(
        session, flow_users, product_factory, blogger_factory, promotion_factory, amount=amount
    )
    service = PromotionService(session)
    from app.modules.report.domain import resolve_time_range
    from app.modules.report.pr_scope import PR_SCOPE_ALL
    from app.modules.report.service import PublishProgressService

    reports = PublishProgressService(session)
    report_before = await reports.get_summary(
        promo.tenant_id, resolve_time_range("last_30d"), pr_scope=PR_SCOPE_ALL
    )
    assert report_before.publish_count == 1
    with pytest.raises(ValidationError) as exc:
        await service.review(
            promo.id, PromotionReviewRequest(action="approve"), flow_users.pr_manager
        )
    assert exc.value.code == "ACCOUNT_FULFILLMENT_REQUIRED"
    assert (
        await session.scalars(select(Settlement).where(Settlement.promotion_id == promo.id))
    ).all() == []
    rows = await account_posts(session, promo)
    inputs = [
        PlatformPostIn(
            account_id=row.account_id,
            platform=row.platform,
            publish_url="https://example.test/" + str(row.account_id),
            actual_publish_date=promo.actual_publish_date,
            like_count=9999,
        )
        for row in rows
    ]
    with pytest.raises(ValidationError, match="全部约定账号"):
        await service.replace_platform_posts(promo.id, inputs[:1], flow_users.pr)
    updated = await service.replace_platform_posts(promo.id, inputs, flow_users.pr)
    assert updated.fulfillment.complete and updated.fulfillment.total == 2
    approved = await service.review(
        promo.id, PromotionReviewRequest(action="approve"), flow_users.pr_manager
    )
    settlements = (
        await session.scalars(select(Settlement).where(Settlement.promotion_id == promo.id))
    ).all()
    assert len(settlements) == (1 if amount else 0)
    assert approved.settlement_status == ("待付款" if amount else "已结款")
    # 已入结款仍可更正有效事实；子项点赞不覆盖主单统计值。
    inputs[0].publish_url = "https://example.test/corrected"
    corrected = await service.replace_platform_posts(promo.id, inputs, flow_users.pr)
    assert corrected.fulfillment.complete and corrected.like_count != 19998
    report_after = await reports.get_summary(
        promo.tenant_id, resolve_time_range("last_30d"), pr_scope=PR_SCOPE_ALL
    )
    assert report_after.publish_count == 1
    assert report_after.like_count == report_before.like_count
    inputs[0].publish_url = None
    with pytest.raises(ValidationError, match="不能清空"):
        await service.replace_platform_posts(promo.id, inputs, flow_users.pr)


@pytest.mark.usefixtures("cross_unit_event_bus")
async def test_both_payment_proof_entries_recheck_fulfillment_before_upload(
    session, flow_users, product_factory, blogger_factory, promotion_factory, monkeypatch
):
    promo = await obligation_promotion(
        session,
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        amount=Decimal("300"),
    )
    rows = await account_posts(session, promo)
    for row in rows:
        row.publish_url, row.actual_publish_date = "https://example.test/ok", get_today()
    await session.flush()
    await PromotionService(session).review(
        promo.id, PromotionReviewRequest(action="approve"), flow_users.pr_manager
    )
    settlement = await session.scalar(select(Settlement).where(Settlement.promotion_id == promo.id))
    settlement.settlement_status = "待财务付款"
    rows[1].publish_url = None  # 构造既有不完整事实，验证两个入口不会信任已审核状态。
    await session.flush()
    finance = SettlementService(session)
    upload = AsyncMock()
    monkeypatch.setattr(finance._attachment_service, "create_upload_record", upload)
    with pytest.raises(ValidationError) as exc:
        await finance.upload_payment_proof(
            settlement.id,
            SettlementPaymentProofRequest(payment_proof_attachment_id=uuid4()),
            flow_users.finance,
        )
    assert exc.value.code == "ACCOUNT_FULFILLMENT_REQUIRED"
    from tests.integration.test_settlement_proof_upload import _PNG

    with pytest.raises(ValidationError) as exc:
        await finance.upload_payment_proof_file(
            settlement.id,
            filename="proof.png",
            mime_type="image/png",
            data=_PNG,
            user=flow_users.finance,
        )
    assert exc.value.code == "ACCOUNT_FULFILLMENT_REQUIRED"
    upload.assert_not_awaited()
    # 驳回不要求履约完成；它仍应能退回缺少发布事实的业务款单。
    rejected = await finance.review(
        settlement.id,
        SettlementReviewRequest(action="reject", review_reason="补齐账号发布"),
        flow_users.finance,
    )
    assert rejected.settlement_status == "已驳回"


async def test_legacy_null_and_http_cannot_downgrade(
    session, flow_users, product_factory, blogger_factory, promotion_factory
):
    old = await _pending_review(flow_users, product_factory, blogger_factory, promotion_factory)
    assert old.account_snapshot is None
    await require_fulfilled(session, old.id, old.tenant_id)
    style, blogger = await product_factory.style(), await blogger_factory.blogger()
    response = await _call(
        session,
        flow_users.pr_manager,
        "POST",
        "/api/promotions/",
        {
            "style_id": str(style.id),
            "blogger_id": str(blogger.id),
            "platform": blogger.platform,
            "cooperation_mode": "送拍",
            "account_snapshot": None,
        },
    )
    assert response.status_code == 201, response.text
    saved = await session.get(Promotion, UUID(response.json()["id"]))
    assert saved.account_snapshot is not None
    assert len(await account_posts(session, saved)) == 1
