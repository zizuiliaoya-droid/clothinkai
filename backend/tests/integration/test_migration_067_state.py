"""M5 head 的真实数据库列、部分索引、三态约束和权限种子。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_completed_column_partial_index_and_three_state_check(session: AsyncSession) -> None:
    row = (
        await session.execute(
            text(
                "SELECT data_type,is_nullable,column_default FROM information_schema.columns "
                "WHERE table_name='promotion' AND column_name='completed_at'"
            )
        )
    ).one()
    assert tuple(row) == ("timestamp with time zone", "YES", None)
    index = (
        await session.execute(
            text(
                "SELECT indexdef FROM pg_indexes WHERE tablename='promotion' "
                "AND indexname='idx_promotion_completed'"
            )
        )
    ).scalar_one()
    assert "(tenant_id, completed_at DESC)" in index
    assert "WHERE (completed_at IS NOT NULL)" in index
    check = (
        await session.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid='promotion'::regclass "
                "AND conname='ck_promotion_ck_promotion_retro_status'"
            )
        )
    ).scalar_one()
    assert "待确认" not in check
    assert all(f"'{v}'" in check for v in ("未开始", "待复盘", "已完成"))


async def test_retro_permission_seed_and_legacy_retained(session: AsyncSession) -> None:
    scopes = set(
        (
            await session.execute(
                text(
                    "SELECT scope FROM permission WHERE scope IN "
                    "('promotion_retro:write','promotion.retro:write','promotion.retro:confirm')"
                )
            )
        ).scalars()
    )
    assert scopes == {"promotion_retro:write", "promotion.retro:write", "promotion.retro:confirm"}
    roles = set(
        (
            await session.execute(
                text(
                    "SELECT r.code FROM role_permission rp JOIN role r ON r.id=rp.role_id "
                    "JOIN permission p ON p.id=rp.permission_id WHERE p.scope='promotion_retro:write'"
                )
            )
        ).scalars()
    )
    assert roles == {"pr_manager"}
    assert roles == {r.code for r in DEFAULT_ROLES if "promotion_retro:write" in r.permissions}
