"""8a-2 ``read_image_batch_form`` 在 Sentry 预读之后的行为（设计 §1.4、§7.3、N10）。

sentry-sdk 2.14 的 Starlette 集成对带 Content-Length 且 ≤ 10KB 的请求，在进处理函数之前先
``request.body()`` 再 ``request.form()``，解析异常被它吞掉。这里手工构造 ``Request(scope, receive)``
照这个顺序预读，再调 ``read_image_batch_form``：它必须复用缓存的请求体、不再去读 receive
（receive 第二次被调用会一直挂起，模拟客户端在等响应），5 秒内结束。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from starlette.datastructures import UploadFile
from starlette.requests import Request

import app.modules.product.style_image_service as svc
from app.core.exceptions import ValidationError
from app.modules.product.style_image_service import read_image_batch_form

BOUNDARY = "8aUnitBoundary"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _multipart(files: list[tuple[str, bytes]]) -> bytes:
    out = b""
    for filename, data in files:
        out += (
            (
                f"--{BOUNDARY}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                "Content-Type: image/png\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    return out + f"--{BOUNDARY}--\r\n".encode()


def _request(body: bytes) -> Request:
    sent = {"done": False}

    async def receive() -> dict[str, Any]:
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # 再读 receive 就挂起：等同客户端在等响应
        raise AssertionError("unreachable")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/styles/main-images/batch",
        "headers": [
            (b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode()),
            (b"content-length", str(len(body)).encode()),
        ],
        "query_string": b"",
    }
    return Request(scope, receive)


async def _sentry_preread(request: Request) -> None:
    await request.body()
    try:
        await request.form()
    except Exception:  # sentry-sdk 吞掉解析异常
        pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_preread_returns_files_without_hanging() -> None:
    request = _request(_multipart([("A01.png", PNG), ("B02.png", PNG)]))
    await _sentry_preread(request)
    form = await asyncio.wait_for(read_image_batch_form(request), timeout=5)
    try:
        files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
        assert [f.filename for f in files] == ["A01.png", "B02.png"]
        assert await files[0].read() == PNG
    finally:
        await form.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_failed_preread_broken_body_is_422() -> None:
    """预读时解析已失败的损坏样本（分隔行格式错）→ 仍是 422 IMAGE_BATCH_FORM_INVALID。"""
    request = _request(f"--{BOUNDARY}X\r\n".encode())
    await _sentry_preread(request)
    with pytest.raises(ValidationError) as exc_info:
        await asyncio.wait_for(read_image_batch_form(request), timeout=5)
    assert exc_info.value.code == "IMAGE_BATCH_FORM_INVALID"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_after_preread_too_many_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """预读过的 4 个文件，MAX_FILES 临时改成 3 → 422 IMAGE_BATCH_COUNT_INVALID。"""
    monkeypatch.setattr(svc, "MAX_FILES", 3)
    request = _request(_multipart([(f"F{i}.png", PNG) for i in range(4)]))
    await _sentry_preread(request)
    with pytest.raises(ValidationError) as exc_info:
        await asyncio.wait_for(read_image_batch_form(request), timeout=5)
    assert exc_info.value.code == "IMAGE_BATCH_COUNT_INVALID"
