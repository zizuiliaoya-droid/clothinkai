"""阅读游标RLS、索引与站内汇总唯一约束。"""

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_comment_read_schema_and_daily_summary_unique(session):
    flags = (
        await session.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='line_comment_read'::regclass"
            )
        )
    ).one()
    assert tuple(flags) == (True, True)
    assert (
        await session.execute(
            text(
                "SELECT has_table_privilege('clothing_app','line_comment_read','SELECT,INSERT,UPDATE,DELETE')"
            )
        )
    ).scalar_one()
    for name, keys in (
        ("uq_line_comment_read", "(tenant_id, user_id, line_id)"),
        ("uq_notification_daily_summary", "(tenant_id, user_id, type, summary_date)"),
    ):
        definition = (
            await session.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname=:name"), {"name": name}
            )
        ).scalar_one()
        assert "UNIQUE" in definition and keys in definition
    assert (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='notification' AND column_name IN ('line_id','event_seq','summary_date')"
            )
        )
    ).scalar_one() == 3
