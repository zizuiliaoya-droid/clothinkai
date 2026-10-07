"""安全模块 API 契约测试（不连库）：未登录 401 + OpenAPI。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

PATH = "/api/security/ip-diagnostics"


@pytest.mark.api
@pytest.mark.asyncio
class TestSecurityApiContract:
    async def test_ip_diagnostics_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(PATH, headers={"X-Forwarded-For": "203.0.113.9"})
        assert resp.status_code == 401
        body = resp.json()
        assert body["code"] == "TOKEN_INVALID"
        # 未登录拿不到任何诊断内容
        assert "headers" not in body
        assert "203.0.113.9" not in resp.text

    async def test_ip_diagnostics_rejects_invalid_token(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(PATH, headers={"Authorization": "Bearer not-a-jwt"})
        assert resp.status_code == 401
        assert resp.json()["code"] == "TOKEN_INVALID"

    async def test_openapi_exposes_ip_diagnostics(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert PATH in paths
        assert set(paths[PATH]) == {"get"}
