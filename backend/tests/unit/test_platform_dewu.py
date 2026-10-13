"""平台加「得物」（流程线 PR-1，设计 7.7 / 9.3 F 块）。

- 博主 schema（Create / Update）按 ``Platform`` 枚举校验，得物要能过
- 点赞折算系数默认表显式登记得物 = 1.0（不靠「未知平台默认 1」兜底）；SQL 片段的逐字断言
  随 7e R7 移到 ``test_platform_metric_settings.py``（系数改成查询参数，不再在导入时拼 CASE）
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.modules.blogger.enums import Platform
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.metric_config.settings import DEFAULT_LIKE_COEFFICIENTS, PlatformMetricSettings


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
    """默认配置显式登记得物 = 1.0（7e R7 起系数在 ``promotion_platform_setting``，默认值见 settings）。"""
    assert "得物" in DEFAULT_LIKE_COEFFICIENTS
    assert DEFAULT_LIKE_COEFFICIENTS["得物"] == Decimal("1.0")
    assert PlatformMetricSettings.defaults().like_coefficient("得物") == Decimal("1.0")
