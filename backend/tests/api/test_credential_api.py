"""U12 平台凭据 API 契约测试（鉴权 + OpenAPI + 不回显）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestCredentialApiContract:
    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/credentials/")
        assert resp.status_code == 401

    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/credentials/",
                json={
                    "platform": "千牛",
                    "username": "x",
                    "password": "y",
                    "privacy_consent": True,
                },
            )
        assert resp.status_code == 401

    async def test_openapi_exposes_credential_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/credentials/" in paths
        assert "/api/credentials/{credential_id}" in paths
        assert "/api/credentials/{credential_id}/pause" in paths
        assert "/api/credentials/{credential_id}/resume" in paths

    async def test_credential_public_schema_has_no_password(self) -> None:
        """CredentialPublic 响应 schema 不含 password / password_ciphertext。"""
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        schemas = resp.json().get("components", {}).get("schemas", {})
        public = schemas.get("CredentialPublic", {})
        props = public.get("properties", {})
        assert "password" not in props
        assert "password_ciphertext" not in props
        assert "username" in props
        assert "status" in props
