"""推广详情读取不能继承报表 / Timeline 的宽权限。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.models import Promotion
from app.modules.promotion.read_access import PromotionReadAccess
from tests.api.test_report_drill_api import (  # noqa: F401 -- pytest fixtures
    _RANGE,
    _URL,
    _call,
    _override,
    tenant_ctx,
)
from tests.api.test_report_drill_api import (
    scene as _scene,
)

scene = _scene


@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
@pytest.mark.parametrize(
    "who,status",
    [
        ("admin", 200),
        ("pr", 200),
        ("pr2", 200),
        ("pr_manager", 200),
        ("operations", 200),
        ("finance", 403),
        ("warehouse", 403),
    ],
)
async def test_default_role_detail_api_matrix(session, scene, who, status):
    """060 后仓库专用读取、财务字段可见均不授予完整推广详情入口。"""
    response = await _call(session, getattr(scene.users, who), f"/api/promotions/{scene.p1}")
    assert response.status_code == status, response.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
async def test_report_link_matches_detail_and_grant(session, scene):
    finance = scene.users.finance
    response = await _call(session, finance, f"{_URL}?{_RANGE}")
    assert response.status_code == 200, response.text
    assert response.json()["items"]
    assert all(r["detail_url"] is None for r in response.json()["items"])
    assert (await _call(session, finance, f"/api/promotions/{scene.p1}")).status_code == 403
    await _override(session, finance, "promotion:read", "grant")
    response = await _call(session, finance, f"{_URL}?{_RANGE}")
    for row in response.json()["items"]:
        assert row["detail_url"] == f"/promotions?id={row['id']}"
        assert (await _call(session, finance, f"/api/promotions/{row['id']}")).status_code == 200
    p = await session.get(Promotion, scene.p1)
    p.is_active = False
    await session.flush()
    assert (await _call(session, finance, f"/api/promotions/{scene.p1}")).status_code == 404
    perms = EffectivePermissions(str(finance.id), frozenset({"promotion:read"}))
    assert scene.p1 not in await PromotionReadAccess(session).visible_ids(
        [scene.p1], tenant_id=finance.tenant_id, perms=perms
    )


@pytest.mark.asyncio
@pytest.mark.rls
async def test_shared_access_uses_real_app_role(
    session, tenant_a, tenant_b, product_factory, blogger_factory, promotion_factory
):
    from tests.integration.test_return_freight_payment_rls import _as_app

    rows = []
    for tenant in (tenant_a, tenant_b):
        style = await product_factory.style(tenant=tenant)
        blogger = await blogger_factory.blogger(tenant=tenant)
        rows.append(await promotion_factory.promotion(tenant=tenant, style=style, blogger=blogger))
    ids = [r.id for r in rows]
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        await _as_app(session, tenant_a.id)
        access = PromotionReadAccess(session)
        perms = EffectivePermissions("rls-test", frozenset({"promotion:read"}))
        assert await access.visible_ids(ids, tenant_id=tenant_a.id, perms=perms) == {ids[0]}
        assert await access.get(ids[1], tenant_id=tenant_a.id, perms=perms) is None
        assert await access.get(ids[0], tenant_id=tenant_a.id, perms=perms) is not None
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_detail_service_rejects_report_and_finance_only() -> None:
    from app.modules.promotion.service import PromotionService

    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    perms = EffectivePermissions(
        str(user.id), frozenset({"report.*:read", "finance.settlement:read"})
    )
    service = PromotionService.__new__(PromotionService)
    service._repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=uuid4())))
    service._session = AsyncMock()
    service._flow_actor = AsyncMock(return_value=SimpleNamespace(perms=perms))
    service._to_response = AsyncMock(return_value="must not expose")
    with pytest.raises(PermissionDeniedError):
        await service.get_promotion(uuid4(), user)
    service._to_response.assert_not_called()
    service._session.execute.assert_not_called()
