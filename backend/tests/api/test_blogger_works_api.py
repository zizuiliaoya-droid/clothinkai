"""作品 HTTP 入口的读写权限、原文件权限及租户约束。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import get_session
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.main import app
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.blogger.work_schemas import WorkInput
from app.modules.blogger.work_service import save_work

pytestmark = [pytest.mark.api, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
def as_user(session):
    async def session_override():
        yield session

    app.dependency_overrides[get_session] = session_override

    def apply(user, scopes, revokes=()):
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: EffectivePermissions(
            str(user.id), frozenset(scopes), frozenset(revokes)
        )

    yield apply
    for dependency in (get_session, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dependency, None)


def payload():
    return {
        "work_id": "work-1",
        "published_on": "2026-11-01",
        "collected_at": "2026-11-05 14:30:00",
        "source": "平台采集",
        "like_count": "14.3w",
    }


@pytest.mark.usefixtures("tenant_ctx")
async def test_create_read_and_explicit_write_deny(as_user, blogger_factory, flow_users):
    blogger = await blogger_factory.blogger()
    endpoint = f"/api/bloggers/{blogger.id}/works"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        as_user(flow_users.pr, {"blogger:read"})
        assert (await client.post(endpoint, json=payload())).status_code == 403
        as_user(flow_users.pr, {"*"}, {"blogger:write"})
        assert (await client.post(endpoint, json=payload())).status_code == 403
        as_user(flow_users.pr, {"blogger:read", "blogger:write"})
        saved = await client.post(endpoint, json=payload())
        assert saved.status_code == 200, saved.text
        assert saved.json()["inserted"] is True
        duplicate = await client.post(endpoint, json=payload())
        assert duplicate.json()["inserted"] is False
        as_user(flow_users.pr, {"blogger:read"})
        page = (await client.get(endpoint)).json()
        assert page["total"] == 1 and page["items"][0]["like_count"] == 143000
        assert page["items"][0]["interaction_total"] is None
        assert page["can_write"] is False
        assert (
            await client.get(endpoint, params={"date_from": "2026-11-03", "date_to": "2026-11-01"})
        ).status_code == 422


@pytest.mark.usefixtures("tenant_ctx")
async def test_source_requires_import_read_and_cross_tenant_is_hidden(
    session,
    tenant_a,
    tenant_b,
    as_user,
    blogger_factory,
    flow_users,
    import_batch_factory,
    monkeypatch,
):
    from app.core.attachment import attachment_service

    blogger = await blogger_factory.blogger()
    other = await blogger_factory.blogger(tenant=tenant_b)
    batch = await import_batch_factory.batch(source="manual_blogger_work")
    row, _ = await save_work(
        session, tenant_a.id, blogger.id, WorkInput(**payload()), flow_users.pr.id, batch.id, 1
    )
    monkeypatch.setattr(
        attachment_service, "get_signed_url", lambda *args: "https://example.test/private-source"
    )
    endpoint = f"/api/bloggers/{blogger.id}/works/{row.id}/source"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        as_user(flow_users.pr, {"blogger:read"})
        assert (await client.get(endpoint)).status_code == 404
        as_user(flow_users.pr, {"blogger:read", "importer.batch:read"})
        source = await client.get(endpoint)
        assert source.status_code == 200 and source.json()["url"].endswith("private-source")
        assert (await client.get(f"/api/bloggers/{other.id}/works")).status_code == 404
        as_user(flow_users.pr, {"blogger:read", "blogger:write"})
        assert (
            await client.post(f"/api/bloggers/{other.id}/works", json=payload())
        ).status_code == 404
