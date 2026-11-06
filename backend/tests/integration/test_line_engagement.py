"""1105 本人未读、并发到达边界、分页排序与人工跟进口径。"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.core.tenancy import tenant_id_ctx
from app.modules.flow.engagement import (
    LineEngagementService,
    engagement_joins,
    engagement_params,
    needs_follow_up_sql,
)
from app.modules.flow.exceptions import LineNotFoundError
from app.modules.flow.models import LineCommentRead
from app.modules.flow.timeline import LineEventService
from app.modules.negotiation.schemas import NegotiationListFilters
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.schemas import PromotionListFilters
from app.modules.promotion.service import PromotionService
from app.modules.wecom.notification_service import NotificationService
from app.tasks.follow_up_tasks import summarize_tenant
from tests.integration.test_line_timeline import _revoke
from tests.integration.test_return_freight_payment_rls import _insert_freight

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def make_line(product_factory, blogger_factory, promotion_factory, flow_users):
    return await promotion_factory.promotion(
        style=await product_factory.style(),
        blogger=await blogger_factory.blogger(),
        pr=flow_users.pr,
        internal_code=f"READ{uuid4().hex[:12]}",
    )


@pytest.mark.usefixtures("tenant_ctx")
async def test_get_does_not_read_and_newer_comments_survive_old_page(
    session,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    timeline = LineEventService(session)
    first = await timeline.comment(promo.line_id, flow_users.pr_manager, text="先到", image=None)
    shown = await timeline.read(promo.line_id, flow_users.pr)
    assert shown.comment_state.unread_comment_count == 1
    assert (await session.execute(select(LineCommentRead))).scalars().all() == []
    second = await timeline.comment(
        promo.line_id, flow_users.pr_manager, text="打开后到达", image=None
    )
    reader = LineEngagementService(session)
    state = await reader.mark_read(promo.line_id, flow_users.pr, first.seq)
    assert state.unread_comment_count == 1
    assert await NotificationService(session).unread_count(user_id=flow_users.pr.id) == 1
    await reader.mark_read(promo.line_id, flow_users.pr, second.seq)
    state = await reader.mark_read(promo.line_id, flow_users.pr, first.seq)
    assert state.last_read_comment_seq == second.seq and state.unread_comment_count == 0
    await timeline.comment(promo.line_id, flow_users.pr, text="PR回复", image=None)
    assert (
        await timeline.read(promo.line_id, flow_users.pr)
    ).comment_state.unread_comment_count == 0
    assert (
        await timeline.read(promo.line_id, flow_users.pr_manager)
    ).comment_state.unread_comment_count == 1


@pytest.mark.usefixtures("tenant_ctx")
async def test_revoked_comment_permission_hides_notifications_and_rejects_read(
    session,
    tenant_a,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    event = await LineEventService(session).comment(
        promo.line_id, flow_users.pr_manager, text="含私密内容", image=None
    )
    notices = await NotificationService(session).list_for_user(user_id=flow_users.pr.id)
    assert notices and all(n.content == "有新留言" for n in notices)
    await _revoke(session, tenant_a, flow_users.pr, "field.line_event.comment:read")
    assert (
        await LineEventService(session).read(promo.line_id, flow_users.pr)
    ).comment_state is None
    assert await NotificationService(session).unread_count(user_id=flow_users.pr.id) == 0
    assert await NotificationService(session).list_for_user(user_id=flow_users.pr.id) == []
    with pytest.raises(LineNotFoundError):
        await LineEngagementService(session).mark_read(promo.line_id, flow_users.pr, event.seq)


@pytest.mark.usefixtures("tenant_ctx")
async def test_read_requires_same_line_comment_and_cannot_accept_future_seq(
    session,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
):
    one = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    two = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    event = await LineEventService(session).comment(
        one.line_id, flow_users.pr_manager, text="本线", image=None
    )
    svc = LineEngagementService(session)
    for line_id, seq in ((two.line_id, event.seq), (one.line_id, event.seq + 10000)):
        with pytest.raises(LineNotFoundError):
            await svc.mark_read(line_id, flow_users.pr, seq)


@pytest.mark.usefixtures("tenant_ctx")
async def test_unread_filter_pagination_and_read_does_not_change_sort(
    session,
    product_factory,
    blogger_factory,
    negotiation_factory,
    flow_users,
):
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    rows = [
        await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="主管待审"
        )
        for _ in range(3)
    ]
    timeline = LineEventService(session)
    first = await timeline.comment(
        rows[0].line_id, flow_users.pr_manager, text="第一条", image=None
    )
    second = await timeline.comment(
        rows[1].line_id, flow_users.pr_manager, text="第二条", image=None
    )
    await session.execute(
        text("UPDATE line_event SET created_at=:at WHERE seq=:seq"),
        {"seq": first.seq, "at": datetime(2026, 11, 4, tzinfo=UTC)},
    )
    await session.execute(
        text("UPDATE line_event SET created_at=:at WHERE seq=:seq"),
        {"seq": second.seq, "at": datetime(2026, 11, 5, tzinfo=UTC)},
    )
    svc = NegotiationService(session)
    filtered = await svc.list_negotiations(
        user=flow_users.pr, filters=NegotiationListFilters(only_unread_comments=True), page_size=1
    )
    assert filtered.total == 2 and len(filtered.items) == 1
    assert filtered.items[0].id == rows[1].id
    before = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
    await LineEngagementService(session).mark_read(rows[0].line_id, flow_users.pr, first.seq)
    after = await svc.list_negotiations(user=flow_users.pr, filters=NegotiationListFilters())
    assert [x.id for x in before.items] == [x.id for x in after.items]


@pytest.mark.usefixtures("tenant_ctx")
async def test_120h_beijing_date_and_ignored_automatic_updates(
    session,
    tenant_a,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)  # 北京11月6日00:00
    await session.execute(
        text("UPDATE promotion SET created_at=:created, scheduled_publish_date=:date WHERE id=:id"),
        {"id": promo.id, "created": now - timedelta(hours=120), "date": date(2026, 11, 6)},
    )

    async def due(at):
        params = {**engagement_params(None, now=at), "pid": promo.id}
        return (
            await session.execute(
                text(
                    f"SELECT {needs_follow_up_sql('p','promotion')} FROM promotion p {engagement_joins('p')} WHERE p.id=:pid"
                ),
                params,
            )
        ).scalar_one()

    assert not await due(now - timedelta(seconds=1))
    assert await due(now)
    await session.execute(
        text(
            "INSERT INTO line_event(id,tenant_id,line_id,promotion_id,event_type,actor_id) VALUES (:id,:tenant,:line,:promo,'promotion.updated',:actor)"
        ),
        {
            "id": uuid4(),
            "tenant": tenant_a.id,
            "line": promo.line_id,
            "promo": promo.id,
            "actor": flow_users.pr.id,
        },
    )
    assert await due(now)
    # 单据只有已结款仍不能隐藏后续复盘等义务。
    await session.execute(
        text("UPDATE promotion SET settlement_status='已结款' WHERE id=:id"), {"id": promo.id}
    )
    assert await due(now)
    await session.execute(
        text("UPDATE promotion SET publish_status='已取消',ship_status='待发货' WHERE id=:id"),
        {"id": promo.id},
    )
    assert not await due(now)
    await session.execute(
        text("UPDATE promotion SET ship_status='已发货' WHERE id=:id"), {"id": promo.id}
    )
    assert await due(now)
    first = await summarize_tenant(session, tenant_a.id, now)
    assert first >= 1
    assert await summarize_tenant(session, tenant_a.id, now) == 0
    await session.execute(
        text("""INSERT INTO line_event(
        id,tenant_id,line_id,promotion_id,event_type,actor_id,created_at)
        VALUES (:id,:tenant,:line,:promo,'line.comment',:actor,:at)"""),
        {
            "id": uuid4(),
            "tenant": tenant_a.id,
            "line": promo.line_id,
            "promo": promo.id,
            "actor": flow_users.pr.id,
            "at": now - timedelta(hours=119),
        },
    )
    assert not await due(now)


@pytest.mark.usefixtures("tenant_ctx")
async def test_completed_recall_keeps_unpaid_freight_visible(
    session,
    tenant_a,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
    attachment_factory,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    now = datetime(2026, 11, 5, 16, 0, tzinfo=UTC)
    await session.execute(
        text("""UPDATE promotion SET created_at=:created,
        completed_at=:created,publish_status='已取消',recall_status='召回成功',
        scheduled_publish_date=NULL,is_active=false WHERE id=:id"""),
        {"id": promo.id, "created": now - timedelta(hours=121)},
    )
    freight_id = await _insert_freight(
        session, attachment_factory, tenant=tenant_a, promotion=promo
    )
    params = {**engagement_params(None, now=now), "pid": promo.id}
    stmt = text(
        f"SELECT {needs_follow_up_sql('p','promotion')} FROM promotion p {engagement_joins('p')} WHERE p.id=:pid"
    )
    assert (await session.execute(stmt, params)).scalar_one()
    # 运费已驳回仍有可重提义务，不能静默当结束。
    await session.execute(
        text("UPDATE return_freight_payment SET status='已驳回' WHERE id=:id"), {"id": freight_id}
    )
    assert (await session.execute(stmt, params)).scalar_one()


@pytest.mark.usefixtures("tenant_ctx")
@pytest.mark.parametrize("mode", [None, "寄拍"])
async def test_cancelled_null_shipping_returns_boolean_and_list_response(
    session,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
    mode,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    await session.execute(
        text("""UPDATE promotion SET created_at=:created,
        publish_status='已取消',ship_status=NULL,recall_status='未召回',
        cooperation_mode=:mode,scheduled_publish_date=NULL WHERE id=:id"""),
        {"id": promo.id, "created": datetime.now(UTC) - timedelta(hours=121), "mode": mode},
    )
    service = PromotionService(session)
    page = await service.list_promotions(
        user=flow_users.pr, filters=PromotionListFilters(), page=1, page_size=20
    )
    item = next(row for row in page.items if row.id == promo.id)
    assert item.follow_up is not None and item.follow_up.is_due is False
    filtered = await service.list_promotions(
        user=flow_users.pr, filters=PromotionListFilters(needs_follow_up=True), page=1, page_size=20
    )
    assert filtered.total == 0


@pytest.mark.usefixtures("tenant_ctx")
async def test_disabled_loaned_sample_keeps_follow_up_and_daily_summary(
    session,
    tenant_a,
    product_factory,
    blogger_factory,
    promotion_factory,
    flow_users,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    now = datetime.now(UTC)
    await session.execute(
        text("""UPDATE promotion SET created_at=:created,
        is_active=false,publish_status='未发布',cooperation_mode='寄拍',ship_status='已发货',
        recall_status='未召回',return_waybill=NULL,scheduled_publish_date=NULL WHERE id=:id"""),
        {"id": promo.id, "created": now - timedelta(hours=121)},
    )
    service = PromotionService(session)
    page = await service.list_promotions(
        user=flow_users.pr, filters=PromotionListFilters(needs_follow_up=True), page=1, page_size=20
    )
    assert [row.id for row in page.items] == [promo.id]
    assert page.items[0].follow_up.is_due is True
    assert await summarize_tenant(session, tenant_a.id, now) >= 1
    summary = await NotificationService(session).list_for_user(user_id=flow_users.pr.id)
    assert [row.type for row in summary] == ["follow_up_summary"]
    # 用既有寄回单号事实结束这项物流提醒，不修改单据业务状态。
    await session.execute(
        text("UPDATE promotion SET return_waybill='RETURN-1105' WHERE id=:id"), {"id": promo.id}
    )
    page = await service.list_promotions(
        user=flow_users.pr, filters=PromotionListFilters(needs_follow_up=True), page=1, page_size=20
    )
    assert page.total == 0
