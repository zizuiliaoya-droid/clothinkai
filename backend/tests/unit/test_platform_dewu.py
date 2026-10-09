"""平台加「得物」（流程线 PR-1，设计 7.7 / 9.3 F 块）。

- 博主 schema（Create / Update）按 ``Platform`` 枚举校验，得物要能过
- 点赞折算系数表显式登记得物 = 1.0（不靠「未知平台默认 1.0」兜底）
- ``publish_progress.like_sum_expr`` 只给系数 < 1 的平台出 CASE：加了得物之后
  生成的 SQL 片段与改动前逐字相同（字面量取自改动前的基线输出）
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.blogger.enums import Platform
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.promotion.legacy_settings import PLATFORM_LIKE_COEFFICIENT
from app.services.metric.publish_progress import like_sum_expr


def test_platform_enum_has_dewu() -> None:
    assert Platform("得物") is Platform.DEWU
    assert [p.value for p in Platform] == ["小红书", "抖音", "快手", "B站", "得物"]


def test_blogger_create_accepts_dewu() -> None:
    b = BloggerCreate(xiaohongshu_id="dewu_001", nickname="得物博主", platform="得物")
    assert b.platform is Platform.DEWU


def test_blogger_update_accepts_dewu() -> None:
    u = BloggerUpdate(platform="得物")
    assert u.platform is Platform.DEWU
    assert u.model_fields_set == {"platform"}


def test_blogger_schema_still_rejects_unknown_platform() -> None:
    """场景有效性：枚举校验确实生效，不是什么都收。"""
    with pytest.raises(ValidationError):
        BloggerCreate(xiaohongshu_id="x_001", nickname="n", platform="微博")
    with pytest.raises(ValidationError):
        BloggerUpdate(platform="微博")


def test_like_coefficient_dewu_is_one() -> None:
    assert "得物" in PLATFORM_LIKE_COEFFICIENT
    assert PLATFORM_LIKE_COEFFICIENT["得物"] == Decimal("1.0")


@pytest.mark.parametrize(
    "column, expected",
    [
        (
            "like_count",
            "COALESCE(SUM(CASE WHEN platform IN ('抖音', '快手') "
            "THEN like_count * 0.1 ELSE like_count END), 0)",
        ),
        (
            "p.like_count",
            "COALESCE(SUM(CASE WHEN platform IN ('抖音', '快手') "
            "THEN p.like_count * 0.1 ELSE p.like_count END), 0)",
        ),
    ],
)
def test_like_sum_expr_unchanged(column: str, expected: str) -> None:
    assert like_sum_expr(column) == expected


def test_like_sum_expr_default_column_unchanged() -> None:
    assert like_sum_expr() == like_sum_expr("like_count")
