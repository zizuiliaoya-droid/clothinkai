"""款式图片纯函数（设计 §7.1、§7.3、§7.4）。

- 8a-4 ``normalize_external_image_url``
- 8a-2 ``resolve_style_image``（上传 > 外部 > 无、签名失败回落、R2 未配置）与 ``image_stem``
  （用例表与前端 ``imageBatch.test.ts`` 同一张）
"""

from __future__ import annotations

from typing import Any

import pytest

import app.modules.product.images as images_mod
from app.core import attachment as att_mod
from app.modules.product.images import (
    EXTERNAL_IMAGE_URL_MAX_LEN,
    StyleImage,
    image_stem,
    normalize_external_image_url,
    resolve_style_image,
)

EXT = "https://img.example.invalid/a.jpg"


class _FakeSigner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("sign boom")
        return f"https://fake-r2.local/{kw['Params']['Key']}?exp={kw['ExpiresIn']}"


@pytest.mark.parametrize(
    ("key", "url", "expected"),
    [
        ("k/main.png", EXT, StyleImage("https://fake-r2.local/k/main.png?exp=3600", "upload")),
        ("k/main.png", None, StyleImage("https://fake-r2.local/k/main.png?exp=3600", "upload")),
        (None, EXT, StyleImage(EXT, "external")),
        ("", EXT, StyleImage(EXT, "external")),
        (None, None, None),
        (None, "", None),
    ],
)
def test_resolve_priority(
    monkeypatch: pytest.MonkeyPatch, key: str | None, url: str | None, expected: Any
) -> None:
    monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeSigner(), raising=False)
    assert resolve_style_image(key, url) == expected


def test_sign_failure_falls_back_to_external_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 直接记 logger 调用：应用的日志配置可能关掉向 root 传播，caplog 不一定收得到
    warnings: list[str] = []
    monkeypatch.setattr(images_mod.log, "warning", lambda msg, *a, **k: warnings.append(msg))
    monkeypatch.setattr(
        att_mod.attachment_service, "_client", _FakeSigner(fail=True), raising=False
    )
    assert resolve_style_image("k/main.png", EXT) == StyleImage(EXT, "external")
    assert resolve_style_image("k/main.png", None) is None
    assert warnings == ["style_image_sign_failed", "style_image_sign_failed"]


def test_r2_not_configured_uses_external(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(att_mod.attachment_service, "_client", None, raising=False)
    assert resolve_style_image("k/main.png", EXT) == StyleImage(EXT, "external")
    assert resolve_style_image("k/main.png", None) is None


# 与 frontend/src/features/product/imageBatch.test.ts 的 IMAGE_STEM_CASES 同一张表
IMAGE_STEM_CASES = [
    ("a/b/ABC.jpg", "ABC"),
    ("C:\\x\\abc.PNG", "abc"),
    (" 2025945 .webp", "2025945"),
    ("x.tar.gz", "x.tar"),
    ("2025946", "2025946"),
    ("  款A01.jpeg ", "款A01"),
    (".jpg", ".jpg"),
    ("dir/", ""),
    ("", ""),
]


@pytest.mark.parametrize(("filename", "stem"), IMAGE_STEM_CASES)
def test_image_stem(filename: str, stem: str) -> None:
    assert image_stem(filename) == stem


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
