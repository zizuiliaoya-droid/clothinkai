"""两个非空租户，真实非superuser/non-bypass业务角色。"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_return_freight_payment_rls import _as_app, _promotion

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_platform_posts_isolate_real_app_tenants(
    session, tenant_a, tenant_b, product_factory, blogger_factory, promotion_factory
):
    own = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_a)
    other = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_b)
    insert = text(
        "INSERT INTO promotion_platform_post(id,tenant_id,promotion_id,platform,like_count) VALUES (:id,:tenant,:promotion,:platform,:likes)"
    )
    own_id, other_id = uuid4(), uuid4()
    for row_id, p, tenant in ((own_id, own, tenant_a), (other_id, other, tenant_b)):
        await session.execute(
            insert,
            {"id": row_id, "tenant": tenant.id, "promotion": p.id, "likes": 5, "platform": "抖音"},
        )
    await _as_app(session, tenant_a.id)
    assert tuple(
        (
            await session.execute(
                text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
        ).one()
    ) == (False, False)
    assert (
        await session.execute(
            text("SELECT id FROM promotion_platform_post WHERE id IN (:own,:other)"),
            {"own": own_id, "other": other_id},
        )
    ).scalars().all() == [own_id]
    for tenant_id, promotion_id, likes, platform in (
        (tenant_b.id, other.id, 0, "B站"),
        (tenant_a.id, own.id, -1, "B站"),
        (tenant_a.id, own.id, 1, "抖音"),
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert,
                    {
                        "id": uuid4(),
                        "tenant": tenant_id,
                        "promotion": promotion_id,
                        "likes": likes,
                        "platform": platform,
                    },
                )
    changed = (
        (
            await session.execute(
                text(
                    "UPDATE promotion_platform_post SET like_count=8 WHERE id IN (:own,:other) RETURNING id"
                ),
                {"own": own_id, "other": other_id},
            )
        )
        .scalars()
        .all()
    )
    assert changed == [own_id]
