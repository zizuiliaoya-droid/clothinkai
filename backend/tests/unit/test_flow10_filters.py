"""PR10 final filter contract and PATCH whitelist."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import FieldPermissionDenied
from app.core.security.permissions import EffectivePermissions
from app.modules.flow.matrix import ensure_patch_allowed, ui_for, writable_fields
from app.modules.promotion.flow_matrix import PROMOTION_MATRIX
from app.modules.promotion.schemas import PromotionListFilters, PromotionUpdate
from app.modules.urge.service import UrgeService
from tests.unit.fixtures.flow_matrix_expected import DOCS
from tests.unit.test_flow_matrix import PERSONAS


def test_stage_filters_preserve_values_and_reject_unknown():
    filters = PromotionListFilters(
        stage="待录 7 天数据",
        badge="metrics_due",
        completed=False,
        receiver_name="王",
        receiver_phone="138",
    )
    assert filters.stage == "待录 7 天数据" and filters.completed is False
    assert filters.receiver_name == "王" and filters.receiver_phone == "138"
    for patch in ({"stage": "错误阶段"}, {"badge": "unknown"}, {"receiver_name": "字" * 33}):
        with pytest.raises(ValidationError):
            PromotionListFilters(**patch)


def test_every_patch_field_is_registered_and_unregistered_is_denied():
    assert set(PromotionUpdate.model_fields) == set(PROMOTION_MATRIX.patch_groups)
    with pytest.raises(FieldPermissionDenied):
        ensure_patch_allowed(
            PERSONAS["admin"], DOCS["promotion"]["待主管审核"], {"hidden_new_field"}
        )


def test_fault_patch_whitelist():
    allowed = writable_fields(PERSONAS["pr"], DOCS["promotion"]["待推送仓库"]).allowed
    assert allowed == {
        "receiver_name",
        "receiver_phone",
        "receiver_address",
        "note_title",
        "source_extra",
    }, "FLOW7_FAULT_EXPECTED:patch_whitelist"


@pytest.mark.parametrize("freight", ["待审核", "待付款", "已驳回", "已付款"])
def test_existing_freight_keeps_amount_patch_locked(freight):
    doc = replace(DOCS["promotion"]["召回中"], freight_status=freight)
    assert (
        ui_for(PERSONAS["admin"], doc).to_dict(view="detail")["fields"]["recall_info"]["state"]
        == "read"
    )
    with pytest.raises(FieldPermissionDenied):
        ensure_patch_allowed(
            PERSONAS["admin"], doc, {"return_shipping_fee", "return_shipping_payer"}
        )


@pytest.mark.parametrize(
    "underlying,settlement,cooperation,quote,metrics",
    [
        ("待主管审核", "待核查", "edit", "edit", "grey"),
        ("待财务付款", "待付款", "read", "read", "grey"),
        ("待满 7 天", "已结款", "read", "read", "grey"),
        ("待录 7 天数据", "已结款", "read", "read", "edit"),
    ],
)
def test_published_recall_keeps_stage_g_and_original_field_locks(
    underlying, settlement, cooperation, quote, metrics
):
    doc = replace(
        DOCS["promotion"]["召回中"],
        publish_status="已发布",
        promotion_settlement_status=settlement,
        published_stage=underlying,
        settlement_status="待财务付款",
    )
    ui = ui_for(PERSONAS["pr_manager"], doc).to_dict(view="detail")
    assert ui["column"] == "G"
    assert ui["fields"]["cooperation"]["state"] == cooperation
    assert ui["fields"]["quote_amount"]["state"] == quote
    assert ui["fields"]["metrics"]["state"] == metrics
    assert ui["fields"]["payment_qr"]["state"] == "read"  # 财务付款强锁仍有效


@pytest.mark.parametrize("write", [False, True])
def test_urge_ui_uses_effective_scope_and_exact_task_state(write):
    perms = EffectivePermissions(
        user_id="synthetic", scopes=frozenset({"promotion.urge:write"} if write else {"promotion.urge:read"})
    )
    row = {
        "id": uuid4(),
        "promotion_id": uuid4(),
        "blogger_id": uuid4(),
        "pr_id": None,
        "publish_status": "未发布",
        "promotion_active": True,
        "status": "进行中",
        "urge_count": 1,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    svc = UrgeService(AsyncMock())

    def actions(**changes):
        return set(svc._row_to_task(row | changes, max_urge_times=3, perms=perms).ui["actions"])

    assert actions() == ({"urge", "complete", "close"} if write else set())
    assert actions(status="已关闭", close_reason="已完成") == ({"urge"} if write else set())
    assert actions(status="已关闭", close_reason="博主已发布", publish_status="已发布") == set()
    assert "urge" not in actions(promotion_active=False)
