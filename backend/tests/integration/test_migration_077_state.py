"""077 只扩状态约束，保留原RLS、复制来源及主线唯一性。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_cancelled_status_and_original_isolation_preserved(session):
    check = await session.scalar(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_negotiation_ck_negotiation_status'"
        )
    )
    assert all(
        state in check
        for state in (
            "草稿",
            "主管待审",
            "审核驳回",
            "待老板审核",
            "老板退回",
            "待补收货地址",
            "已定稿",
            "已取消",
        )
    )
    assert (
        await session.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname='negotiation'"
            )
        )
    ).one() == (True, True)
    assert "UNIQUE" in await session.scalar(
        text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_negotiation_line'")
    )
