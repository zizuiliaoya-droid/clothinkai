"""迁移 057_8a_goods_master_data 在 head 状态下的库结构与数据（往返另见 ciwork roundtrip.sh）。

本文件先放 8a-7 的权限部分（AC 59）；后续 8a 各项（类目可空、导入表、外部图片链接）往这里加。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration057Permissions:
    async def test_operations_has_exactly_one_product_all(self, session: AsyncSession) -> None:
        n = (
            await session.execute(
                text(
                    "SELECT count(*) FROM role_permission rp "
                    "JOIN role r ON r.id = rp.role_id "
                    "JOIN permission p ON p.id = rp.permission_id "
                    "WHERE r.code = 'operations' AND p.scope = 'product.*:*'"
                )
            )
        ).scalar_one()
        assert n == 1

    async def test_product_import_write_registered(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text("SELECT name, category FROM permission WHERE scope = 'product.import:write'")
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].category == "function"
        assert rows[0].name == "导入商品资料、配置其字段映射、处理其导入冲突"

    async def test_product_all_permission_row_unique(self, session: AsyncSession) -> None:
        n = (
            await session.execute(
                text("SELECT count(*) FROM permission WHERE scope = 'product.*:*'")
            )
        ).scalar_one()
        assert n == 1

    async def test_db_operations_product_scopes_match_default_roles(
        self, session: AsyncSession
    ) -> None:
        """AC 59：库里运营的 product 相关授权与 default_roles.py 一致。"""
        scopes = set(
            (
                await session.execute(
                    text(
                        "SELECT p.scope FROM role_permission rp "
                        "JOIN role r ON r.id = rp.role_id "
                        "JOIN permission p ON p.id = rp.permission_id "
                        "WHERE r.code = 'operations' AND p.scope LIKE 'product%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        ops = next(r for r in DEFAULT_ROLES if r.code == "operations")
        assert scopes == {s for s in ops.permissions if s.startswith("product")}


_BATCH_COUNTS = ("filled", "skipped", "conflicted", "warning_count", "filled_objects")


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration057ImportTables:
    """8a-6：导入表（约束按定义查，不按名字——落库名取决于命名约定）。"""

    async def _checks(self, session: AsyncSession, table: str) -> list[str]:
        return list(
            (
                await session.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = CAST(:t AS regclass) AND contype = 'c'"
                    ),
                    {"t": table},
                )
            )
            .scalars()
            .all()
        )

    async def test_style_external_image_url(self, session: AsyncSession) -> None:
        """8a-4：style.external_image_url VARCHAR(1024) NULL（§3.1）。"""
        row = (
            await session.execute(
                text(
                    "SELECT is_nullable, data_type, character_maximum_length "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND table_name = 'style' AND column_name = 'external_image_url'"
                )
            )
        ).one()
        assert (row.is_nullable, row.data_type, row.character_maximum_length) == (
            "YES",
            "character varying",
            1024,
        )

    async def test_import_batch_count_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, column_default, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'import_batch' "
                    "AND column_name = ANY(:cols)"
                ),
                {"cols": list(_BATCH_COUNTS)},
            )
        ).all()
        assert {r.column_name for r in rows} == set(_BATCH_COUNTS)
        for r in rows:
            assert r.is_nullable == "NO"
            assert r.data_type == "integer"
            assert r.column_default == "0"
        defs = [d for d in await self._checks(session, "import_batch") if "filled_objects" in d]
        assert len(defs) == 1
        for col in _BATCH_COUNTS:
            assert f"({col} >= 0)" in defs[0]

    async def test_import_job_status_check_and_notes(self, session: AsyncSession) -> None:
        defs = [d for d in await self._checks(session, "import_job") if "status" in d]
        assert len(defs) == 1
        for value in ("success", "failed", "filled", "skipped", "conflict"):
            assert f"'{value}'" in defs[0]
        col = (
            await session.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'import_job' "
                    "AND column_name = 'notes'"
                )
            )
        ).one()
        assert (col.data_type, col.is_nullable) == ("jsonb", "YES")

    async def test_import_conflict_table(self, session: AsyncSession) -> None:
        indexes = dict(
            (
                await session.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'import_conflict'"
                    )
                )
            )
            .tuples()
            .all()
        )
        pending = indexes["uq_import_conflict_pending"]
        assert pending.startswith("CREATE UNIQUE INDEX")
        assert "(tenant_id, source, object_type, object_id)" in pending
        assert "WHERE ((status)::text = 'pending'::text)" in pending
        assert "idx_import_conflict_list" in indexes
        assert "idx_import_conflict_batch" in indexes

        defs = await self._checks(session, "import_conflict")
        assert any("resolved_at IS NULL" in d for d in defs)
        assert any("'blogger'" in d and "'style'" in d for d in defs)
        assert any("'superseded'" in d and "'invalid'" in d for d in defs)

        rls = (
            await session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'import_conflict'"
                )
            )
        ).one()
        assert rls.relrowsecurity is True
        assert rls.relforcerowsecurity is True

        fks = list(
            (
                await session.execute(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid = 'import_conflict'::regclass AND contype = 'f'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any("REFERENCES import_batch(id) ON DELETE SET NULL" in d for d in fks)
        # object_id 多态、superseded_by 不设外键
        assert not any("(object_id)" in d or "(superseded_by)" in d for d in fks)
