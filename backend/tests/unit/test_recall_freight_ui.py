"""召回动作与精确收款码例外；冻结矩阵表之外的行为断言。"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.modules.flow.matrix import ui_for
from tests.unit.fixtures.flow_matrix_expected import DOCS
from tests.unit.test_flow_matrix import PERSONAS

pytestmark = pytest.mark.unit


def test_unpublished_shipped_offers_recall_and_unshipped_is_grey():
    shipped = ui_for(PERSONAS["pr"], DOCS["promotion"]["档期内"])
    assert shipped.actions.get("recall_start") is not None
    assert shipped.actions["recall_start"].state == "enabled"
    unshipped = ui_for(PERSONAS["pr"], DOCS["promotion"]["待推送仓库"])
    assert unshipped.actions.get("recall_start") is not None
    assert unshipped.actions["recall_start"].state == "grey"


def test_settled_recalling_qr_is_edit_but_financial_pending_is_read():
    recalling = ui_for(PERSONAS["pr"], DOCS["promotion"]["召回中·已结款"])
    assert recalling.fields["payment_qr"].state == "edit"
    pending = ui_for(PERSONAS["pr"], DOCS["promotion"]["召回中·结款单待财务付款"])
    assert pending.fields["payment_qr"].state == "read"


@pytest.mark.parametrize("who", ["pr", "pr_manager", "admin"])
@pytest.mark.parametrize("status", ["待审核", "待付款", "已付款"])
def test_r33_snapshot_locks_waybill_even_star(who, status):
    doc = replace(
        DOCS["promotion"]["已完结"], completed_at=datetime.now(UTC), freight_status=status
    )
    ui = ui_for(PERSONAS[who], doc)
    assert "recall_waybill" not in ui.actions and "freight_submit" not in ui.actions


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("settlement", ["已付款", "待财务付款"])
def test_rejected_r33_qr_exception_never_overrides_financial_pending(completed, settlement):
    doc = replace(
        DOCS["promotion"]["已完结"],
        completed_at=datetime.now(UTC) if completed else None,
        publish_status="已取消",
        freight_status="已驳回",
        settlement_status=settlement,
    )
    ui = ui_for(PERSONAS["pr"], doc)
    assert ui.fields["payment_qr"].state == ("read" if settlement == "待财务付款" else "edit")
    assert ui.actions["recall_waybill"].state == "enabled"
    assert "freight_submit" not in ui.actions


def test_completed_published_ongoing_recall_only_continues_existing_actions():
    doc = replace(
        DOCS["promotion"]["已完结"],
        completed_at=datetime.now(UTC),
        publish_status="已发布",
        recall_status="召回中",
    )
    ui = ui_for(PERSONAS["pr"], doc)
    assert ui.actions["recall_waybill"].state == ui.actions["recall_result"].state == "enabled"
    assert "recall_start" not in ui.actions and "freight_submit" not in ui.actions
    assert "cooperation" not in ui.edits


def test_completed_legacy_without_r33_only_true_star_can_supplement():
    doc = replace(
        DOCS["promotion"]["已完结"],
        completed_at=datetime.now(UTC),
        publish_status="已取消",
        recall_status="召回成功",
    )
    for who in ("pr", "pr_manager"):
        actions = ui_for(PERSONAS[who], doc).actions
        assert "freight_submit" not in actions and "recall_waybill" not in actions
    admin = ui_for(PERSONAS["admin"], doc)
    assert (
        admin.actions["freight_submit"].state == admin.actions["recall_waybill"].state == "enabled"
    )


@pytest.mark.parametrize("status", ["待审核", "待付款", "已驳回"])
def test_open_freight_makes_deactivate_readonly_even_star(status):
    doc = replace(DOCS["promotion"]["召回中"], freight_status=status)
    action = ui_for(PERSONAS["admin"], doc).actions["deactivate"]
    assert action.state == "disabled"
    assert action.reason == "寄回运费尚未付款，不能停用"


@pytest.mark.parametrize("who", ["pr_manager", "admin"])
def test_missing_negotiator_disables_review_without_breaking_read(who):
    from app.modules.promotion.freight_flow import freight_doc

    action = ui_for(PERSONAS[who], freight_doc("待审核", None)).actions["freight_review"]
    assert action.state == "disabled"
    assert action.reason == "缺少谈款人/负责PR，无法核对审核回避"
