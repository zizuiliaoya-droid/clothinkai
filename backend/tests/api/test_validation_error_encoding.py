"""全局 422 编码：走现有博主 HTTP 接口，不改业务 Schema（R7 收尾 D）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.blogger.deps import get_blogger_service

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

_BODY = {"xiaohongshu_id": "validation_quote", "nickname": "校验测试"}


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from app.main import app, limiter

    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    permissions = EffectivePermissions(user_id=str(user.id), scopes=frozenset({"blogger:write"}))
    # 无效请求应在真实 BloggerCreate 校验处中止，服务不应被执行，也不需要数据库。
    monkeypatch.setitem(app.dependency_overrides, get_current_user_active, lambda: user)
    monkeypatch.setitem(app.dependency_overrides, get_current_perms, lambda: permissions)
    monkeypatch.setitem(app.dependency_overrides, get_blogger_service, lambda: None)
    monkeypatch.setattr(limiter, "enabled", False)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as ac:
        yield ac


async def test_decimal_bound_returns_422_instead_of_500(client: AsyncClient) -> None:
    response = await client.post("/api/bloggers/", json={**_BODY, "quote": "-0.01"})
    assert response.status_code == 422, response.text
    assert response.json() == {
        "code": "VALIDATION_ERROR",
        "message": "请求数据校验失败",
        "details": {
            "errors": [
                {
                    "type": "greater_than_equal",
                    "loc": ["body", "quote"],
                    "msg": "Input should be greater than or equal to 0",
                    "input": "-0.01",
                    "ctx": {"ge": "0"},
                }
            ]
        },
    }


async def test_plain_integer_422_response_bytes_unchanged(client: AsyncClient) -> None:
    response = await client.post("/api/bloggers/", json={**_BODY, "follower_count": -1})
    assert response.status_code == 422, response.text
    # 旧处理器的原始响应；不借助被测编码器构造期望，字段顺序 / 类型 / 空白均需保持。
    assert (
        response.content
        == (
            '{"code":"VALIDATION_ERROR","message":"请求数据校验失败","details":{"errors":['
            '{"type":"greater_than_equal","loc":["body","follower_count"],'
            '"msg":"Input should be greater than or equal to 0","input":-1,"ctx":{"ge":0}}]}}'
        ).encode()
    )


async def test_value_error_context_is_json_safe(client: AsyncClient) -> None:
    response = await client.post("/api/bloggers/", json={**_BODY, "category_tags": [" "]})
    assert response.status_code == 422, response.text
    error: dict[str, Any] = response.json()["details"]["errors"][0]
    assert error["loc"] == ["body", "category_tags"]
    assert error["type"] == "value_error"
    assert error["ctx"] == {"error": "每个 tag 长度需 1-32"}
