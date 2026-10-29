"""PR9数据库契约；回填/往返由PG18隔离演练覆盖。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_content_column_and_platform_post_rls(session):
    col = (
        await session.execute(
            text(
                "SELECT character_maximum_length,is_nullable,column_default FROM information_schema.columns WHERE table_name='promotion' AND column_name='content_category'"
            )
        )
    ).one()
    assert tuple(col) == (8, "YES", None)
    rls = (
        await session.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='promotion_platform_post'::regclass"
            )
        )
    ).one()
    assert tuple(rls) == (True, True)
    assert (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_policies WHERE tablename='promotion_platform_post' AND policyname='tenant_isolation' AND 'clothing_app'=ANY(roles)"
            )
        )
    ).scalar_one() == 1
    assert (
        await session.execute(
            text(
                "SELECT has_table_privilege('clothing_app','promotion_platform_post','SELECT,INSERT,UPDATE,DELETE')"
            )
        )
    ).scalar_one()
    index = (
        await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='uq_platform_post'")
        )
    ).scalar_one()
    assert "UNIQUE" in index and "(tenant_id, promotion_id, platform)" in index
    checks = (
        (
            await session.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='promotion_platform_post'::regclass AND contype='c'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any("like_count >= 0" in check for check in checks)
