"""076只加可空创建元数据，保留原谈款RLS和唯一创建行键。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_negotiation_creation_key_schema_state(session):
    columns = (
        await session.execute(
            text(
                "SELECT column_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='negotiation' AND column_name IN ('client_create_key','creation_group_id','creation_pr_id','creation_request_hash')"
            )
        )
    ).all()
    assert len(columns) == 4
    assert all(nullable == "YES" and default is None for _, nullable, default in columns)
    index = await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_negotiation_create_key'")
    )
    assert "UNIQUE" in index and "tenant_id, creation_pr_id, client_create_key" in index
    assert "client_create_key IS NOT NULL" in index
    assert (
        await session.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='negotiation'"
            )
        )
    ).one() == (True, True)
    fk = await session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='fk_negotiation_creation_pr'"
        )
    )
    assert "(tenant_id, creation_pr_id)" in fk and "REFERENCES" in fk
