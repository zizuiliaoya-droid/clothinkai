"""迁移 059_8b_blogger_library 在 head 状态下的库结构（往返另见 ciwork roundtrip.sh）。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import BLOGGER_TAG_WRITE, DEFAULT_ROLES
from app.modules.blogger.models import Blogger

# 设计 §4.1 + D3：四个新列（均可空、无默认）
_NEW_COLUMNS = {
    "web_id": ("character varying", 64),
    "homepage_url": ("character varying", 1024),
    "platform_metrics": ("jsonb", None),
    "quote_note": ("character varying", 500),
}


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration059Blogger:
    async def _indexes(self, session: AsyncSession) -> dict[str, str]:
        return dict(
            (
                await session.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'blogger'")
                )
            )
            .tuples()
            .all()
        )

    async def test_unique_index_has_platform(self, session: AsyncSession) -> None:
        indexes = await self._indexes(session)
        assert "uq_blogger_xiaohongshu_id" not in indexes
        idx = indexes["uq_blogger_platform_account"]
        assert idx.startswith("CREATE UNIQUE INDEX")
        assert "(tenant_id, platform, xiaohongshu_id)" in idx
        assert "WHERE (is_deleted = false)" in idx

    async def test_new_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, data_type, character_maximum_length, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'blogger' "
                    "AND column_name = ANY(:cols)"
                ),
                {"cols": list(_NEW_COLUMNS)},
            )
        ).all()
        got = {r.column_name: r for r in rows}
        assert set(got) == set(_NEW_COLUMNS)
        for name, (dtype, length) in _NEW_COLUMNS.items():
            r = got[name]
            assert (r.is_nullable, r.data_type, r.character_maximum_length) == (
                "YES",
                dtype,
                length,
            ), name
            assert r.column_default is None, name

    async def test_tag_scope_registered_and_granted_only_to_pr_manager(
        self, session: AsyncSession
    ) -> None:
        rows = (
            await session.execute(
                text("SELECT name, category FROM permission WHERE scope = :s"),
                {"s": BLOGGER_TAG_WRITE},
            )
        ).all()
        assert [(r.name, r.category) for r in rows] == [("维护博主标签字典", "function")]
        roles = set(
            (
                await session.execute(
                    text(
                        "SELECT r.code FROM role_permission rp "
                        "JOIN role r ON r.id = rp.role_id "
                        "JOIN permission p ON p.id = rp.permission_id "
                        "WHERE p.scope = :s"
                    ),
                    {"s": BLOGGER_TAG_WRITE},
                )
            )
            .scalars()
            .all()
        )
        assert roles == {"pr_manager"}
        assert roles == {r.code for r in DEFAULT_ROLES if BLOGGER_TAG_WRITE in r.permissions}


class TestBloggerOrmMatchesMigration059:
    """ORM 声明与迁移一致（``upsert_atomic`` 的 ON CONFLICT 靠它找到索引）。"""

    def test_orm_declares_same_index(self) -> None:
        idx = {i.name: i for i in Blogger.__table__.indexes}
        assert "uq_blogger_xiaohongshu_id" not in idx
        new = idx["uq_blogger_platform_account"]
        assert new.unique is True
        assert [c.name for c in new.columns] == ["tenant_id", "platform", "xiaohongshu_id"]
        assert str(new.dialect_options["postgresql"]["where"]) == "is_deleted = false"

    def test_orm_columns(self) -> None:
        cols = Blogger.__table__.columns
        for name in _NEW_COLUMNS:
            assert cols[name].nullable is True, name
        assert cols["web_id"].type.length == 64
        assert cols["homepage_url"].type.length == 1024
        assert cols["quote_note"].type.length == 500
        # JSON null 存成 SQL NULL，「没有指标」不会写成 'null'::jsonb
        assert cols["platform_metrics"].type.none_as_null is True
