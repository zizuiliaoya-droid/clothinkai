"""PR9快照、整组修改与主平台报表隔离。"""

from datetime import UTC, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.promotion.exceptions import FieldPermissionDenied, PromotionCompletedReadonlyError
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import (
    PlatformPostIn,
    PromotionCreate,
    PromotionListFilters,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def promotion(product_factory, blogger_factory, promotion_factory, flow_users, **kw):
    return await promotion_factory.promotion(
        style=await product_factory.style(),
        blogger=await blogger_factory.blogger(),
        pr=flow_users.pr,
        internal_code=f"F9{uuid4().hex}",
        publish_status="已发布",
        cooperation_date=get_today(),
        actual_publish_date=get_today() - timedelta(days=8),
        quote_amount=Decimal("500"),
        like_count=123,
        **kw,
    )


async def test_created_snapshot_is_independent(
    session, flow_users, product_factory, blogger_factory
):
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    blogger.content_category = "合集"
    await session.flush()
    svc = PromotionService(session)
    created = await svc.create_promotion(
        PromotionCreate(
            style_id=style.id, blogger_id=blogger.id, platform="小红书", cooperation_mode="寄拍"
        ),
        flow_users.pr_manager,
    )
    assert created.content_category == "合集", "FLOW9_FAULT_EXPECTED:snapshot"
    blogger.content_category = "单篇"
    await session.flush()
    assert (await svc.get_promotion(created.id, flow_users.pr_manager)).content_category == "合集"
    changed = await svc.update_promotion(
        created.id, PromotionUpdate(content_category="历史分类"), flow_users.pr_manager
    )
    assert changed.content_category == "历史分类"
    cleared = await svc.update_promotion(
        created.id, PromotionUpdate(content_category=None), flow_users.pr_manager
    )
    assert cleared.content_category is None


async def test_platform_replace_is_atomic_and_primary_remains_authoritative(
    session, tenant_a, flow_users, product_factory, blogger_factory, promotion_factory
):
    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    from tests.integration.test_production_report_mapping import _goods, _link_style

    gid = await _goods(session, tenant_a.id, code=f"F9-{uuid4().hex[:12]}")
    await _link_style(session, tenant_a.id, gid, p.style_id)
    p.goods_main_id = gid
    p.content_category = "单篇"
    await session.flush()
    svc = PromotionService(session)
    before = await svc.get_promotion(p.id, flow_users.pr)
    from app.modules.report.domain import resolve_time_range
    from app.modules.report.pr_scope import PR_SCOPE_ALL
    from app.modules.report.production_service import ProductionService
    from app.modules.report.service import PublishProgressService

    progress = PublishProgressService(session)
    tr = resolve_time_range("last_30d")
    report_before = await progress.get_summary(tenant_a.id, tr, pr_scope=PR_SCOPE_ALL)
    assert (
        report_before.publish_count > 0
        and report_before.like_count > 0
        and report_before.cooperation_amount > 0
    )
    production = ProductionService(session)
    production_before = await production.get_report(
        tenant_a.id, (get_today(), get_today()), use_summary=False
    )
    assert any(row.promo_cost > 0 for row in production_before.items)
    row = PlatformPostIn(platform="抖音", publish_url="https://example.com/post", like_count=999999)
    after = await svc.replace_platform_posts(p.id, [row], flow_users.pr)
    assert after.extra_platforms == ["抖音"] and after.platform_posts[0].like_count == 999999
    assert (await progress.get_summary(tenant_a.id, tr, pr_scope=PR_SCOPE_ALL)).model_dump(
        mode="json"
    ) == report_before.model_dump(mode="json"), "FLOW9_FAULT_EXPECTED:primary_report"
    assert (
        await production.get_report(tenant_a.id, (get_today(), get_today()), use_summary=False)
    ).model_dump(mode="json") == production_before.model_dump(mode="json")
    for key in (
        "like_count",
        "effective_like_count",
        "is_hit",
        "cpl",
        "total_promo_cost",
        "publish_status",
        "settlement_status",
    ):
        assert after.model_dump(mode="json")[key] == before.model_dump(mode="json")[key], key
    assert before.like_count == 123 and before.total_promo_cost != 0
    for rows in (
        [PlatformPostIn(platform=p.platform)],
        [row, row],
        [PlatformPostIn(platform=f"平台{i}") for i in range(6)],
    ):
        with pytest.raises(ValidationError):
            await svc.replace_platform_posts(p.id, rows, flow_users.pr)
    with pytest.raises(ValidationError):
        await svc.update_promotion(p.id, PromotionUpdate(platform="抖音"), flow_users.admin)
    assert (await svc.get_promotion(p.id, flow_users.pr)).extra_platforms == ["抖音"]
    with pytest.raises(FieldPermissionDenied):
        await svc.replace_platform_posts(p.id, [], flow_users.operations)
    page = await svc.list_promotions(
        filters=PromotionListFilters(content_category="不存在"),
        page=1,
        page_size=20,
        user=flow_users.pr,
    )
    assert page.total == 0
    matched = await svc.list_promotions(
        filters=PromotionListFilters(content_category="单篇"),
        page=1,
        page_size=20,
        user=flow_users.pr,
    )
    assert matched.total == 1 and matched.items[0].id == p.id
    assert matched.items[0].extra_platforms == ["抖音"]
    assert matched.items[0].platform_posts == []
    assert not (await svc.replace_platform_posts(p.id, [], flow_users.pr)).platform_posts
    count = (
        await session.execute(
            text(
                "SELECT count(*) FROM line_event WHERE promotion_id=:pid AND payload->'diff' ? 'platform_posts'"
            ),
            {"pid": p.id},
        )
    ).scalar_one()
    assert count == 2


async def test_completed_posts_only_admin_can_edit(
    session, flow_users, product_factory, blogger_factory, promotion_factory
):
    from datetime import datetime

    p = await promotion(
        product_factory,
        blogger_factory,
        promotion_factory,
        flow_users,
        completed_at=datetime.now(UTC),
    )
    svc = PromotionService(session)
    for user in (flow_users.pr, flow_users.pr_manager):
        with pytest.raises(PromotionCompletedReadonlyError):
            await svc.replace_platform_posts(p.id, [PlatformPostIn(platform="抖音")], user)
    result = await svc.replace_platform_posts(
        p.id, [PlatformPostIn(platform="抖音")], flow_users.admin
    )
    assert result.extra_platforms == ["抖音"]


@pytest.mark.parametrize("published", [True, False])
async def test_recalling_platforms_detail_and_put_follow_publish_state(
    session, flow_users, product_factory, blogger_factory, promotion_factory, published
):
    from tests.integration.test_promotion_shipping import _call

    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    svc = PromotionService(session)
    await svc.replace_platform_posts(p.id, [PlatformPostIn(platform="抖音")], flow_users.pr)
    p.recall_status = "召回中"
    p.publish_status = "已发布" if published else "已取消"
    await session.commit()
    path = f"/api/promotions/{p.id}"
    for role in ("pr", "pr_manager", "admin", "operations"):
        user = getattr(flow_users, role)
        detail = await _call(session, user, "GET", path, None)
        assert detail.status_code == 200, detail.text
        assert detail.json()["extra_platforms"] == (["抖音"] if published else [])
        assert len(detail.json()["platform_posts"]) == int(published)
        changed = await _call(
            session,
            user,
            "PUT",
            path + "/platform-posts",
            [{"platform": "抖音", "like_count": 9}],
        )
        assert changed.status_code == (
            200 if published and role != "operations" else 403
        ), changed.text


async def test_maximum_platform_rows_create_and_update_keep_full_urls(
    session, flow_users, product_factory, blogger_factory, promotion_factory
):
    from app.modules.flow.line_events import PAYLOAD_MAX_BYTES, payload_size
    from tests.integration.test_promotion_shipping import _call

    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    path = f"/api/promotions/{p.id}/platform-posts"
    url = "https://example.com/" + "中" * (512 - len("https://example.com/"))
    rows = [
        {
            "platform": f"{i}" + "平台" * 7 + "甲",
            "publish_url": url,
            "actual_publish_date": "2026-10-20",
            "like_count": 2147483647,
        }
        for i in range(5)
    ]
    for likes in (2147483647, 2147483646):
        rows[0]["like_count"] = likes
        result = await _call(session, flow_users.pr, "PUT", path, rows)
        assert result.status_code == 200, result.text
        assert len(result.json()["platform_posts"]) == 5
        assert all(row["publish_url"] == url for row in result.json()["platform_posts"])
        assert result.json()["platform_posts"][0]["like_count"] == likes
    payloads = (
        (
            await session.execute(
                text(
                    "SELECT payload FROM line_event WHERE promotion_id=:pid AND event_type='promotion.updated'"
                ),
                {"pid": p.id},
            )
        )
        .scalars()
        .all()
    )
    assert len(payloads) == 2
    assert all(payload_size(payload) <= PAYLOAD_MAX_BYTES for payload in payloads)
    assert all(payload == {"changed_fields": ["platform_posts"]} for payload in payloads)


@pytest.mark.parametrize(
    "category, expected, custom",
    [("单篇", "单篇", None), ("", "合集", None), ("单篇", "合集", False), ("单篇", "单篇", True)],
    ids=["mapped", "fallback", "custom_without", "custom_with"],
)
async def test_import_category_uses_mapped_value_or_blogger(
    session, tenant_a, flow_users, product_factory, blogger_factory, category, expected, custom
):
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    blogger.content_category = "合集"
    await session.flush()
    adapter = PromotionImportAdapter()
    from types import SimpleNamespace

    mapping = None
    if custom is not None:
        columns = [
            {"source_col": source, "target_field": target, "type": kind}
            for source, target, kind in (
                ("款式编码", "style_code", "str"),
                ("小红书ID", "xiaohongshu_id", "str"),
                ("报价金额", "quote_amount", "decimal"),
                ("合作日期", "cooperation_date", "date"),
            )
        ]
        if custom:
            columns.append(
                {"source_col": "合作形式", "target_field": "content_category", "type": "str"}
            )
        mapping = SimpleNamespace(mapping_config={"columns": columns})
    parsed = adapter.parse_row(
        {
            "款式编码": style.style_code,
            "小红书ID": blogger.xiaohongshu_id,
            "合作形式": category,
            "报价金额": "1,234.50",
            "合作日期": "2026-10-20",
        },
        mapping,
    )
    assert not adapter.validate(parsed)
    pid, inserted = await adapter.upsert(
        parsed, session=session, tenant_id=tenant_a.id, actor_id=flow_users.pr_manager.id
    )
    imported = await session.scalar(select(Promotion).where(Promotion.id == pid))
    assert (
        inserted and imported.content_category == expected
    ), "FLOW9_FAULT_EXPECTED:import_category"
    assert imported.ship_status is None and imported.source_extra == {}
