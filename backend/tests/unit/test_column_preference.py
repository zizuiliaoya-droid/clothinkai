from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.modules.report.preference_api import PromotionColumnPreference, get_promotion_columns


def test_default_and_future_column_ids_are_supported():
    assert PromotionColumnPreference().model_dump() == {"order": [], "hidden": []}
    assert PromotionColumnPreference(order=["stage", "future_column"], hidden=["quote_amount"])


@pytest.mark.parametrize(
    "payload", [{"user_id": "another-user"}, {"order": ["x"] * 81}, {"hidden": ["x" * 65]}]
)
def test_column_payload_only_accepts_bounded_column_lists(payload):
    with pytest.raises(ValidationError):
        PromotionColumnPreference.model_validate(payload)


@pytest.mark.asyncio
async def test_new_defaults_do_not_override_saved_visible_columns():
    service = SimpleNamespace(
        get_or_default=AsyncMock(side_effect=lambda _user, _key, default: default)
    )
    user = SimpleNamespace(id="user-id")
    assert (await get_promotion_columns(user, service)).hidden == ["like_count", "return_waybill"]
    service.get_or_default = AsyncMock(return_value={"order": [], "hidden": []})
    assert (await get_promotion_columns(user, service)).hidden == []
