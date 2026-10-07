"""7a-4 重新提交入参校验（PromotionResubmitRequest）。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.modules.promotion.schemas import PromotionResubmitRequest


class TestResubmitSchema:
    def test_note_required(self) -> None:
        with pytest.raises(ValidationError):
            PromotionResubmitRequest.model_validate({})

    def test_blank_note_is_missing(self) -> None:
        with pytest.raises(ValidationError):
            PromotionResubmitRequest(note="   \n\t ")

    def test_note_max_2000(self) -> None:
        assert len(PromotionResubmitRequest(note="字" * 2000).note) == 2000
        with pytest.raises(ValidationError):
            PromotionResubmitRequest(note="字" * 2001)

    def test_publish_url_must_be_http(self) -> None:
        with pytest.raises(ValidationError):
            PromotionResubmitRequest(note="重提", publish_url="xhslink.com/abc")

    def test_optional_fields_default_none(self) -> None:
        req = PromotionResubmitRequest(note=" 重提 ")
        assert req.note == "重提"
        assert req.publish_url is None
        assert req.actual_publish_date is None

    def test_valid_full_payload(self) -> None:
        req = PromotionResubmitRequest(
            note="换链接",
            publish_url="https://www.xiaohongshu.com/note/1",
            actual_publish_date=date(2026, 5, 27),
        )
        assert req.publish_url == "https://www.xiaohongshu.com/note/1"
        assert req.actual_publish_date == date(2026, 5, 27)
