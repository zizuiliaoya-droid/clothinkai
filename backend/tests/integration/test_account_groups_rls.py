"""真实应用角色验证新表RLS、账号/组的同租户外键。"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_return_freight_payment_rls import _as_app

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_account_group_tenant_rls_and_membership_fk(
    session, tenant_a, tenant_b, blogger_factory, product_factory, promotion_factory
):
    own = await blogger_factory.blogger(tenant=tenant_a)
    other = await blogger_factory.blogger(tenant=tenant_b)
    promotion = await promotion_factory.promotion(style=await product_factory.style(), blogger=own)
    groups = [uuid4(), uuid4()]
    for tenant, gid, blogger in ((tenant_a, groups[0], own), (tenant_b, groups[1], other)):
        await session.execute(
            text("INSERT INTO blogger_account_group(id,tenant_id,name) VALUES (:id,:tid,'账号组')"),
            {"id": gid, "tid": tenant.id},
        )
        await session.execute(
            text(
                "INSERT INTO blogger_account_member(id,tenant_id,group_id,blogger_id,is_active) VALUES (:id,:tid,:gid,:bid,true)"
            ),
            {"id": uuid4(), "tid": tenant.id, "gid": gid, "bid": blogger.id},
        )
        await session.execute(
            text(
                "INSERT INTO blogger_account_quote(id,tenant_id,group_id,account_key,account_ids,amount) VALUES (:id,:tid,:gid,:key,CAST(:ids AS jsonb),0)"
            ),
            {
                "id": uuid4(),
                "tid": tenant.id,
                "gid": gid,
                "key": str(blogger.id),
                "ids": '["' + str(blogger.id) + '"]',
            },
        )
    await _as_app(session, tenant_a.id)
    for table in ("blogger_account_group", "blogger_account_member", "blogger_account_quote"):
        rows = (
            (
                await session.execute(
                    text(f"SELECT tenant_id FROM {table} WHERE tenant_id IN (:a,:b)"),
                    {"a": tenant_a.id, "b": tenant_b.id},
                )
            )
            .scalars()
            .all()
        )
        assert rows == [tenant_a.id]
    for tid, gid, bid in (
        (tenant_b.id, groups[1], other.id),
        (tenant_a.id, groups[1], own.id),
        (tenant_a.id, groups[0], other.id),
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO blogger_account_member(id,tenant_id,group_id,blogger_id,is_active) VALUES (:id,:tid,:gid,:bid,false)"
                    ),
                    {"id": uuid4(), "tid": tid, "gid": gid, "bid": bid},
                )
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO blogger_account_quote(id,tenant_id,group_id,account_key,account_ids,amount) VALUES (:id,:tid,:gid,'bad','[]',1)"
                ),
                {"id": uuid4(), "tid": tenant_a.id, "gid": groups[1]},
            )
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO promotion_platform_post(id,tenant_id,promotion_id,account_id,platform) VALUES (:id,:tid,:pid,:bid,'抖音')"
                ),
                {"id": uuid4(), "tid": tenant_a.id, "pid": promotion.id, "bid": other.id},
            )
