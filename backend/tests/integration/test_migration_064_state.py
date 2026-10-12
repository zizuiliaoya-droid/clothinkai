"""迁移 064_urge_complete 在 head 状态下的库结构与 seed（流程线设计 2.4 M3、4.3、4.5）。

往返与存量演练（下行把「已完成」改记「手动关闭」、个人授权一并删除、生产形态 4 个任务上行）按设计 9.4
在测试库上手工跑，结果写报告，不进仓库。scope 角色集合与 ``DEFAULT_ROLES`` 的一致性在 test_flow_rls_schema.py。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import ALLOWED_PURPOSES
from app.modules.auth.default_roles import DEFAULT_ROLES, PROMOTION_CREATE_WRITE
from app.modules.urge.enums import UrgeCloseReason
from app.modules.urge.models import UrgeTask

_CLOSE_REASONS = ("博主已发布", "已取消", "手动关闭", "已完成")


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
class TestMigration064UrgeTask:
    async def test_close_attachment_column(self, session: AsyncSession) -> None:
        row = (
            await session.execute(
                text(
                    "SELECT is_nullable, data_type, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'urge_task' "
                    "AND column_name = 'close_attachment_id'"
                )
            )
        ).one()
        assert (row.is_nullable, row.data_type, row.column_default) == ("YES", "uuid", None)

    async def test_close_attachment_fk_restrict_and_index(self, session: AsyncSession) -> None:
        fks = set((await _constraints(session, "urge_task", "f")).values())
        assert (
            "FOREIGN KEY (close_attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT" in fks
        )
        idx = dict(
            (
                await session.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'urge_task'")
                )
            )
            .tuples()
            .all()
        )["idx_urge_task_close_attachment"]
        assert not idx.startswith("CREATE UNIQUE")
        assert "(close_attachment_id)" in idx
        assert "WHERE (close_attachment_id IS NOT NULL)" in idx

    async def test_checks_real_names(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "urge_task", "c")
        # 命名约定给 ck 套前缀：删 → 同名重建后实名仍是 ck_urge_task_ck_urge_task_close_reason
        reason = checks["ck_urge_task_ck_urge_task_close_reason"]
        for value in _CLOSE_REASONS:
            assert f"'{value}'" in reason, value
        shot = checks["ck_urge_task_ck_urge_task_complete_screenshot"]
        assert "'已完成'" in shot
        assert "close_attachment_id IS NOT NULL" in shot
        # 其余三个 CHECK 不动
        for name in ("closed_fields", "status", "urge_count_nonneg"):
            assert f"ck_urge_task_ck_urge_task_{name}" in checks, name


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration064Seed:
    async def test_create_write_only_pr_manager(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text("SELECT category FROM permission WHERE scope = CAST(:s AS text)"),
                {"s": PROMOTION_CREATE_WRITE},
            )
        ).all()
        assert [r.category for r in rows] == ["function"]
        in_db = await _roles_for(session, PROMOTION_CREATE_WRITE)
        assert in_db == {"pr_manager"}
        assert in_db == {r.code for r in DEFAULT_ROLES if PROMOTION_CREATE_WRITE in r.permissions}

    async def test_finance_has_no_importer_grant(self, session: AsyncSession) -> None:
        # 结款单导入按来源只认 finance.settlement:pay（设计 4.4、4.5），不给财务任何 importer scope
        scopes = (
            (
                await session.execute(
                    text(
                        "SELECT p.scope FROM role_permission rp "
                        "JOIN role r ON r.id = rp.role_id "
                        "JOIN permission p ON p.id = rp.permission_id "
                        "WHERE r.code = 'finance' AND p.scope LIKE 'importer%'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert scopes == []
        finance = next(r for r in DEFAULT_ROLES if r.code == "finance")
        assert not [s for s in finance.permissions if s.startswith("importer")]


class TestOrmMatchesMigration064:
    def test_close_attachment_column(self) -> None:
        col = UrgeTask.__table__.columns["close_attachment_id"]
        assert col.nullable is True
        assert col.server_default is None
        fk = next(iter(col.foreign_keys))
        assert (fk.column.table.name, fk.ondelete) == ("attachment", "RESTRICT")
        idx = {i.name: i for i in UrgeTask.__table__.indexes}["idx_urge_task_close_attachment"]
        assert [c.name for c in idx.columns] == ["close_attachment_id"]
        assert str(idx.dialect_options["postgresql"]["where"]) == "close_attachment_id IS NOT NULL"

    def test_checks(self) -> None:
        by_name = {str(c.name): c for c in UrgeTask.__table__.constraints}
        reason = str(by_name["ck_urge_task_ck_urge_task_close_reason"].sqltext)  # type: ignore[attr-defined]
        for value in _CLOSE_REASONS:
            assert f"'{value}'" in reason, value
        shot = str(by_name["ck_urge_task_ck_urge_task_complete_screenshot"].sqltext)  # type: ignore[attr-defined]
        assert "'已完成'" in shot
        assert "close_attachment_id IS NOT NULL" in shot

    def test_enum_and_purpose(self) -> None:
        assert tuple(r.value for r in UrgeCloseReason) == _CLOSE_REASONS
        assert UrgeCloseReason.COMPLETED.value == "已完成"
        assert "urge_complete_screenshot" in ALLOWED_PURPOSES
