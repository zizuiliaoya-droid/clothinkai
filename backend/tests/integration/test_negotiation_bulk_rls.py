"""原谈款RLS仍保护创建键，原创建者复合外键不可跨租户。"""

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from tests.integration.test_return_freight_payment_rls import _as_app

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_creation_keys_tenant_rls_creator_fk_and_unique(
    session,
    tenant_a,
    tenant_b,
    flow_users,
    factory,
    product_factory,
    blogger_factory,
    negotiation_factory,
):
    role = await session.scalar(select(Role).where(Role.code == "pr"))
    token = tenant_id_ctx.set(tenant_b.id)
    try:
        other_pr = await factory.user(tenant_b, roles=[role])
    finally:
        tenant_id_ctx.reset(token)
    own_style, own_blogger = await product_factory.style(), await blogger_factory.blogger()
    own = await negotiation_factory.negotiation(
        style=own_style, blogger=own_blogger, pr=flow_users.pr
    )
    own2 = await negotiation_factory.negotiation(
        style=own_style, blogger=own_blogger, pr=flow_users.pr
    )
    other = await negotiation_factory.negotiation(
        style=await product_factory.style(tenant=tenant_b),
        blogger=await blogger_factory.blogger(tenant=tenant_b),
        pr=other_pr,
        tenant=tenant_b,
    )
    key, gid = uuid4(), uuid4()
    for row, creator, row_key in [
        (own, flow_users.pr, key),
        (own2, flow_users.pr, uuid4()),
        (other, other_pr, key),
    ]:
        await session.execute(
            text(
                "UPDATE negotiation SET client_create_key=:key,creation_group_id=:gid,creation_pr_id=:uid,creation_request_hash=:hash WHERE id=:id"
            ),
            {"id": row.id, "key": row_key, "gid": gid, "uid": creator.id, "hash": "a" * 64},
        )
    await _as_app(session, tenant_a.id)
    ids = (
        (
            await session.execute(
                text("SELECT id FROM negotiation WHERE client_create_key=:key"), {"key": key}
            )
        )
        .scalars()
        .all()
    )
    assert ids == [own.id]
    for sql, values in [
        (
            "UPDATE negotiation SET creation_pr_id=:uid WHERE id=:id",
            {"uid": other_pr.id, "id": own.id},
        ),
        ("UPDATE negotiation SET client_create_key=:key WHERE id=:id", {"key": key, "id": own2.id}),
        ("UPDATE negotiation SET creation_request_hash=NULL WHERE id=:id", {"id": own.id}),
        ("UPDATE negotiation SET tenant_id=:tid WHERE id=:id", {"tid": tenant_b.id, "id": own.id}),
    ]:
        with pytest.raises(DBAPIError):
            async with session.begin_nested():
                await session.execute(text(sql), values)
    assert (
        await session.execute(
            text("UPDATE negotiation SET creation_request_hash=:hash WHERE id=:id"),
            {"hash": "b" * 64, "id": other.id},
        )
    ).rowcount == 0
