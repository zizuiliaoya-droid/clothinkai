"""待补款草稿：保存、补款、原审批链与空值边界。真实数据库由 CI 执行。"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.exceptions import FlowGateMissingError
from app.modules.negotiation.exceptions import (
    InvalidNegotiationGoodsError,
    InvalidNegotiationStyleError,
    NegotiationNotFoundError,
)
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.schemas import (
    NegotiationCreate,
    NegotiationFinalReviewRequest,
    NegotiationListFilters,
    NegotiationReceiverConfirm,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService, _submit_snapshot
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import GoodsItemIn

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def draft(session, flow_users, blogger_factory, **fields):
    blogger = await blogger_factory.blogger(quote=None)
    return await NegotiationService(session).create(
        NegotiationCreate(blogger_id=blogger.id, cooperation_mode="寄拍", **fields), flow_users.pr
    )


async def test_empty_draft_read_update_permissions_and_no_downstream(
    session, flow_users, blogger_factory
):
    svc = NegotiationService(session)
    created = await draft(session, flow_users, blogger_factory)
    assert (created.status, created.style_id, created.goods_main_id, created.items) == (
        "草稿",
        None,
        None,
        [],
    )
    assert created.quote_amount is None
    fetched = await svc.get(created.id, flow_users.pr)
    assert fetched.style_id is None and fetched.items == []
    page = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
    assert [row.id for row in page.items] == [created.id]
    updated = await svc.update(created.id, NegotiationUpdate(remark="先记合作意向"), flow_users.pr)
    assert updated.style_id is None and updated.quote_amount is None
    with pytest.raises(NegotiationNotFoundError):
        await svc.update(created.id, NegotiationUpdate(remark="其他PR"), flow_users.pr2)
    with pytest.raises(NegotiationNotFoundError):
        await svc.get(created.id, flow_users.pr_manager)
    with pytest.raises(FlowGateMissingError) as exc:
        await svc.submit(created.id, flow_users.pr)
    assert {key for key, _ in exc.value.missing} >= {"style_id", "goods_items", "quote_amount"}
    with pytest.raises(AppException):
        await svc.review(created.id, NegotiationReviewRequest(action="approve"), flow_users.pr)
    with pytest.raises(AppException):
        await svc.final_review(
            created.id, NegotiationFinalReviewRequest(action="approve"), flow_users.pr
        )
    with pytest.raises(AppException):
        await svc.confirm_receiver(created.id, NegotiationReceiverConfirm(), flow_users.pr)
    assert (
        await session.execute(select(Promotion.id).where(Promotion.line_id == created.line_id))
    ).first() is None
    stored = await session.get(Negotiation, created.id)
    assert _submit_snapshot(stored, [])["style_id"] is None


async def test_complete_new_draft_uses_original_two_level_flow(
    session, flow_users, blogger_factory, product_factory
):
    svc = NegotiationService(session)
    created = await draft(session, flow_users, blogger_factory)
    style = await product_factory.style()
    sku = await product_factory.sku(style)
    completed = await svc.update(
        created.id,
        NegotiationUpdate(
            style_id=style.id,
            quote_amount=Decimal("0"),
            scheduled_publish_date=date(2026, 12, 20),
            items=[GoodsItemIn(style_id=style.id, sku_id=sku.id)],
        ),
        flow_users.pr,
    )
    assert completed.style_id == style.id and completed.quote_amount == 0
    assert (await svc.submit(created.id, flow_users.pr)).status == "主管待审"
    assert (
        await svc.review(
            created.id, NegotiationReviewRequest(action="approve"), flow_users.pr_manager
        )
    ).status == "待老板审核"
    assert (
        await svc.final_review(
            created.id, NegotiationFinalReviewRequest(action="approve"), flow_users.admin
        )
    ).status == "待补收货地址"
    final = await svc.confirm_receiver(
        created.id,
        NegotiationReceiverConfirm(
            receiver_name="测试收件人",
            receiver_phone="13800000000",
            receiver_address="测试收件地址",
        ),
        flow_users.pr,
    )
    assert final.status == "已定稿" and final.promotion_id is not None
    promo = await session.get(Promotion, final.promotion_id)
    assert promo.style_id == style.id and promo.quote_amount == 0


async def test_clear_and_refill_suit_members(
    session, tenant_a, flow_users, blogger_factory, product_factory
):
    svc = NegotiationService(session)
    created = await draft(session, flow_users, blogger_factory)
    first, second = await product_factory.style(), await product_factory.style()
    sku = await product_factory.sku(first)
    goods = GoodsMain(
        tenant_id=tenant_a.id,
        goods_code=f"G{uuid4().hex[:8]}",
        goods_title="测试套装",
        is_suit=True,
    )
    session.add(goods)
    await session.flush()
    session.add_all(
        [
            GoodsStyleItem(
                tenant_id=tenant_a.id, goods_main_id=goods.id, style_id=s.id, sort_order=i
            )
            for i, s in enumerate((first, second))
        ]
    )
    await session.flush()
    selected = await svc.update(
        created.id, NegotiationUpdate(style_id=first.id, goods_main_id=goods.id), flow_users.pr
    )
    assert {i.style_id for i in selected.items} == {first.id, second.id}
    kept = await svc.update(created.id, NegotiationUpdate(remark="保留选款"), flow_users.pr)
    assert kept.style_id == first.id and kept.goods_main_id == goods.id
    cleared = await svc.update(created.id, NegotiationUpdate(style_id=None), flow_users.pr)
    assert cleared.style_id is None and cleared.goods_main_id is None and cleared.items == []
    with pytest.raises(InvalidNegotiationGoodsError):
        await svc.update(created.id, NegotiationUpdate(goods_main_id=goods.id), flow_users.pr)
    with pytest.raises(InvalidNegotiationGoodsError):
        await svc.update(
            created.id,
            NegotiationUpdate(items=[GoodsItemIn(style_id=first.id, sku_id=sku.id)]),
            flow_users.pr,
        )
    filled = await svc.update(
        created.id,
        NegotiationUpdate(
            style_id=first.id,
            goods_main_id=None,
            items=[GoodsItemIn(style_id=first.id, sku_id=sku.id)],
        ),
        flow_users.pr,
    )
    assert len(filled.items) == 1 and filled.items[0].sku_id == sku.id


@pytest.mark.parametrize(
    ("mode", "provided", "profile", "expected"),
    [
        ("寄拍", None, None, None),
        ("送拍", None, None, None),
        ("寄拍", Decimal("0"), None, Decimal("0")),
        ("送拍", None, Decimal("120"), Decimal("120")),
        ("置换", Decimal("120"), None, Decimal("0")),
    ],
)
async def test_unknown_quote_and_explicit_zero(
    session, flow_users, blogger_factory, mode, provided, profile, expected
):
    blogger = await blogger_factory.blogger(quote=profile)
    svc = NegotiationService(session)
    created = await svc.create(
        NegotiationCreate(blogger_id=blogger.id, cooperation_mode=mode, quote_amount=provided),
        flow_users.pr,
    )
    assert created.quote_amount == expected
    updated = await svc.update(created.id, NegotiationUpdate(remark="只改备注"), flow_users.pr)
    assert updated.quote_amount == expected


async def test_non_draft_cannot_clear_style(
    session, flow_users, blogger_factory, product_factory, negotiation_factory
):
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    returned = await negotiation_factory.negotiation(
        style=style, blogger=blogger, pr=flow_users.pr, status="老板退回"
    )
    with pytest.raises(InvalidNegotiationStyleError):
        await NegotiationService(session).update(
            returned.id, NegotiationUpdate(style_id=None), flow_users.pr
        )


async def test_database_rejects_incomplete_non_draft(session, flow_users, blogger_factory):
    created = await draft(session, flow_users, blogger_factory)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE negotiation SET status='主管待审' WHERE id=:id"), {"id": created.id}
            )


async def test_other_tenant_blogger_cannot_be_used_for_empty_draft(
    session, flow_users, blogger_factory, tenant_b
):
    foreign = await blogger_factory.blogger(tenant=tenant_b)
    with pytest.raises(AppException):
        await NegotiationService(session).create(
            NegotiationCreate(blogger_id=foreign.id, cooperation_mode="寄拍"), flow_users.pr
        )
