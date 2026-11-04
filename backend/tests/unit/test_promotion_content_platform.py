"""PR9 schema rules and independent content snapshots."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.modules.promotion.schemas import PlatformPostIn, PromotionUpdate

TEST_TODAY = date(2026, 11, 4)


@pytest.mark.parametrize(
    "patch",
    [
        {"like_count": -1},
        {"like_count": 2147483648},
        {"publish_url": "javascript:alert(1)"},
        {"actual_publish_date": TEST_TODAY + timedelta(days=1)},
        {"platform": " "},
    ],
)
def test_platform_post_rejects_invalid_input(monkeypatch, patch):
    # 收集到执行可能跨租户时区零点，未来日期与校验时钟使用同一固定基准。
    monkeypatch.setattr("app.modules.promotion.urge_calculator.get_today", lambda: TEST_TODAY)
    with pytest.raises(ValidationError):
        PlatformPostIn.model_validate({"platform": "抖音", **patch})


def test_platform_post_allows_optional_metrics_and_legacy_platform():
    row = PlatformPostIn(platform="历史平台", publish_url="https://example.com/post", like_count=0)
    assert row.like_count == 0
    assert row.actual_publish_date is None


def test_content_category_preserves_legacy_values_and_explicit_clear():
    assert PromotionUpdate(content_category="历史写法").content_category == "历史写法"
    assert "content_category" in PromotionUpdate(content_category=None).model_fields_set
    assert "content_category" not in PromotionUpdate().model_fields_set
    with pytest.raises(ValidationError):
        PromotionUpdate(content_category="一二三四五六七八九")
