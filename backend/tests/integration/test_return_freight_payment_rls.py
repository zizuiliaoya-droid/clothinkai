"""R33 寄回运费付款单 ``return_freight_payment`` 用 ``clothing_app`` 真跑 RLS（PR-6 评审 Not tested 第三处）。

test_flow_rls_schema.py 只读系统表（ENABLE + FORCE + ``tenant_isolation`` policy）；这里用业务角色真读写：
设计 3.5 T1 ~ T6 要的是 SELECT / INSERT / UPDATE（没有删除，065 显式授这三项、REVOKE DELETE），
本租户能读、能写、能改，别的租户读不到、写不进、改不了，也不能把行改到别的租户；本租户的行也删不掉。

和 test_rls.py 一样打 ``rls`` 标记（全量 ``-m "not rls"`` 不跑，单跑 ``-m rls``）。不另开 clothing_app 连接：
``session`` fixture 的数据不提交，别的连接看不到；改成同一事务里 ``SET LOCAL ROLE clothing_app``，
造数仍走连接本身的角色（测试库是 postgres），角色切换和数据都随 fixture 回滚。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

_APP_ROLE = "clothing_app"


async def _promotion(
    product_factory: Any, blogger_factory: Any, promotion_factory: Any, t: Any
) -> Any:
    style = await product_factory.style(tenant=t)
    blogger = await blogger_factory.blogger(tenant=t)
    return await promotion_factory.promotion(style=style, blogger=blogger, tenant=t)


async def _insert_freight(
    session: AsyncSession, attachment_factory: Any, *, tenant: Any, promotion: Any
) -> UUID:
    """插一张待审核付款单；附件先用连接本身的角色建好（附件表不是这里要测的）。"""
    qr = await attachment_factory.attachment(tenant=tenant, purpose="promotion_payment_qr")
    waybill = await attachment_factory.attachment(tenant=tenant)
    return await _insert_freight_row(
        session, tenant_id=tenant.id, promotion_id=promotion.id, qr_id=qr.id, waybill_id=waybill.id
    )


async def _insert_freight_row(
    session: AsyncSession, *, tenant_id: UUID, promotion_id: UUID, qr_id: UUID, waybill_id: UUID
) -> UUID:
    fid = uuid4()
    await session.execute(
        text(
            "INSERT INTO return_freight_payment "
            "(id, tenant_id, created_at, updated_at, promotion_id, amount, "
            " payment_qr_attachment_id, waybill_attachment_id, submitted_at) "
            "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), NOW(), NOW(), CAST(:p AS uuid), "
            " CAST(:amount AS numeric), CAST(:qr AS uuid), CAST(:wb AS uuid), NOW())"
        ),
        {
            "id": str(fid),
            "t": str(tenant_id),
            "p": str(promotion_id),
            "amount": "12.00",
            "qr": str(qr_id),
            "wb": str(waybill_id),
        },
    )
    return fid


async def _as_app(session: AsyncSession, tenant_id: UUID) -> None:
    """本事务剩下的语句按 clothing_app + 租户上下文执行（不开 bypass）。"""
    await session.execute(text(f"SET LOCAL ROLE {_APP_ROLE}"))
    await session.execute(
        text("SELECT set_config('app.tenant_id', CAST(:t AS text), true)"), {"t": str(tenant_id)}
    )
    await session.execute(text("SELECT set_config('app.bypass_rls', 'off', true)"))
    # 场景有效性：后面的断言真的是 clothing_app 在跑
    assert (await session.execute(text("SELECT current_user"))).scalar_one() == _APP_ROLE
    role = (
        await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        )
    ).one()
    assert tuple(role) == (False, False), "必须以NOSUPERUSER/NOBYPASSRLS应用身份验证"


async def _visible_ids(session: AsyncSession, ids: list[UUID]) -> set[UUID]:
    rows = await session.execute(
        text("SELECT id FROM return_freight_payment WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": [str(i) for i in ids]},
    )
    return set(rows.scalars().all())


async def _remark(session: AsyncSession, fid: UUID) -> str | None:
    return (
        await session.execute(
            text("SELECT remark FROM return_freight_payment WHERE id = CAST(:id AS uuid)"),
            {"id": str(fid)},
        )
    ).scalar_one()


@pytest_asyncio.fixture
async def freight_pair(
    session: AsyncSession,
    tenant_a: Any,
    tenant_b: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    attachment_factory: Any,
) -> tuple[UUID, UUID]:
    """A、B 两个租户各一张付款单（连接本身的角色造数）。"""
    ids = []
    for t in (tenant_a, tenant_b):
        p = await _promotion(product_factory, blogger_factory, promotion_factory, t)
        ids.append(await _insert_freight(session, attachment_factory, tenant=t, promotion=p))
    # 场景有效性：两张都真落库了
    assert await _visible_ids(session, ids) == set(ids)
    return ids[0], ids[1]


@pytest.mark.integration
@pytest.mark.rls
@pytest.mark.asyncio
class TestReturnFreightPaymentAsClothingApp:
    async def test_reads_own_tenant_only(
        self, session: AsyncSession, tenant_a: Any, freight_pair: tuple[UUID, UUID]
    ) -> None:
        own, other = freight_pair
        await _as_app(session, tenant_a.id)
        assert await _visible_ids(session, [own, other]) == {own}

    async def test_inserts_own_tenant_row(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
    ) -> None:
        p = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_a)
        qr = await attachment_factory.attachment(tenant=tenant_a, purpose="promotion_payment_qr")
        waybill = await attachment_factory.attachment(tenant=tenant_a)
        await _as_app(session, tenant_a.id)
        fid = await _insert_freight_row(
            session, tenant_id=tenant_a.id, promotion_id=p.id, qr_id=qr.id, waybill_id=waybill.id
        )
        assert await _visible_ids(session, [fid]) == {fid}

    async def test_cannot_insert_other_tenant_row(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
    ) -> None:
        p = await _promotion(product_factory, blogger_factory, promotion_factory, tenant_b)
        qr = await attachment_factory.attachment(tenant=tenant_b, purpose="promotion_payment_qr")
        waybill = await attachment_factory.attachment(tenant=tenant_b)
        await _as_app(session, tenant_a.id)
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin_nested():
                await _insert_freight_row(
                    session,
                    tenant_id=tenant_b.id,
                    promotion_id=p.id,
                    qr_id=qr.id,
                    waybill_id=waybill.id,
                )

    async def test_updates_own_tenant_row_only(
        self, session: AsyncSession, tenant_a: Any, freight_pair: tuple[UUID, UUID]
    ) -> None:
        own, other = freight_pair
        await _as_app(session, tenant_a.id)
        sql = text(
            "UPDATE return_freight_payment SET remark = 'app 改的' WHERE id = CAST(:id AS uuid)"
        )
        assert (await session.execute(sql, {"id": str(own)})).rowcount == 1
        assert (await session.execute(sql, {"id": str(other)})).rowcount == 0
        assert await _remark(session, own) == "app 改的"
        # 换回连接本身的角色核对 B 那张没被改
        await session.execute(text("RESET ROLE"))
        assert await _remark(session, other) is None

    async def test_cannot_move_row_to_other_tenant(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        freight_pair: tuple[UUID, UUID],
    ) -> None:
        own, _ = freight_pair
        await _as_app(session, tenant_a.id)
        with pytest.raises(DBAPIError, match="row-level security"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE return_freight_payment SET tenant_id = CAST(:t AS uuid) "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"t": str(tenant_b.id), "id": str(own)},
                )
        assert await _visible_ids(session, [own]) == {own}

    async def test_cannot_delete_even_own_tenant_row(
        self, session: AsyncSession, tenant_a: Any, freight_pair: tuple[UUID, UUID]
    ) -> None:
        """设计 T1 ~ T6 不删（驳回后改同一张重提），065 显式 REVOKE DELETE。"""
        own, _ = freight_pair
        await _as_app(session, tenant_a.id)
        with pytest.raises(DBAPIError, match="permission denied"):
            async with session.begin_nested():
                await session.execute(
                    text("DELETE FROM return_freight_payment WHERE id = CAST(:id AS uuid)"),
                    {"id": str(own)},
                )
        assert await _visible_ids(session, [own]) == {own}
