"""画像HTTP读写、显式deny、租户与原文件边界。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import get_session
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.main import app
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.blogger.audience_schemas import AudienceInput
from app.modules.blogger.audience_service import save_audience

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
        "dimension": "age",
        "collected_on": "2026-11-05",
        "source": "平台截图",
        "completeness": "partial",
        "estimated": True,
        "entries": [{"label": "41–50", "percentage": "23.5%"}, {"label": "50+", "percentage": 0}],
    }


@pytest.mark.usefixtures("tenant_ctx")
async def test_manual_save_read_history_and_write_deny(as_user, blogger_factory, flow_users):
    blogger = await blogger_factory.blogger()
    endpoint = f"/api/bloggers/{blogger.id}/audience"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        as_user(flow_users.pr, {"blogger:read"})
        assert (await client.post(endpoint, json=payload())).status_code == 403
        as_user(flow_users.pr, {"*"}, {"blogger:write"})
        assert (await client.post(endpoint, json=payload())).status_code == 403
        as_user(flow_users.pr, {"blogger:read", "blogger:write"})
        saved = await client.post(endpoint, json=payload())
        assert saved.status_code == 200, saved.text
        assert saved.json()["inserted"] and saved.json()["current"]
        assert not (await client.post(endpoint, json=payload())).json()["inserted"]
        older = await client.post(endpoint, json={**payload(), "collected_on": "2026-10-01"})
        assert not older.json()["current"] and "归档" in older.json()["message"]
        invalid = await client.post(endpoint, json={**payload(), "completeness": "complete"})
        assert invalid.status_code == 422
        as_user(flow_users.pr, {"blogger:read"})
        current = (await client.get(endpoint)).json()
        assert current["total"] == 1 and current["items"][0]["entries"][1]["percentage"] == 0
        assert current["can_write"] is False and current["can_read_source"] is False
        history = (await client.get(endpoint, params={"history": True})).json()
        assert history["total"] == 2


@pytest.mark.usefixtures("tenant_ctx")
async def test_source_gate_and_tenant_visibility(
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
    batch = await import_batch_factory.batch(source="manual_blogger_audience")
    result = await save_audience(
        session,
        tenant_a.id,
        blogger.id,
        AudienceInput.model_validate(payload()),
        flow_users.pr.id,
        batch.id,
        [1, 2],
    )
    monkeypatch.setattr(
        attachment_service, "get_signed_url", lambda *args: "https://example.test/private-source"
    )
    endpoint = f"/api/bloggers/{blogger.id}/audience/{result.id}/source"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        as_user(flow_users.pr, {"blogger:read"})
        assert (await client.get(endpoint)).status_code == 404
        as_user(flow_users.pr, {"blogger:read", "importer.batch:read"})
        source = await client.get(endpoint)
        assert source.status_code == 200 and source.json()["url"].endswith("private-source")
        assert (await client.get(f"/api/bloggers/{other.id}/audience")).status_code == 404
        assert (
            await client.get(f"/api/bloggers/{other.id}/audience", params={"history": True})
        ).status_code == 404
        as_user(flow_users.pr, {"blogger:read", "blogger:write"})
        assert (
            await client.post(f"/api/bloggers/{other.id}/audience", json=payload())
        ).status_code == 404
