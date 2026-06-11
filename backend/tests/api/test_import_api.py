"""U06a importer API 契约测试（鉴权 401 + OpenAPI 8 端点）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestImportApiAuth:
    async def test_upload_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/imports/upload",
                data={"source": "fake_source"},
                files={"file": ("d.csv", b"a,b\n1,2\n", "text/csv")},
            )
        assert resp.status_code == 401

    async def test_list_batches_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/imports/batches")
        assert resp.status_code == 401

    async def test_get_batch_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(f"/api/imports/batches/{uuid4()}")
        assert resp.status_code == 401

    async def test_retry_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(f"/api/imports/batches/{uuid4()}/retry")
        assert resp.status_code == 401

    async def test_errors_download_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(f"/api/imports/batches/{uuid4()}/errors/download")
        assert resp.status_code == 401

    async def test_create_field_mapping_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/imports/field-mappings",
                json={
                    "source": "fake_source",
                    "columns": [{"source_col": "a", "target_field": "b"}],
                },
            )
        assert resp.status_code == 401


@pytest.mark.api
@pytest.mark.asyncio
class TestImportOpenApi:
    async def test_openapi_exposes_import_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/imports/upload" in paths
        assert "/api/imports/batches" in paths
        assert "/api/imports/batches/{batch_id}" in paths
        assert "/api/imports/batches/{batch_id}/retry" in paths
        assert "/api/imports/batches/{batch_id}/errors/download" in paths
        assert "/api/imports/field-mappings" in paths
        assert "/api/imports/field-mappings/active" in paths
