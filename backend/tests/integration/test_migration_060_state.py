"""迁移 060_promotion_shipping 在 head 状态下的库结构与 seed（流程线设计 2.2、4.5）。

往返与存量演练（JSONB 搬迁、超长值、个人授权）按设计 9.4 在测试库上手工跑，结果写报告，不进仓库。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import (
    DEFAULT_ROLES,
    PROMOTION_SHIP_EXPORT,
    PROMOTION_SHIP_FILL,
    PROMOTION_SHIP_PUSH,
    PROMOTION_WAREHOUSE_WRITE_LEGACY,
)
from app.modules.promotion.enums import ShipCourier, ShipStatus
from app.modules.promotion.models import Promotion, PromotionItem

# 设计 2.2：9 列都可空、无默认值
_NEW_COLUMNS: dict[str, tuple[str, int | None]] = {
    "receiver_name": ("character varying", 32),
    "receiver_phone": ("character varying", 32),
    "receiver_address": ("character varying", 255),
    "ship_status": ("character varying", 8),
    "ship_pushed_at": ("timestamp with time zone", None),
    "ship_pushed_by": ("uuid", None),
    "ship_courier": ("character varying", 16),
    "ship_waybill": ("character varying", 128),
    "shipped_at": ("timestamp with time zone", None),
}

_FUNCTION_SCOPES = {
    PROMOTION_SHIP_PUSH: {"pr_manager"},
    PROMOTION_SHIP_FILL: {"warehouse"},
    PROMOTION_SHIP_EXPORT: {"warehouse"},
}

_FIELD_SCOPES = {
    f"field.promotion.receiver_{f}:{a}"
    for f in ("name", "phone", "address")
    for a in ("read", "write")
}


async def _indexes(session: AsyncSession, table: str) -> dict[str, str]:
    return dict(
        (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = CAST(:t AS text)"
                ),
                {"t": table},
            )
        )
        .tuples()
        .all()
    )


async def _constraints(session: AsyncSession, table: str, contype: str) -> dict[str, str]:
    return dict(
        (
            await session.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = CAST(:t AS regclass) AND CAST(contype AS text) = CAST(:c AS text)"
                ),
                {"t": table, "c": contype},
            )
        )
        .tuples()
        .all()
    )


async def _roles_for(session: AsyncSession, scope: str) -> set[str]:
    return set(
        (
            await session.execute(
                text(
                    "SELECT r.code FROM role_permission rp "
                    "JOIN role r ON r.id = rp.role_id "
                    "JOIN permission p ON p.id = rp.permission_id "
                    "WHERE p.scope = CAST(:s AS text)"
                ),
                {"s": scope},
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration060PromotionColumns:
    async def test_new_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, data_type, character_maximum_length, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'promotion' "
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

    async def test_ship_status_check_real_name(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "promotion", "c")
        # 命名约定给 ck 套前缀：落库实名是 ck_promotion_ck_promotion_ship_status
        definition = checks["ck_promotion_ck_promotion_ship_status"]
        for value in ShipStatus:
            assert f"'{value.value}'" in definition
        assert "ship_status IS NULL" in definition
        # 快递公司不加 CHECK（增减不用 DDL）
        assert not any("ship_courier" in d for d in checks.values())

    async def test_ship_pushed_by_fk_set_null(self, session: AsyncSession) -> None:
        fks = await _constraints(session, "promotion", "f")
        hits = [d for d in fks.values() if d.startswith("FOREIGN KEY (ship_pushed_by)")]
        assert hits == ['FOREIGN KEY (ship_pushed_by) REFERENCES "user"(id) ON DELETE SET NULL']

    async def test_indexes_rebuilt(self, session: AsyncSession) -> None:
        indexes = await _indexes(session, "promotion")
        assert "idx_promotion_print_address" not in indexes
        assert "idx_promotion_waybill" not in indexes
        idx = indexes["idx_promotion_ship_status"]
        assert "(tenant_id, ship_status, cooperation_date DESC, created_at DESC)" in idx
        assert "WHERE (ship_status IS NOT NULL)" in idx


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration060PromotionItem:
    async def test_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, data_type, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'promotion_item'"
                )
            )
        ).all()
        got = {r.column_name: r for r in rows}
        assert set(got) == {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
            "promotion_id",
            "style_id",
            "sku_id",
            "sort_order",
        }
        for name in ("promotion_id", "style_id", "sku_id", "sort_order"):
            assert got[name].is_nullable == "NO", name
        assert got["sort_order"].data_type == "smallint"
        assert got["sort_order"].column_default == "0"

    async def test_foreign_keys_restrict(self, session: AsyncSession) -> None:
        fks = sorted((await _constraints(session, "promotion_item", "f")).values())
        assert fks == [
            "FOREIGN KEY (promotion_id) REFERENCES promotion(id) ON DELETE RESTRICT",
            "FOREIGN KEY (sku_id) REFERENCES sku(id) ON DELETE RESTRICT",
            "FOREIGN KEY (style_id) REFERENCES style(id) ON DELETE RESTRICT",
            "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
        ]

    async def test_indexes(self, session: AsyncSession) -> None:
        indexes = await _indexes(session, "promotion_item")
        uq = indexes["uq_promotion_item_style"]
        assert uq.startswith("CREATE UNIQUE INDEX")
        assert "(tenant_id, promotion_id, style_id)" in uq
        assert "(tenant_id, sku_id)" in indexes["idx_promotion_item_sku"]
        assert not indexes["idx_promotion_item_sku"].startswith("CREATE UNIQUE")


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration060Seed:
    async def test_function_scopes_registered_and_granted(self, session: AsyncSession) -> None:
        for scope, roles in _FUNCTION_SCOPES.items():
            rows = (
                await session.execute(
                    text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                    {"s": scope},
                )
            ).all()
            assert [r.category for r in rows] == ["function"], scope
            in_db = await _roles_for(session, scope)
            assert in_db == roles, scope
            assert in_db == {r.code for r in DEFAULT_ROLES if scope in r.permissions}, scope

    async def test_field_scopes_registered_not_granted(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT scope, category FROM permission "
                    "WHERE scope LIKE 'field.promotion.receiver\\_%'"
                )
            )
        ).all()
        assert {r.scope for r in rows} == _FIELD_SCOPES
        assert {r.category for r in rows} == {"field"}
        for scope in _FIELD_SCOPES:
            assert await _roles_for(session, scope) == set(), scope

    async def test_warehouse_scopes_exact(self, session: AsyncSession) -> None:
        """仓库收回 promotion:read，只剩发货两条 + 回滚用的旧回填 scope。"""
        scopes = set(
            (
                await session.execute(
                    text(
                        "SELECT p.scope FROM role_permission rp "
                        "JOIN role r ON r.id = rp.role_id "
                        "JOIN permission p ON p.id = rp.permission_id "
                        "WHERE r.code = 'warehouse'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert scopes == {
            PROMOTION_SHIP_FILL,
            PROMOTION_SHIP_EXPORT,
            PROMOTION_WAREHOUSE_WRITE_LEGACY,
        }
        warehouse = next(r for r in DEFAULT_ROLES if r.code == "warehouse")
        assert scopes == set(warehouse.permissions)
        # promotion:read 本身还在册（下行补授要用）
        n = (
            await session.execute(
                text("SELECT count(*) FROM permission WHERE scope = 'promotion:read'")
            )
        ).scalar_one()
        assert n == 1


class TestPromotionOrmMatchesMigration060:
    def test_promotion_columns(self) -> None:
        cols = Promotion.__table__.columns
        for name, (_dtype, length) in _NEW_COLUMNS.items():
            assert cols[name].nullable is True, name
            assert cols[name].server_default is None, name
            if length is not None:
                assert cols[name].type.length == length, name  # type: ignore[attr-defined]

    def test_promotion_index_and_check(self) -> None:
        idx = {i.name: i for i in Promotion.__table__.indexes}
        assert "idx_promotion_ship_status" in idx
        assert str(idx["idx_promotion_ship_status"].dialect_options["postgresql"]["where"]) == (
            "ship_status IS NOT NULL"
        )
        # ORM 侧的名字已套上命名约定，与库里实名相同
        names = {str(c.name) for c in Promotion.__table__.constraints}
        assert "ck_promotion_ck_promotion_ship_status" in names

    def test_promotion_item(self) -> None:
        table = PromotionItem.__table__
        assert table.name == "promotion_item"
        idx = {i.name: i for i in table.indexes}
        assert idx["uq_promotion_item_style"].unique is True
        assert [c.name for c in idx["uq_promotion_item_style"].columns] == [
            "tenant_id",
            "promotion_id",
            "style_id",
        ]
        assert [c.name for c in idx["idx_promotion_item_sku"].columns] == ["tenant_id", "sku_id"]
        for name in ("promotion_id", "style_id", "sku_id"):
            fk = next(iter(table.columns[name].foreign_keys))
            assert fk.ondelete == "RESTRICT", name

    def test_enums(self) -> None:
        assert [s.value for s in ShipStatus] == ["待发货", "待打单", "已发货"]
        assert [c.value for c in ShipCourier] == [
            "顺丰",
            "中通",
            "圆通",
            "韵达",
            "申通",
            "极兔",
            "邮政",
            "京东",
            "德邦",
            "其他",
        ]
