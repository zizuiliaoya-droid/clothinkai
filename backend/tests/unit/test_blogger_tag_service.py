"""U11 单元测试：BloggerTagService 纯函数。

覆盖 compute_blogger_type 阈值边界 / compute_read_like_ratio 分母 0/None 安全 /
is_fake_account 保守判定。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.blogger.tag_config import (
    FAKE_RATIO_THRESHOLD,
    FOLLOWER_KOC_MIN,
    FOLLOWER_KOL_MIN,
)
from app.modules.blogger.tag_service import BloggerTagService


class TestComputeBloggerType:
    def test_none_follower(self) -> None:
        assert BloggerTagService.compute_blogger_type(None) is None

    def test_below_koc(self) -> None:
        assert BloggerTagService.compute_blogger_type(0) == "素人"
        assert (
            BloggerTagService.compute_blogger_type(FOLLOWER_KOC_MIN - 1) == "素人"
        )

    def test_koc_boundary(self) -> None:
        assert BloggerTagService.compute_blogger_type(FOLLOWER_KOC_MIN) == "KOC"
        assert (
            BloggerTagService.compute_blogger_type(FOLLOWER_KOL_MIN - 1) == "KOC"
        )

    def test_kol_boundary(self) -> None:
        assert BloggerTagService.compute_blogger_type(FOLLOWER_KOL_MIN) == "KOL"
        assert BloggerTagService.compute_blogger_type(5_000_000) == "KOL"


class TestComputeReadLikeRatio:
    def test_none_profile(self) -> None:
        assert BloggerTagService.compute_read_like_ratio(None) is None

    def test_empty_profile(self) -> None:
        assert BloggerTagService.compute_read_like_ratio({}) is None

    def test_missing_note_stats(self) -> None:
        assert (
            BloggerTagService.compute_read_like_ratio({"foo": "bar"}) is None
        )

    def test_zero_reads_returns_none(self) -> None:
        profile = {"note_stats": {"avg_likes": 100, "avg_reads": 0}}
        assert BloggerTagService.compute_read_like_ratio(profile) is None

    def test_normal_ratio(self) -> None:
        profile = {"note_stats": {"avg_likes": 50, "avg_reads": 1000}}
        ratio = BloggerTagService.compute_read_like_ratio(profile)
        assert ratio == Decimal("0.05")


class TestIsFakeAccount:
    def test_none_ratio_conservative_false(self) -> None:
        assert BloggerTagService.is_fake_account(None) is False

    def test_below_threshold_is_fake(self) -> None:
        assert BloggerTagService.is_fake_account(Decimal("0.005")) is True

    def test_at_threshold_is_fake(self) -> None:
        assert BloggerTagService.is_fake_account(FAKE_RATIO_THRESHOLD) is True

    def test_above_threshold_not_fake(self) -> None:
        assert BloggerTagService.is_fake_account(Decimal("0.05")) is False


@pytest.mark.parametrize(
    ("follower", "expected"),
    [
        (None, None),
        (9_999, "素人"),
        (10_000, "KOC"),
        (99_999, "KOC"),
        (100_000, "KOL"),
    ],
)
def test_blogger_type_table(follower: int | None, expected: str | None) -> None:
    assert BloggerTagService.compute_blogger_type(follower) == expected
