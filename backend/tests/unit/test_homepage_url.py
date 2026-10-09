"""8b-4 博主主页链接校验：``domain.normalize_homepage_url`` 与 schema 入参（设计 §3.8、§7.1）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.blogger.domain import normalize_homepage_url
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate


class TestNormalizeHomepageUrl:
    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_empty_is_none(self, raw: str | None) -> None:
        assert normalize_homepage_url(raw) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://www.douyin.com/user/abc", "https://www.douyin.com/user/abc"),
            ("  http://xhslink.com/a/b?x=1  ", "http://xhslink.com/a/b?x=1"),
            ("HTTPS://Example.com/p", "HTTPS://Example.com/p"),
        ],
    )
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_homepage_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "javascript:alert(1)",
            "data:text/html,<b>x</b>",
            "ftp://example.com/a",
            "www.douyin.com/user/abc",
            "https://",
            "https://exa mple.com/a",
            "https://example.com/\x07",
            "https://example.com/" + "a" * 1100,
        ],
    )
    def test_invalid_raises(self, raw: str) -> None:
        with pytest.raises(ValueError, match="主页链接"):
            normalize_homepage_url(raw)


class TestSchemaHomepageUrl:
    def test_create_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BloggerCreate(xiaohongshu_id="A1", nickname="x", homepage_url="javascript:alert(1)")

    def test_create_valid_trimmed(self) -> None:
        p = BloggerCreate(xiaohongshu_id="A1", nickname="x", homepage_url=" https://a.com/u ")
        assert p.homepage_url == "https://a.com/u"

    def test_update_blank_clears(self) -> None:
        p = BloggerUpdate(homepage_url="")
        assert p.homepage_url is None
        assert "homepage_url" in p.model_fields_set

    def test_update_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BloggerUpdate(homepage_url="ftp://a.com/u")

    def test_account_pattern_accepts_dot(self) -> None:
        # 抖音博主ID 含「.」（设计 §2、§7.1）
        assert BloggerCreate(xiaohongshu_id="ab.c_1-2", nickname="x").xiaohongshu_id == "ab.c_1-2"
        assert BloggerUpdate(xiaohongshu_id="ab.c").xiaohongshu_id == "ab.c"
        with pytest.raises(ValidationError):
            BloggerCreate(xiaohongshu_id="ab c", nickname="x")

    def test_web_id_and_quote_note_length(self) -> None:
        with pytest.raises(ValidationError):
            BloggerCreate(xiaohongshu_id="A1", nickname="x", web_id="1" * 65)
        with pytest.raises(ValidationError):
            BloggerUpdate(quote_note="字" * 501)
        assert BloggerUpdate(quote_note="  ").quote_note is None
        assert BloggerCreate(
            xiaohongshu_id="A1", nickname="x", quote_note=" 图文500 "
        ).quote_note == ("图文500")
