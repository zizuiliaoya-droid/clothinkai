import pytest
from pydantic import ValidationError

from app.modules.report.preference_api import PromotionColumnPreference


def test_default_and_future_column_ids_are_supported():
    assert PromotionColumnPreference().model_dump() == {"order": [], "hidden": []}
    assert PromotionColumnPreference(order=["stage", "future_column"], hidden=["quote_amount"])


@pytest.mark.parametrize(
    "payload", [{"user_id": "another-user"}, {"order": ["x"] * 81}, {"hidden": ["x" * 65]}]
)
def test_column_payload_only_accepts_bounded_column_lists(payload):
    with pytest.raises(ValidationError):
        PromotionColumnPreference.model_validate(payload)
