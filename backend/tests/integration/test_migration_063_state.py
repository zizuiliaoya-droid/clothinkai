"""迁移 061_negotiation_two_level 在 head 状态下的库结构与 seed（流程线设计 2.3、4.5）。

往返与存量演练（同义改名、line_id 继承、下行折态与有损行数、个人授权）按设计 9.4 在测试库上手工跑，
结果写报告，不进仓库。RLS 与 clothing_app 权限断言在 test_flow_rls_schema.py。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import (
    DEFAULT_ROLES,
    NEGOTIATION_ALL_READ,
    NEGOTIATION_FINAL_APPROVE,
)
from app.modules.flow.models import LineEvent
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.models import Negotiation, NegotiationItem
from app.modules.promotion.models import Promotion

_STATUSES = ("草稿", "主管待审", "审核驳回", "待老板审核", "老板退回", "待补收货地址", "已定稿")

# 设计 2.3：9 列都可空、无默认值
_NEGOTIATION_NEW_COLUMNS: dict[str, tuple[str, int | None]] = {
    "final_reviewed_by": ("uuid", None),
    "final_reviewed_at": ("timestamp with time zone", None),
    "final_review_opinion": ("text", None),
    "receiver_name": ("character varying", 32),
    "receiver_phone": ("character varying", 32),
    "receiver_address": ("character varying", 255),
    "receiver_confirmed_at": ("timestamp with time zone", None),
    "receiver_confirmed_by": ("uuid", None),
    "copied_from_id": ("uuid", None),
}

_LINE_EVENT_COLUMNS: dict[str, tuple[str, str]] = {
    # 列 -> (data_type, is_nullable)
    "id": ("uuid", "NO"),
    "tenant_id": ("uuid", "NO"),
    "created_at": ("timestamp with time zone", "NO"),
    "updated_at": ("timestamp with time zone", "NO"),
    "seq": ("bigint", "NO"),
    "line_id": ("uuid", "NO"),
    "negotiation_id": ("uuid", "YES"),
    "promotion_id": ("uuid", "YES"),
    "event_type": ("character varying", "NO"),
    "from_state": ("character varying", "YES"),
    "to_state": ("character varying", "YES"),
    "note": ("text", "YES"),
    "attachment_id": ("uuid", "YES"),
    "payload": ("jsonb", "NO"),
    "actor_id": ("uuid", "YES"),
}

_FIELD_SCOPES = {
    f"field.line_event.{k}:read"
    for k in ("negotiation", "comment", "promotion", "shipping", "settlement", "retro")
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
class TestMigration061Negotiation:
    async def test_new_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, data_type, character_maximum_length, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'negotiation' "
                    "AND column_name = ANY(:cols)"
                ),
                {"cols": list(_NEGOTIATION_NEW_COLUMNS)},
            )
        ).all()
        got = {r.column_name: r for r in rows}
        assert set(got) == set(_NEGOTIATION_NEW_COLUMNS)
        for name, (dtype, length) in _NEGOTIATION_NEW_COLUMNS.items():
            r = got[name]
            assert (r.is_nullable, r.data_type, r.character_maximum_length) == (
                "YES",
                dtype,
                length,
            ), name
            assert r.column_default is None, name

    async def test_line_id_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, is_nullable, data_type, column_default "
                    "FROM information_schema.columns WHERE table_schema = 'public' "
                    "AND column_name = 'line_id' AND table_name IN ('negotiation', 'promotion')"
                )
            )
        ).all()
        got = {r.table_name: r for r in rows}
        assert set(got) == {"negotiation", "promotion"}
        for r in got.values():
            assert (r.is_nullable, r.data_type, r.column_default) == (
                "NO",
                "uuid",
                "gen_random_uuid()",
            ), r.table_name

    async def test_line_unique_indexes(self, session: AsyncSession) -> None:
        for table in ("negotiation", "promotion"):
            idx = (await _indexes(session, table))[f"uq_{table}_line"]
            assert idx.startswith("CREATE UNIQUE INDEX"), table
            assert "(tenant_id, line_id)" in idx, table

    async def test_status_checks_real_names(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "negotiation", "c")
        # 命名约定给 ck 套前缀：落库实名是 ck_negotiation_ck_negotiation_xxx
        status = checks["ck_negotiation_ck_negotiation_status"]
        for value in _STATUSES:
            assert f"'{value}'" in status, value
        for old in ("待审核", "审核通过"):
            assert f"'{old}'" not in status, old
        finalized = checks["ck_negotiation_ck_negotiation_finalized_has_promotion"]
        assert "'已定稿'" in finalized
        assert "promotion_id IS NOT NULL" in finalized
        assert "ck_negotiation_ck_negotiation_approved_has_promotion" not in checks
        # 合作模式、服务费 ≥ 0 两个 CHECK 不动
        assert "ck_negotiation_ck_negotiation_cooperation_mode" in checks
        assert "ck_negotiation_ck_negotiation_quote_amount_nonneg" in checks

    async def test_new_foreign_keys(self, session: AsyncSession) -> None:
        fks = set((await _constraints(session, "negotiation", "f")).values())
        for col in ("final_reviewed_by", "receiver_confirmed_by"):
            assert f'FOREIGN KEY ({col}) REFERENCES "user"(id) ON DELETE SET NULL' in fks, col
        assert "FOREIGN KEY (copied_from_id) REFERENCES negotiation(id) ON DELETE SET NULL" in fks


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration061LineEvent:
    async def test_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, data_type, is_nullable, is_identity, identity_generation, "
                    "column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'line_event'"
                )
            )
        ).all()
        got = {r.column_name: r for r in rows}
        assert {k: (r.data_type, r.is_nullable) for k, r in got.items()} == _LINE_EVENT_COLUMNS
        assert (got["seq"].is_identity, got["seq"].identity_generation) == ("YES", "ALWAYS")
        assert got["payload"].column_default == "'{}'::jsonb"

    async def test_owner_check_and_foreign_keys(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "line_event", "c")
        owner = checks["ck_line_event_ck_line_event_owner"]
        assert "negotiation_id IS NOT NULL" in owner
        assert "promotion_id IS NOT NULL" in owner
        # 事件类型不加 CHECK（加类型不用 DDL）
        assert not any("event_type" in d for d in checks.values())
        fks = sorted((await _constraints(session, "line_event", "f")).values())
        assert fks == [
            'FOREIGN KEY (actor_id) REFERENCES "user"(id) ON DELETE SET NULL',
            "FOREIGN KEY (attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT",
            "FOREIGN KEY (negotiation_id) REFERENCES negotiation(id) ON DELETE RESTRICT",
            "FOREIGN KEY (promotion_id) REFERENCES promotion(id) ON DELETE RESTRICT",
            "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
        ]

    async def test_line_index(self, session: AsyncSession) -> None:
        idx = (await _indexes(session, "line_event"))["idx_line_event_line"]
        assert "(tenant_id, line_id, seq)" in idx
        assert not idx.startswith("CREATE UNIQUE")


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration061NegotiationItem:
    async def test_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT column_name, is_nullable, data_type, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'negotiation_item'"
                )
            )
        ).all()
        got = {r.column_name: r for r in rows}
        assert set(got) == {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
            "negotiation_id",
            "style_id",
            "sku_id",
            "sort_order",
        }
        for name in ("negotiation_id", "style_id", "sort_order"):
            assert got[name].is_nullable == "NO", name
        # 草稿可以先不选颜色尺码
        assert got["sku_id"].is_nullable == "YES"
        assert got["sort_order"].data_type == "smallint"
        assert got["sort_order"].column_default == "0"

    async def test_foreign_keys_restrict(self, session: AsyncSession) -> None:
        fks = sorted((await _constraints(session, "negotiation_item", "f")).values())
        assert fks == [
            "FOREIGN KEY (negotiation_id) REFERENCES negotiation(id) ON DELETE RESTRICT",
            "FOREIGN KEY (sku_id) REFERENCES sku(id) ON DELETE RESTRICT",
            "FOREIGN KEY (style_id) REFERENCES style(id) ON DELETE RESTRICT",
            "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
        ]

    async def test_indexes(self, session: AsyncSession) -> None:
        indexes = await _indexes(session, "negotiation_item")
        uq = indexes["uq_negotiation_item_style"]
        assert uq.startswith("CREATE UNIQUE INDEX")
        assert "(tenant_id, negotiation_id, style_id)" in uq
        sku = indexes["idx_negotiation_item_sku"]
        assert not sku.startswith("CREATE UNIQUE")
        assert "(tenant_id, sku_id)" in sku
        assert "WHERE (sku_id IS NOT NULL)" in sku


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration061Seed:
    async def test_all_read_granted_like_default_roles(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                {"s": NEGOTIATION_ALL_READ},
            )
        ).all()
        assert [r.category for r in rows] == ["function"]
        in_db = await _roles_for(session, NEGOTIATION_ALL_READ)
        assert in_db == {"pr_manager", "finance", "operations"}
        assert in_db == {r.code for r in DEFAULT_ROLES if NEGOTIATION_ALL_READ in r.permissions}

    async def test_final_approve_registered_not_granted(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                {"s": NEGOTIATION_FINAL_APPROVE},
            )
        ).all()
        assert [r.category for r in rows] == ["function"]
        # 管理员靠 *，不绑任何角色；DEFAULT_ROLES 也没人显式持有
        assert await _roles_for(session, NEGOTIATION_FINAL_APPROVE) == set()
        assert not [r.code for r in DEFAULT_ROLES if NEGOTIATION_FINAL_APPROVE in r.permissions]

    async def test_field_scopes_registered_not_granted(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT scope, category FROM permission "
                    "WHERE scope LIKE 'field.line\\_event.%'"
                )
            )
        ).all()
        assert {r.scope for r in rows} == _FIELD_SCOPES
        assert {r.category for r in rows} == {"field"}
        for scope in _FIELD_SCOPES:
            assert await _roles_for(session, scope) == set(), scope


class TestOrmMatchesMigration061:
    def test_negotiation_columns(self) -> None:
        cols = Negotiation.__table__.columns
        for name, (_dtype, length) in _NEGOTIATION_NEW_COLUMNS.items():
            assert cols[name].nullable is True, name
            assert cols[name].server_default is None, name
            if length is not None:
                assert cols[name].type.length == length, name  # type: ignore[attr-defined]
        for model in (Negotiation, Promotion):
            line = model.__table__.columns["line_id"]
            assert line.nullable is False
            assert str(line.server_default.arg) == "gen_random_uuid()"  # type: ignore[union-attr]

    def test_check_and_index_names(self) -> None:
        # ORM 侧的名字已套上命名约定，与库里实名相同
        names = {str(c.name) for c in Negotiation.__table__.constraints}
        assert "ck_negotiation_ck_negotiation_status" in names
        assert "ck_negotiation_ck_negotiation_finalized_has_promotion" in names
        assert "ck_negotiation_ck_negotiation_approved_has_promotion" not in names
        status = next(
            c
            for c in Negotiation.__table__.constraints
            if c.name == "ck_negotiation_ck_negotiation_status"
        )
        for value in _STATUSES:
            assert f"'{value}'" in str(status.sqltext), value  # type: ignore[attr-defined]
        for model in (Negotiation, Promotion):
            idx = {i.name: i for i in model.__table__.indexes}
            uq = idx[f"uq_{model.__tablename__}_line"]
            assert uq.unique is True
            assert [c.name for c in uq.columns] == ["tenant_id", "line_id"]

    def test_negotiation_item(self) -> None:
        table = NegotiationItem.__table__
        assert table.name == "negotiation_item"
        assert table.columns["sku_id"].nullable is True
        idx = {i.name: i for i in table.indexes}
        assert idx["uq_negotiation_item_style"].unique is True
        assert [c.name for c in idx["uq_negotiation_item_style"].columns] == [
            "tenant_id",
            "negotiation_id",
            "style_id",
        ]
        sku = idx["idx_negotiation_item_sku"]
        assert [c.name for c in sku.columns] == ["tenant_id", "sku_id"]
        assert str(sku.dialect_options["postgresql"]["where"]) == "sku_id IS NOT NULL"
        for name in ("negotiation_id", "style_id", "sku_id"):
            fk = next(iter(table.columns[name].foreign_keys))
            assert fk.ondelete == "RESTRICT", name

    def test_line_event(self) -> None:
        table = LineEvent.__table__
        assert set(table.columns.keys()) == set(_LINE_EVENT_COLUMNS)
        assert table.columns["seq"].identity is not None
        assert table.columns["seq"].identity.always is True  # type: ignore[union-attr]
        names = {str(c.name) for c in table.constraints}
        assert "ck_line_event_ck_line_event_owner" in names
        idx = {i.name: i for i in table.indexes}
        assert [c.name for c in idx["idx_line_event_line"].columns] == [
            "tenant_id",
            "line_id",
            "seq",
        ]
        ondelete = {
            name: next(iter(table.columns[name].foreign_keys)).ondelete
            for name in ("negotiation_id", "promotion_id", "attachment_id", "actor_id")
        }
        assert ondelete == {
            "negotiation_id": "RESTRICT",
            "promotion_id": "RESTRICT",
            "attachment_id": "RESTRICT",
            "actor_id": "SET NULL",
        }

    def test_status_enum(self) -> None:
        assert tuple(s.value for s in NegotiationStatus) == _STATUSES
        # 旧成员名沿用（同义改名）
        assert NegotiationStatus.PENDING.value == "主管待审"
        assert NegotiationStatus.APPROVED.value == "已定稿"
