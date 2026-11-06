from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_blogger_claims import work
from tests.integration.test_return_freight_payment_rls import _as_app

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_claims_rls_and_composite_tenant_references(
    session, tenant_a, tenant_b, factory, blogger_factory
):
    a, b = (
        await blogger_factory.blogger(tenant=tenant_a),
        await blogger_factory.blogger(tenant=tenant_b),
    )
    fresh = await blogger_factory.blogger(tenant=tenant_a)
    ua, ub = await factory.user(tenant_a), await factory.user(tenant_b)
    wa, wb = await work(session, tenant_a), await work(session, tenant_b)
    insert = text(
        "INSERT INTO blogger_claim(id,tenant_id,blogger_id,pr_id,operated_by,method,work_wechat_id) VALUES (:id,:tenant,:blogger,:pr,:actor,'assigned',:work)"
    )
    own, other = uuid4(), uuid4()
    for ident, tenant, blogger, user, account in (
        (own, tenant_a, a, ua, wa),
        (other, tenant_b, b, ub, wb),
    ):
        await session.execute(
            insert,
            {
                "id": ident,
                "tenant": tenant.id,
                "blogger": blogger.id,
                "pr": user.id,
                "actor": user.id,
                "work": account.id,
            },
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
        await session.scalars(
            text("SELECT id FROM blogger_claim WHERE id IN (:a,:b)"), {"a": own, "b": other}
        )
    ).all() == [own]
    for tenant, blogger, pr, actor, account in (
        (tenant_b.id, b.id, ub.id, ub.id, wb.id),
        (tenant_a.id, b.id, ua.id, ua.id, wa.id),
        (tenant_a.id, fresh.id, ub.id, ua.id, wa.id),
        (tenant_a.id, fresh.id, ua.id, ub.id, wa.id),
        (tenant_a.id, fresh.id, ua.id, ua.id, wb.id),
    ):
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(
                    insert,
                    {
                        "id": uuid4(),
                        "tenant": tenant,
                        "blogger": blogger,
                        "pr": pr,
                        "actor": actor,
                        "work": account,
                    },
                )
    changed = (
        await session.scalars(
            text("DELETE FROM blogger_claim WHERE id IN (:a,:b) RETURNING id"),
            {"a": own, "b": other},
        )
    ).all()
    assert changed == [own]
