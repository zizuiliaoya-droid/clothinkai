"""迁移 065_settlement_flow 在 head 状态下的库结构与 seed（流程线设计 2.5 M4、4.3、4.5）。

往返与存量演练（下行把已结款折回已付款、结款驳回折回待付款，个人授权一并删除；生产形态的结款单
与推广单上行）按设计 9.4 在测试库上手工跑（drill065），结果写报告，不进仓库。
scope 角色集合与 ``DEFAULT_ROLES`` 的一致性、新表 RLS 在 test_flow_rls_schema.py。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import ALLOWED_PURPOSES
from app.modules.auth.default_roles import DEFAULT_ROLES, PROMOTION_REVIEW, PROMOTION_REVIEW_APPROVE
from app.modules.finance.models import Settlement
from app.modules.promotion.models import Promotion

# 列 → (data_type, is_nullable)
_PROMOTION_NEW_COLUMNS: dict[str, tuple[str, str]] = {
    "settlement_chat_attachment_id": ("uuid", "YES"),
    "settled_at": ("timestamp with time zone", "YES"),
    "recall_waybill_attachment_id": ("uuid", "YES"),
    "return_shipping_payer": ("character varying", "YES"),
}

_FREIGHT_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("uuid", "NO"),
    "tenant_id": ("uuid", "NO"),
    "created_at": ("timestamp with time zone", "NO"),
    "updated_at": ("timestamp with time zone", "NO"),
    "promotion_id": ("uuid", "NO"),
    "amount": ("numeric", "NO"),
    "status": ("character varying", "NO"),
    "payment_qr_attachment_id": ("uuid", "NO"),
    "waybill_attachment_id": ("uuid", "NO"),
    "submitted_by": ("uuid", "YES"),
    "submitted_at": ("timestamp with time zone", "NO"),
    "reviewed_by": ("uuid", "YES"),
    "reviewed_at": ("timestamp with time zone", "YES"),
    "reject_reason": ("text", "YES"),
    "rejected_by": ("uuid", "YES"),
    "rejected_at": ("timestamp with time zone", "YES"),
    "payment_date": ("date", "YES"),
    "payment_proof_attachment_id": ("uuid", "YES"),
    "paid_by": ("uuid", "YES"),
    "paid_at": ("timestamp with time zone", "YES"),
    "remark": ("text", "YES"),
}

_FIELD_SCOPES = {
    "field.promotion.payment_qr:read",
    "field.promotion.payment_qr:write",
    "field.settlement.payment_adjust_note:read",
    "field.settlement.payment_adjust_note:write",
}


async def _columns(session: AsyncSession, table: str) -> dict[str, tuple[str, str]]:
    rows = (
        await session.execute(
            text(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = CAST(:t AS text)"
            ),
            {"t": table},
        )
    ).all()
    return {r.column_name: (r.data_type, r.is_nullable) for r in rows}


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


async def _indexes(session: AsyncSession, table: str) -> dict[str, str]:
    return dict(
        (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND tablename = CAST(:t AS text)"
                ),
                {"t": table},
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
class TestMigration065Promotion:
    async def test_new_columns(self, session: AsyncSession) -> None:
        cols = await _columns(session, "promotion")
        assert {k: cols[k] for k in _PROMOTION_NEW_COLUMNS} == _PROMOTION_NEW_COLUMNS
        width = (
            await session.execute(
                text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name = 'promotion' AND column_name = 'return_shipping_payer'"
                )
            )
        ).scalar_one()
        assert width == 8

    async def test_attachment_fks_restrict(self, session: AsyncSession) -> None:
        fks = set((await _constraints(session, "promotion", "f")).values())
        for col in ("settlement_chat_attachment_id", "recall_waybill_attachment_id"):
            assert f"FOREIGN KEY ({col}) REFERENCES attachment(id) ON DELETE RESTRICT" in fks, col

    async def test_payer_check_real_name(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "promotion", "c")
        payer = checks["ck_promotion_ck_promotion_return_shipping_payer"]
        assert "return_shipping_payer IS NULL" in payer
        assert "'博主垫付'" in payer
        assert "'到付'" in payer
        # 推广单 settlement_status 仍没有 CHECK：新值「已结款」「结款驳回」不用 DDL
        assert not any("settlement_status" in d for d in checks.values())


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration065Settlement:
    async def test_adjust_note_column_without_check(self, session: AsyncSession) -> None:
        cols = await _columns(session, "settlement")
        assert cols["payment_adjust_note"] == ("text", "YES")
        # 「实付 ≠ 应付必须写说明」只在 service：存量已付款单有实付 ≠ 应付的，CHECK 会让迁移失败
        checks = await _constraints(session, "settlement", "c")
        assert not any("payment_adjust_note" in d for d in checks.values())


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration065ReturnFreightPayment:
    async def test_columns(self, session: AsyncSession) -> None:
        assert await _columns(session, "return_freight_payment") == _FREIGHT_COLUMNS
        row = (
            await session.execute(
                text(
                    "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
                    "WHERE table_name = 'return_freight_payment' AND column_name = 'amount'"
                )
            )
        ).one()
        assert (row.numeric_precision, row.numeric_scale) == (10, 2)
        default = (
            await session.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'return_freight_payment' AND column_name = 'status'"
                )
            )
        ).scalar_one()
        assert default.startswith("'待审核'")

    async def test_foreign_keys(self, session: AsyncSession) -> None:
        fks = sorted((await _constraints(session, "return_freight_payment", "f")).values())
        assert fks == [
            'FOREIGN KEY (paid_by) REFERENCES "user"(id) ON DELETE SET NULL',
            "FOREIGN KEY (payment_proof_attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT",
            "FOREIGN KEY (payment_qr_attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT",
            "FOREIGN KEY (promotion_id) REFERENCES promotion(id) ON DELETE RESTRICT",
            'FOREIGN KEY (rejected_by) REFERENCES "user"(id) ON DELETE SET NULL',
            'FOREIGN KEY (reviewed_by) REFERENCES "user"(id) ON DELETE SET NULL',
            'FOREIGN KEY (submitted_by) REFERENCES "user"(id) ON DELETE SET NULL',
            "FOREIGN KEY (tenant_id) REFERENCES tenant(id) ON DELETE RESTRICT",
            "FOREIGN KEY (waybill_attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT",
        ]

    async def test_checks_real_names(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "return_freight_payment", "c")
        prefix = "ck_return_freight_payment_ck_return_freight_"
        assert set(checks) == {f"{prefix}amount_pos", f"{prefix}status", f"{prefix}paid_fields"}
        assert "amount > " in checks[f"{prefix}amount_pos"]
        for value in ("待审核", "待付款", "已付款", "已驳回"):
            assert f"'{value}'" in checks[f"{prefix}status"], value
        paid = checks[f"{prefix}paid_fields"]
        assert "'已付款'" in paid
        assert "payment_date IS NOT NULL" in paid
        assert "payment_proof_attachment_id IS NOT NULL" in paid

    async def test_indexes(self, session: AsyncSession) -> None:
        indexes = await _indexes(session, "return_freight_payment")
        uq = indexes["uq_return_freight_promotion"]
        assert uq.startswith("CREATE UNIQUE INDEX")
        assert "(tenant_id, promotion_id)" in uq
        status = indexes["idx_return_freight_status"]
        assert not status.startswith("CREATE UNIQUE")
        assert "(tenant_id, status, submitted_at DESC)" in status

    async def test_empty(self, session: AsyncSession) -> None:
        n = (
            await session.execute(text("SELECT count(*) FROM return_freight_payment"))
        ).scalar_one()
        assert n == 0


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration065Seed:
    async def test_review_approve_only_pr_manager(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                {"s": PROMOTION_REVIEW_APPROVE},
            )
        ).all()
        assert [r.category for r in rows] == ["function"]
        in_db = await _roles_for(session, PROMOTION_REVIEW_APPROVE)
        assert in_db == {"pr_manager"}
        assert in_db == {r.code for r in DEFAULT_ROLES if PROMOTION_REVIEW_APPROVE in r.permissions}

    async def test_old_review_scope_kept_for_rollback(self, session: AsyncSession) -> None:
        # 旧 scope 不删（下行要用），授权照旧
        assert "pr_manager" in await _roles_for(session, PROMOTION_REVIEW)

    async def test_field_scopes_registered_not_granted(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT scope, category FROM permission "
                    "WHERE scope = ANY(CAST(:scopes AS text[]))"
                ),
                {"scopes": sorted(_FIELD_SCOPES)},
            )
        ).all()
        assert {r.scope for r in rows} == _FIELD_SCOPES
        assert {r.category for r in rows} == {"field"}
        for scope in _FIELD_SCOPES:
            assert await _roles_for(session, scope) == set(), scope


class TestOrmMatchesMigration065:
    def test_promotion_columns(self) -> None:
        cols = Promotion.__table__.columns
        for name in _PROMOTION_NEW_COLUMNS:
            assert cols[name].nullable is True, name
            assert cols[name].server_default is None, name
        assert cols["return_shipping_payer"].type.length == 8  # type: ignore[attr-defined]
        for name in ("settlement_chat_attachment_id", "recall_waybill_attachment_id"):
            fk = next(iter(cols[name].foreign_keys))
            assert (fk.column.table.name, fk.ondelete) == ("attachment", "RESTRICT"), name

    def test_promotion_payer_check(self) -> None:
        by_name = {str(c.name): c for c in Promotion.__table__.constraints}
        payer = str(by_name["ck_promotion_ck_promotion_return_shipping_payer"].sqltext)  # type: ignore[attr-defined]
        assert "'博主垫付'" in payer
        assert "'到付'" in payer

    def test_settlement_adjust_note(self) -> None:
        col = Settlement.__table__.columns["payment_adjust_note"]
        assert col.nullable is True
        assert col.server_default is None

    def test_purposes(self) -> None:
        assert "settlement_chat_screenshot" in ALLOWED_PURPOSES
        assert "recall_waybill_screenshot" in ALLOWED_PURPOSES
