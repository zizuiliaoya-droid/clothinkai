"""账号报价与履约边界，无数据库。"""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ValidationError
from app.core.security.field_permissions import FieldPermissionContext
from app.modules.auth.support_policy import support_read_allowed
from app.modules.blogger.account_schemas import AccountIdentity, AccountSnapshot
from app.modules.blogger.account_service import account_key, apply_deal_amount, visible_snapshot
from app.modules.flow.line_events import filter_payload
from app.modules.negotiation.schemas import NegotiationCreate
from app.modules.promotion import fulfillment
from app.modules.promotion.schemas import PlatformPostIn, PlatformPostResponse
from app.modules.promotion.urge_calculator import get_today


def test_support_can_only_read_account_group():
    account_id = uuid4()
    assert support_read_allowed("GET", f"/api/bloggers/{account_id}/account-group")
    assert not support_read_allowed("POST", "/api/bloggers/account-quotes/resolve")
    assert not support_read_allowed("POST", "/api/bloggers/account-groups")
    assert not support_read_allowed("PUT", f"/api/bloggers/account-groups/{account_id}")
    assert not support_read_allowed("PUT", f"/api/bloggers/account-groups/{account_id}/quotes")


def agreement():
    ids = [uuid4(), uuid4()]
    return AccountSnapshot(
        primary_account_id=ids[0],
        group_id=uuid4(),
        accounts=[
            AccountIdentity(
                account_id=value, platform="抖音", account=f"account-{i}", nickname=f"账号{i}"
            )
            for i, value in enumerate(ids)
        ],
        price_source="group_quote",
        quote_id=uuid4(),
        reference_amount=Decimal("500"),
    ).model_dump(mode="json")


def posts(snapshot):
    return [
        PlatformPostResponse(
            id=uuid4(),
            account_id=a["account_id"],
            platform=a["platform"],
            account=a["account"],
            nickname=a["nickname"],
            is_primary=a["account_id"] == snapshot["primary_account_id"],
            publish_url=f"https://example.test/{i}",
            actual_publish_date=get_today(),
        )
        for i, a in enumerate(snapshot["accounts"])
    ]


def test_specific_combination_key_zero_unknown_and_barter():
    a, b = uuid4(), uuid4()
    assert account_key([a, b, a]) == account_key([b, a])
    assert account_key([a]) != account_key([b]) != account_key([a, b])
    snapshot = agreement()
    assert apply_deal_amount(snapshot, None, barter=False)[1] == Decimal("500")
    manual, value = apply_deal_amount(snapshot, Decimal("0"), barter=False)
    assert value == 0 and manual["price_source"] == "manual"
    assert snapshot["price_source"] == "group_quote"
    snapshot["reference_amount"] = None
    snapshot["quote_id"] = None
    assert apply_deal_amount(snapshot, None, barter=False)[1] is None
    exchanged, value = apply_deal_amount(snapshot, None, barter=True)
    assert value == 0 and exchanged["price_source"] == "exchange"


@pytest.mark.parametrize("deny", ["field.blogger.quote:read", "field.promotion.quote_amount:read"])
def test_reference_amount_deny_applies_to_snapshot_and_event_diff(deny):
    snapshot = agreement()
    ctx = FieldPermissionContext(frozenset({"pr"}), frozenset(), frozenset({deny}))
    assert visible_snapshot(snapshot, ctx)["reference_amount"] is None
    event = {
        "snapshot": {"account_snapshot": snapshot},
        "diff": {"account_snapshot": {"before": snapshot, "after": snapshot}},
    }
    filtered = filter_payload(event, ctx)
    assert filtered["snapshot"]["account_snapshot"]["reference_amount"] is None
    assert filtered["diff"]["account_snapshot"]["before"]["reference_amount"] is None
    assert filtered["diff"]["account_snapshot"]["after"]["reference_amount"] is None
    assert snapshot["reference_amount"] == "500"


def test_client_empty_snapshot_is_not_a_writable_field():
    payload = NegotiationCreate.model_validate(
        {"blogger_id": str(uuid4()), "cooperation_mode": "送拍", "account_snapshot": None}
    )
    assert "account_snapshot" not in payload.model_dump()


@pytest.mark.parametrize(
    "problem", ["missing_row", "url", "date", "future", "invalid_url", "extra", "duplicate"]
)
def test_fulfillment_requires_exact_all_accounts_and_valid_facts(problem):
    snapshot = agreement()
    rows = posts(snapshot)
    if problem == "missing_row":
        rows.pop()
    elif problem == "url":
        rows[1].publish_url = None
    elif problem == "date":
        rows[1].actual_publish_date = None
    elif problem == "future":
        rows[1].actual_publish_date = get_today() + timedelta(days=1)
    elif problem == "invalid_url":
        rows[1].publish_url = "javascript:bad"
    elif problem == "extra":
        rows.append(rows[1].model_copy(update={"id": uuid4(), "account_id": uuid4()}))
    else:
        rows.append(rows[1])
    assert not fulfillment.fulfillment_summary(snapshot, rows).complete
    assert fulfillment.fulfillment_summary(None, rows) is None


@pytest.mark.asyncio
async def test_fixed_account_edit_preserves_obligations_but_allows_valid_correction(monkeypatch):
    snapshot = agreement()
    rows = posts(snapshot)
    promotion = SimpleNamespace(
        account_snapshot=snapshot,
        publish_status="已发布",
        settlement_status="待付款",
        blogger_id=UUID(snapshot["primary_account_id"]),
        publish_url=rows[0].publish_url,
        actual_publish_date=get_today(),
    )
    monkeypatch.setattr(fulfillment, "account_posts", AsyncMock(return_value=rows))
    session = AsyncMock()
    inputs = [PlatformPostIn.model_validate(row.model_dump()) for row in rows]
    with pytest.raises(ValidationError, match="全部约定账号"):
        await fulfillment.update_account_posts(session, promotion, inputs[:1])
    clearing = deepcopy(inputs)
    clearing[1].publish_url = None
    with pytest.raises(ValidationError, match="不能清空"):
        await fulfillment.update_account_posts(session, promotion, clearing)
    inputs[0].publish_url = "https://example.test/corrected"
    inputs[1].actual_publish_date = get_today() - timedelta(days=1)
    await fulfillment.update_account_posts(session, promotion, inputs)
    assert promotion.publish_url == "https://example.test/corrected"
    assert rows[1].actual_publish_date == get_today() - timedelta(days=1)
    assert fulfillment.fulfillment_summary(snapshot, rows).complete


@pytest.mark.asyncio
async def test_fixed_editor_cannot_replace_initial_primary_publish(monkeypatch):
    snapshot = agreement()
    rows = posts(snapshot)
    rows[0].publish_url = None
    rows[0].actual_publish_date = None
    promotion = SimpleNamespace(
        account_snapshot=snapshot, publish_status="未发布", settlement_status="未核查"
    )
    monkeypatch.setattr(fulfillment, "account_posts", AsyncMock(return_value=rows))
    inputs = [PlatformPostIn.model_validate(row.model_dump()) for row in rows]
    inputs[0].publish_url = "https://example.test/bypass"
    inputs[0].actual_publish_date = get_today()
    with pytest.raises(ValidationError, match="首次发布"):
        await fulfillment.update_account_posts(AsyncMock(), promotion, inputs)
