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
