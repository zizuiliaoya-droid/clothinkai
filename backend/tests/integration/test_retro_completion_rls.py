"""真实clothing_app角色，双租户非空场景证明M5读写RLS与append-only ACL。"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.test_return_freight_payment_rls import _as_app, _promotion

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_completion_and_retro_isolate_real_app_tenants(
    session: AsyncSession,
    tenant_a: Any,
    tenant_b: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    own = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_a)
    other = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_b)
    params = {"own": own.id, "other": other.id, "tenant": tenant_a.id, "foreign": tenant_b.id}
    assert (
        await session.scalar(
            text("SELECT count(*) FROM promotion WHERE id IN (:own,:other)"), params
        )
    ) == 2
    await _as_app(session, tenant_a.id)
    flags = (
        await session.execute(
            text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
    ).one()
    assert tuple(flags) == (False, False)
    assert set(
        (
            await session.execute(
                text("SELECT id FROM promotion WHERE id IN (:own,:other)"), params
            )
        ).scalars()
    ) == {own.id}
    changed = (
        (
            await session.execute(
                text(
                    "UPDATE promotion SET completed_at=now() WHERE id IN (:own,:other) RETURNING id"
                ),
                params,
            )
        )
        .scalars()
        .all()
    )
    assert changed == [own.id]
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(
                text("UPDATE promotion SET tenant_id=:foreign WHERE id=:own"), params
            )


async def test_retro_and_timeline_app_writes_and_acl(
    session: AsyncSession,
    tenant_a: Any,
    tenant_b: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    own = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_a)
    other = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_b)
    params = {
        "id": uuid4(),
        "tenant": tenant_a.id,
        "blogger": own.blogger_id,
        "promo": own.id,
        "line": own.line_id,
    }
    await _as_app(session, tenant_a.id)
    insert = text(
        "INSERT INTO blogger_retrospective(id,tenant_id,blogger_id,promotion_id,content) "
        "VALUES (:id,:tenant,:blogger,:promo,'合成主管复盘') RETURNING id"
    )
    assert await session.scalar(insert, params) == params["id"]
    with pytest.raises(DBAPIError):
        async with session.begin_nested():
            await session.execute(
                insert,
                {
                    **params,
                    "id": uuid4(),
                    "tenant": tenant_b.id,
                    "blogger": other.blogger_id,
                    "promo": other.id,
                },
            )
    from app.core.audit import AuditService
    from app.modules.flow.line_events import LineEventRepository, LineEventType

    event = await LineEventRepository(session).append(
        tenant_id=tenant_a.id,
        line_id=own.line_id,
        promotion_id=own.id,
        event_type=LineEventType.PROMOTION_COMPLETED,
        actor_id=None,
    )
    assert event.seq > 0
    await AuditService(session).log(
        "promotion.complete", tenant_id=tenant_a.id, actor_type="system"
    )
    await session.flush()
    # 保存点必须在错误发生前建立，退出时显式rollback，避免污染后续权限检查。
    grants = (
        await session.execute(
            text(
                "SELECT has_table_privilege(current_user,'line_event','INSERT'),"
                "has_table_privilege(current_user,'line_event','UPDATE'),"
                "has_table_privilege(current_user,'line_event','DELETE'),"
                "has_table_privilege(current_user,'audit_log','INSERT'),"
                "has_table_privilege(current_user,'audit_log','UPDATE'),"
                "has_table_privilege(current_user,'audit_log','DELETE'),"
                "has_sequence_privilege(current_user,pg_get_serial_sequence('line_event','seq'),'USAGE')"
            )
        )
    ).one()
    assert tuple(grants) == (True, False, False, True, False, False, True)
