"""PR10 R1：只覆盖驳回换图例外与收件字段个人撤销。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.exceptions import FieldPermissionDenied
from app.modules.flow.line_events import LineEventRepository
from app.modules.metric_config.service import PlatformMetricSettingService
from app.modules.metric_config.settings import PlatformMetricSettings
from app.modules.promotion.freight_repository import ReturnFreightRepository
from app.modules.promotion.schemas import PromotionListFilters
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import UrgeThresholds
from app.modules.urge.service import UrgeService
from tests.unit.fixtures.flow_matrix_expected import DOCS
from tests.unit.test_flow_matrix import PERSONAS


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["pr", "pr_manager"])
@pytest.mark.parametrize("phase", ["rejected", "recalled_rejected", "review_pending"])
async def test_resubmit_screenshot_reaches_upload_only_in_rejected_phase(monkeypatch, role, phase):
    doc = DOCS["promotion"]["推广驳回 · 待重提"]
    if phase == "recalled_rejected":
        doc = replace(
            doc,
            stage="召回中",
            state="召回中",
            column="G",
            recall_status="召回中",
            published_stage="推广驳回 · 待重提",
        )
    elif phase == "review_pending":
        doc = DOCS["promotion"]["待主管审核"]
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    promotion = SimpleNamespace(
        id=uuid4(), tenant_id=user.tenant_id, line_id=uuid4(), brand_comment_attachment_id=uuid4()
    )
    uploaded = SimpleNamespace(id=uuid4(), r2_key="synthetic/screenshot")
    svc = PromotionService(AsyncMock())
    svc._writable_promotion = AsyncMock(return_value=promotion)
    svc._flow_actor = AsyncMock(return_value=PERSONAS[role])
    svc._promotion_doc = AsyncMock(return_value=doc)
    svc._attachment_service = SimpleNamespace(
        create_upload_record=AsyncMock(return_value=(uploaded, None)),
        upload_bytes=Mock(),
        mark_uploaded=AsyncMock(),
    )
    svc._audit = AsyncMock()
    svc._to_response = AsyncMock(return_value="synthetic-response")
    monkeypatch.setattr(LineEventRepository, "append", AsyncMock())
    request = {
        "filename": "synthetic.png",
        "mime_type": "image/png",
        "data": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        "user": user,
    }
    if phase == "review_pending":
        with pytest.raises(FieldPermissionDenied):
            await svc.upload_brand_comment(promotion.id, **request)
        svc._attachment_service.create_upload_record.assert_not_awaited()
    else:
        assert await svc.upload_brand_comment(promotion.id, **request) == "synthetic-response"
        svc._attachment_service.create_upload_record.assert_awaited_once()
        assert promotion.brand_comment_attachment_id == uploaded.id


@pytest.mark.asyncio
async def test_receiver_search_capabilities_follow_individual_phone_revoke(monkeypatch):
    actor = PERSONAS["pr"]
    actor = replace(
        actor,
        field_ctx=replace(
            actor.field_ctx, revokes=frozenset({"field.promotion.receiver_phone:read"})
        ),
    )
    svc = PromotionService(AsyncMock())
    svc._flow_actor = AsyncMock(return_value=actor)
    svc._repo = AsyncMock()
    svc._repo.list_with_cte.return_value = ([], 0)
    svc._items_repo = AsyncMock()
    svc.amount_log_readable = AsyncMock(return_value=False)
    monkeypatch.setattr(
        UrgeService,
        "get_urge_thresholds",
        AsyncMock(return_value=UrgeThresholds(urge_days=10, important_days=3)),
    )
    monkeypatch.setattr(
        PlatformMetricSettingService,
        "get_settings",
        AsyncMock(return_value=PlatformMetricSettings.defaults()),
    )
    monkeypatch.setattr(ReturnFreightRepository, "briefs", AsyncMock(return_value={}))
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    result = await svc.list_promotions(
        filters=PromotionListFilters(), page=1, page_size=10, user=user
    )
    assert result.ui["fields"] == {"receiver_name": {"state": "read"}}
    assert await svc._list_actor(PromotionListFilters(receiver_name="合成"), user) == actor
    with pytest.raises(FieldPermissionDenied):
        await svc._list_actor(PromotionListFilters(receiver_phone="138"), user)
