"""U07 wecom 领域纯函数单元测试。"""

from __future__ import annotations

from datetime import date

from app.modules.wecom.domain import (
    build_render_ctx,
    extract_template_vars,
    is_important,
    render_template,
    validate_template_vars,
)


def test_extract_vars():
    assert extract_template_vars("{博主昵称}你好 {商品简称}") == ["博主昵称", "商品简称"]


def test_validate_pass():
    assert validate_template_vars("{博主昵称}的{商品简称}{预定发布日期}{剩余天数}") == []


def test_validate_illegal():
    invalid = validate_template_vars("{博主昵称}{未知变量}{价格}")
    assert "未知变量" in invalid and "价格" in invalid


def test_render_replaces_whitelist():
    out = render_template(
        "{博主昵称}你好，{商品简称}还剩{剩余天数}天",
        {"博主昵称": "小美", "商品简称": "连衣裙", "剩余天数": "3"},
    )
    assert out == "小美你好，连衣裙还剩3天"


def test_render_missing_value_empty():
    out = render_template("{博主昵称}-{剩余天数}", {"博主昵称": "小美"})
    assert out == "小美-"


def test_is_important_within_3_days():
    today = date(2026, 6, 1)
    assert is_important(
        scheduled_publish_date=date(2026, 6, 3), today=today
    ) is True


def test_is_important_overdue():
    today = date(2026, 6, 10)
    assert is_important(
        scheduled_publish_date=date(2026, 6, 1), today=today
    ) is True


def test_not_important_urge_range():
    today = date(2026, 6, 1)
    # 剩余 7 天 → 催发（非重要）
    assert is_important(
        scheduled_publish_date=date(2026, 6, 8), today=today
    ) is False


def test_build_render_ctx():
    ctx = build_render_ctx(
        blogger_nickname="小美",
        style_short_name="连衣裙",
        scheduled_publish_date=date(2026, 6, 5),
        today=date(2026, 6, 1),
    )
    assert ctx["博主昵称"] == "小美"
    assert ctx["剩余天数"] == "4"
    assert ctx["预定发布日期"] == "2026-06-05"


def test_build_render_ctx_no_date():
    ctx = build_render_ctx(
        blogger_nickname="小美",
        style_short_name="裙",
        scheduled_publish_date=None,
        today=date(2026, 6, 1),
    )
    assert ctx["预定发布日期"] == ""
    assert ctx["剩余天数"] == "0"
