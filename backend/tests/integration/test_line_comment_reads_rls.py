"""071阅读游标使用真实业务角色验证租户隔离。"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.test_return_freight_payment_rls import _as_app

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_comment_reads_isolate_real_app_tenants(session, tenant_a, tenant_b, factory):
    user_a, user_b = await factory.user(tenant_a), await factory.user(tenant_b)
    line_a, line_b = uuid4(), uuid4()
    insert = text(
        "INSERT INTO line_comment_read(id,tenant_id,user_id,line_id,last_read_comment_seq) VALUES (:id,:tenant,:user,:line,1)"
    )
    ids = [uuid4(), uuid4()]
    for row_id, tenant, user, line in (
        (ids[0], tenant_a, user_a, line_a),
        (ids[1], tenant_b, user_b, line_b),
    ):
        await session.execute(
            insert, {"id": row_id, "tenant": tenant.id, "user": user.id, "line": line}
        )
    await _as_app(session, tenant_a.id)
    assert tuple(
        (
            await session.execute(
                text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
        ).one()
    ) == (False, False)
    assert list(
        (
            await session.execute(
                text("SELECT id FROM line_comment_read WHERE id IN (:a,:b)"),
                {"a": ids[0], "b": ids[1]},
            )
        ).scalars()
    ) == [ids[0]]
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(
                insert, {"id": uuid4(), "tenant": tenant_b.id, "user": user_b.id, "line": uuid4()}
            )
    changed = (
        (
            await session.execute(
                text(
                    "UPDATE line_comment_read SET last_read_comment_seq=8 WHERE id IN (:a,:b) RETURNING id"
                ),
                {"a": ids[0], "b": ids[1]},
            )
        )
        .scalars()
        .all()
    )
    assert changed == [ids[0]]
