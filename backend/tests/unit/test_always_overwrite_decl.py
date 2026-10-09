"""8b R2「总是覆盖」声明的护栏（设计 §6.5，简报补充二）。

- 声明只在 ``duplicate_rules.ALWAYS_OVERWRITE_FIELDS``；``BloggerApplier.specs`` 上的标记由它派生
- 判重键（账号、平台）与网页ID（D1①）、以及业务方定了「仍按冲突裁决」的字段永不进这个集合
- ``always_overwrite()`` 每次调用现查 dict（测试改 dict 即生效）
"""

from __future__ import annotations

import pytest

from app.modules.importer.conflict_appliers import BloggerApplier
from app.modules.importer.duplicate_rules import ALWAYS_OVERWRITE_FIELDS, always_overwrite

pytestmark = pytest.mark.unit

# 简报补充二：账号、昵称、平台、联系方式、报价（含报价备注）、标签、博主类型、网页ID 仍按冲突裁决
_NEVER_OVERWRITE = frozenset(
    {
        "xiaohongshu_id",
        "platform",
        "web_id",
        "nickname",
        "wechat",
        "phone",
        "quote",
        "quote_note",
        "category_tags",
        "quality_tags",
        "blogger_type",
    }
)


def test_blogger_declaration() -> None:
    """R2 定了：粉丝数与抖音统计快照以文件为准。"""
    assert ALWAYS_OVERWRITE_FIELDS["blogger"] == frozenset({"follower_count", "platform_metrics"})


def test_never_overwrite_fields_disjoint() -> None:
    """判重键与网页ID（D1①：跨批网页ID 不同要进冲突）不能被自动覆盖。"""
    assert {"xiaohongshu_id", "platform", "web_id"}.isdisjoint(ALWAYS_OVERWRITE_FIELDS["blogger"])
    assert _NEVER_OVERWRITE.isdisjoint(ALWAYS_OVERWRITE_FIELDS["blogger"])


def test_spec_flags_derived_from_declaration() -> None:
    flagged = {s.name for s in BloggerApplier.specs if s.always_overwrite}
    names = {s.name for s in BloggerApplier.specs}
    assert flagged == ALWAYS_OVERWRITE_FIELDS["blogger"] & names
    assert flagged == {"follower_count"}  # platform_metrics 不是比较字段，_write_snapshot 现查


def test_always_overwrite_reads_dict_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    assert always_overwrite("blogger", "follower_count") is True
    assert always_overwrite("blogger", "nickname") is False
    assert always_overwrite("style", "external_image_url") is False  # 未声明的对象类型
    monkeypatch.setitem(ALWAYS_OVERWRITE_FIELDS, "blogger", frozenset())
    assert always_overwrite("blogger", "follower_count") is False
