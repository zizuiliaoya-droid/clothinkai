"""PR10真实SQL/HTTP交集；本机无库时仅收集，交由现有PG18通道执行。"""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.exceptions import FieldPermissionDenied, PromotionCompletedReadonlyError
from app.modules.promotion.schemas import PromotionUpdate
from app.modules.promotion.service import PromotionService
from app.modules.promotion.stage_calculator import BADGES, PROMOTION_STAGES
from app.modules.promotion.urge_calculator import get_today
from tests.api.test_report_drill_api import _call

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_counts_share_filters_exclude_stage_badge_and_other_tenant(
    session, tenant_a, tenant_b, flow_users, product_factory, blogger_factory, promotion_factory
):
    tag = f"F10{uuid4().hex[:8]}"
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style, blogger = await product_factory.style(), await blogger_factory.blogger()
        common = {
            "style": style,
            "blogger": blogger,
            "pr": flow_users.pr,
            "receiver_name": "王%甲",
            "receiver_phone": "13800000000",
        }
        rows = []
        for i, attrs in enumerate(
            [
                {"ship_status": "待发货"},
                {
                    "publish_status": "已发布",
                    "actual_publish_date": get_today() - timedelta(days=7),
                    "retro_status": "待复盘",
                },
                {
                    "publish_status": "已发布",
                    "settlement_status": "已结款",
                    "actual_publish_date": get_today() - timedelta(days=7),
                    "retro_status": "待复盘",
                },
                {
                    "publish_status": "已发布",
                    "settlement_status": "已结款",
                    "completed_at": datetime.now(UTC),
                    "metrics_recorded_at": datetime.now(UTC),
                    "retro_status": "已完成",
                },
                {"is_active": False},
            ]
        ):
            rows.append(
                await promotion_factory.promotion(**common, internal_code=f"{tag}{i}", **attrs)
            )
        # 同租户、不匹配关键词/收件前缀的行也不能混入计数。
        await promotion_factory.promotion(
            **(common | {"receiver_name": "王甲"}), internal_code=f"OTHER{uuid4().hex[:8]}"
        )
        await session.flush()
        tenant_id_ctx.set(tenant_b.id)
        await promotion_factory.promotion(
            tenant=tenant_b,
            style=await product_factory.style(tenant=tenant_b),
            blogger=await blogger_factory.blogger(tenant=tenant_b),
            internal_code=f"{tag}B",
        )
        tenant_id_ctx.set(tenant_a.id)
        query = {
            "keyword": tag,
            "stage": "待录 7 天数据",
            "badge": "metrics_due",
            "receiver_name": "王%",
            "receiver_phone": "138",
        }
        counts = await _call(
            session, flow_users.admin, "/api/promotions/stage-counts?" + urlencode(query)
        )
        assert counts.status_code == 200, counts.text
        data = counts.json()
        assert data["total"] == 4 and data["completed"] == 1
        assert set(data["stages"]) == set(PROMOTION_STAGES) and sum(data["stages"].values()) == 4
        assert (
            data["stages"]["待推送仓库"]
            == data["stages"]["待主管审核"]
            == data["stages"]["待录 7 天数据"]
            == data["stages"]["已完结"]
            == 1
        )
        assert data["badges"] == dict.fromkeys(BADGES, 0) | {
            "missing_qr": 1,
            "metrics_due": 1,
            "metrics_wait_settle": 1,
            "retro_due": 2,
        }
        listed = await _call(
            session, flow_users.admin, "/api/promotions/?" + urlencode(query | {"page_size": 1})
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1 and listed.json()["items"][0]["id"] == str(rows[2].id)
        assert listed.json()["items"][0]["stage"] == "待录 7 天数据"
        done = await _call(
            session,
            flow_users.admin,
            "/api/promotions/stage-counts?" + urlencode(query | {"completed": "true"}),
        )
        assert done.json()["total"] == done.json()["completed"] == 1
        inactive = await _call(
            session,
            flow_users.admin,
            "/api/promotions/stage-counts?" + urlencode({"keyword": tag, "is_active": "false"}),
        )
        assert inactive.json()["stages"]["已停用"] == inactive.json()["total"] == 1
    finally:
        tenant_id_ctx.reset(token)


async def test_receiver_search_denied_without_field_read_and_invalid_stage_rejected(
    session, tenant_a, flow_users
):
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        for path in ("/api/promotions/", "/api/promotions/stage-counts"):
            for field in ("receiver_name", "receiver_phone"):
                response = await _call(session, flow_users.operations, f"{path}?{field}=138")
                assert response.status_code == 403, response.text
            for query in ("stage=bad", "badge=bad", "receiver_phone=" + "1" * 33):
                assert (
                    await _call(session, flow_users.admin, f"{path}?{query}")
                ).status_code == 422
    finally:
        tenant_id_ctx.reset(token)


async def test_published_recall_snapshot_patch_and_completed_lock(
    session, tenant_a, flow_users, product_factory, blogger_factory, promotion_factory
):
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        p = await promotion_factory.promotion(
            style=await product_factory.style(),
            blogger=await blogger_factory.blogger(),
            pr=flow_users.pr,
            publish_status="已发布",
            recall_status="召回中",
            settlement_status="待核查",
            ship_status="已发货",
        )
        svc = PromotionService(session)
        doc = await svc._promotion_doc(p)
        assert doc.stage == "召回中" and doc.published_stage == "待主管审核"
        result = await svc.update_promotion(
            p.id, PromotionUpdate(quote_amount=123), flow_users.pr_manager
        )
        assert result.quote_amount == 123
        p.settlement_status = "待付款"
        await session.flush()
        doc = await svc._promotion_doc(p)
        assert doc.stage == "召回中" and doc.published_stage == "待财务付款"
        with pytest.raises(FieldPermissionDenied):
            await svc.update_promotion(
                p.id, PromotionUpdate(quote_amount=456), flow_users.pr_manager
            )
        result = await svc.update_promotion(
            p.id,
            PromotionUpdate(note_title="可补充", source_extra={"订单号": "SYNTHETIC"}),
            flow_users.pr,
        )
        assert result.note_title == "可补充" and result.source_extra["订单号"] == "SYNTHETIC"
        with pytest.raises(FieldPermissionDenied):
            await svc.update_promotion(p.id, PromotionUpdate(like_count=123), flow_users.pr)
        p.completed_at = datetime.now(UTC)
        await session.flush()
        with pytest.raises(PromotionCompletedReadonlyError):
            await svc.update_promotion(
                p.id, PromotionUpdate(note_title="不可改"), flow_users.pr_manager
            )
    finally:
        tenant_id_ctx.reset(token)
