"""073接作品072：草稿空款与未知金额的落库契约。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_draft_nullable_columns_and_guards(session):
    assert (
        await session.execute(text("SELECT to_regclass('blogger_work_snapshot')"))
    ).scalar_one() is not None
    columns = (
        await session.execute(
            text("""
        SELECT column_name, is_nullable, column_default FROM information_schema.columns
        WHERE table_name='negotiation' AND column_name IN ('style_id','quote_amount')
    """)
        )
    ).all()
    assert {name for name, _, _ in columns} == {"style_id", "quote_amount"}
    assert all(nullable == "YES" and default is None for _, nullable, default in columns)
    names = (
        (
            await session.execute(
                text("""
        SELECT conname FROM pg_constraint WHERE conrelid='negotiation'::regclass
    """)
            )
        )
        .scalars()
        .all()
    )
    assert {
        "ck_negotiation_ck_negotiation_submitted_complete",
        "ck_negotiation_ck_negotiation_goods_requires_style",
    } <= set(names)
