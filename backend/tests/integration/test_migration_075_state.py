"""075约束、空历史模式和RLS安装状态。往返由CI执行。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_account_group_schema_state(session):
    rows = (
        await session.execute(
            text(
                "SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('blogger_account_group','blogger_account_member','blogger_account_quote')"
            )
        )
    ).all()
    assert len(rows) == 3 and all(enabled and forced for _, enabled, forced in rows)
    indexes = (
        await session.execute(
            text(
                "SELECT indexname,indexdef FROM pg_indexes WHERE indexname IN ('uq_account_member_active','uq_platform_post','uq_platform_post_account')"
            )
        )
    ).all()
    assert len(indexes) == 3
    by_name = dict(indexes)
    assert "WHERE is_active" in by_name["uq_account_member_active"]
    assert "account_id IS NULL" in by_name["uq_platform_post"]
    assert "account_id IS NOT NULL" in by_name["uq_platform_post_account"]
    columns = (
        await session.execute(
            text(
                "SELECT table_name,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND column_name='account_snapshot' AND table_name IN ('negotiation','promotion')"
            )
        )
    ).all()
    assert len(columns) == 2 and all(
        nullable == "YES" and default is None for _, nullable, default in columns
    )
