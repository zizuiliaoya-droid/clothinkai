"""8a-4 ``normalize_external_image_url``（设计 §7.4）。FEAT-005 再加 resolve_style_image / image_stem。"""

from __future__ import annotations

import pytest

from app.modules.product.images import EXTERNAL_IMAGE_URL_MAX_LEN, normalize_external_image_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://img.example.invalid/a.jpg", "https://img.example.invalid/a.jpg"),
        ("  http://img.example.invalid/a.jpg  ", "http://img.example.invalid/a.jpg"),
        ("HTTPS://IMG.example.invalid/a.jpg?x=1", "HTTPS://IMG.example.invalid/a.jpg?x=1"),
    ],
)
def test_http_https_accepted(raw: str, expected: str) -> None:
    assert normalize_external_image_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "ftp://img.example.invalid/a.jpg",
        "//img.example.invalid/a.jpg",
        "https://",
        "https:///a.jpg",
        "img.example.invalid/a.jpg",
        "https://img.example.invalid/a b.jpg",
        "https://img.example.invalid/a\tb.jpg",
        "https://img.example.invalid/a\x00.jpg",
        "https://img.example.invalid/a\x85.jpg",
        "https://[::1/a.jpg",
        "",
        "   ",
        None,
        123,
    ],
)
def test_rejected(raw: object) -> None:
    assert normalize_external_image_url(raw) is None


def test_length_limit() -> None:
    prefix = "https://img.example.invalid/"
    ok = prefix + "a" * (EXTERNAL_IMAGE_URL_MAX_LEN - len(prefix))
    assert normalize_external_image_url(ok) == ok
    assert normalize_external_image_url(ok + "a") is None
