"""迁移 062_report_view_columns 在 head 状态下的库结构，以及回填 SQL 与 adapter 的逐值对拍（报表视图设计 3.1）。

往返（下行删新维度行、打印仅退款备份、恢复旧唯一键）与按生产形态的存量演练在测试库上手工跑，
结果写报告，不进仓库。
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.product.platform_product_models  # noqa: F401  (ad_daily 的 FK 目标表)
from app.modules.collect.models import AdDaily
from app.modules.importer.adapters.lenient_parse import to_optional_int
from app.modules.report.summary_models import ProductRoiSummary, PrWorkProgressSummary

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "062_report_view_columns.py"
)

# (表, 列) → (data_type, numeric_precision, numeric_scale, is_nullable, column_default)
_NEW_COLUMNS: dict[tuple[str, str], tuple[str, int | None, int | None, str, str | None]] = {
    ("product_roi_summary", "channel"): (
        "character varying",
        None,
        None,
        "NO",
        "'普通'::character varying",
    ),
    ("product_roi_summary", "pay_orders"): ("bigint", 64, 0, "NO", "0"),
    ("product_roi_summary", "ad_add_cart_count"): ("bigint", 64, 0, "NO", "0"),
    ("product_roi_summary", "paired_all_cart"): ("bigint", 64, 0, "NO", "0"),
    ("product_roi_summary", "paired_ad_cart"): ("bigint", 64, 0, "NO", "0"),
    ("product_roi_summary", "paired_days"): ("integer", 32, 0, "NO", "0"),
    ("ad_daily", "add_cart_count"): ("integer", 32, 0, "YES", None),
    ("pr_work_progress_summary", "goods_main_id"): ("uuid", None, None, "YES", None),
    ("store_daily", "refund_only_amount"): ("numeric", 12, 2, "YES", None),
}
for _t in ("shop_daily_summary",):  # 069删除无人读取的周/月缓存；062历史迁移保留。
    # 退款不限精度（同 054），其余 numeric(16,2)
    _NEW_COLUMNS[(_t, "refund_amount")] = ("numeric", None, None, "NO", "0")
    for _c in ("brushing_amount", "ad_spend", "promo_cost", "live_pay_amount", "live_ad_spend"):
        _NEW_COLUMNS[(_t, _c)] = ("numeric", 16, 2, "NO", "0")
for _c in (
    "quote_amount",
    "published_quote_amount",
    "effective_quote_amount",
    "unpublished_quote_amount",
    "cancelled_quote_amount",
    "promo_cost",
):
    _NEW_COLUMNS[("pr_work_progress_summary", _c)] = ("numeric", 16, 2, "NO", "0")
for _c in ("unpublished_count", "shipped_count", "shipped_published_count"):
    _NEW_COLUMNS[("pr_work_progress_summary", _c)] = ("integer", 32, 0, "NO", "0")


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_migration_062", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _indexdef(session: AsyncSession, name: str) -> str:
    return (
        await session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = CAST(:n AS text)"),
            {"n": name},
        )
    ).scalar_one()


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


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration062Columns:
    async def test_new_columns(self, session: AsyncSession) -> None:
        rows = (
            await session.execute(
                text(
                    "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, "
                    "is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
                ),
                {"tables": sorted({t for t, _ in _NEW_COLUMNS})},
            )
        ).all()
        got = {(r.table_name, r.column_name): r for r in rows}
        for key, expected in _NEW_COLUMNS.items():
            assert key in got, key
            r = got[key]
            assert (
                r.data_type,
                r.numeric_precision,
                r.numeric_scale,
                r.is_nullable,
                r.column_default,
            ) == expected, key

    async def test_roi_unique_index_has_channel(self, session: AsyncSession) -> None:
        idx = await _indexdef(session, "uq_product_roi_summary")
        assert idx.startswith("CREATE UNIQUE INDEX")
        assert idx.endswith("(tenant_id, goods_main_id, channel, stat_date)")

    async def test_pr_unique_index_has_goods_nulls_not_distinct(
        self, session: AsyncSession
    ) -> None:
        idx = await _indexdef(session, "uq_pr_work_progress_summary")
        assert idx.startswith("CREATE UNIQUE INDEX")
        assert idx.endswith("(tenant_id, pr_id, goods_main_id, stat_date) NULLS NOT DISTINCT")

    async def test_channel_check(self, session: AsyncSession) -> None:
        checks = await _constraints(session, "product_roi_summary", "c")
        definition = checks["ck_product_roi_summary_channel"]
        assert "'普通'" in definition and "'直播'" in definition

    async def test_pr_goods_fk_cascade(self, session: AsyncSession) -> None:
        fks = await _constraints(session, "pr_work_progress_summary", "f")
        assert fks["fk_pr_work_progress_summary_goods_main_id_goods_main"] == (
            "FOREIGN KEY (goods_main_id) REFERENCES goods_main(id) ON DELETE CASCADE"
        )


# 回填对拍的取值：真实导出的样子（千分位、空白、"-"、空串、中文单位、小数）+ 缺键 / JSON null
_BACKFILL_CASES: list[Any] = [
    "1,234",
    "1,234.00",
    " 12 ",
    "-",
    "",
    "1.2万",
    "5.0",
    "5.",
    "5.5",
    "+5",
    "-3",
    "0",
    None,
    "<missing>",
]


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration062Backfill:
    async def test_backfill_matches_adapter(self, session: AsyncSession, tenant_a: Any) -> None:
        module = _load_migration()
        # expire_all 之后再读 ORM 属性会 MissingGreenlet，先取出来
        tenant_id = tenant_a.id
        extras: dict[str, dict[str, Any]] = {}
        for i, raw in enumerate(_BACKFILL_CASES):
            pid = f"bf062_{i}_{uuid4().hex[:8]}"
            extra: dict[str, Any] = {"花费": "1.00"}
            if raw != "<missing>":
                extra["总购物车数"] = raw
            extras[pid] = extra
            session.add(
                AdDaily(
                    tenant_id=tenant_id,
                    platform_id_snapshot=pid,
                    date=date(2026, 10, 1),
                    extra=extra,
                )
            )
        await session.flush()

        await session.execute(
            text(module._BACKFILL_SQL + " AND tenant_id = CAST(:t AS uuid)"),
            {"t": str(tenant_id)},
        )
        session.expire_all()
        got = dict(
            (
                await session.execute(
                    select(AdDaily.platform_id_snapshot, AdDaily.add_cart_count).where(
                        AdDaily.tenant_id == tenant_id
                    )
                )
            )
            .tuples()
            .all()
        )
        expected = {pid: to_optional_int(extra.get("总购物车数")) for pid, extra in extras.items()}
        assert got == expected
        # 场景有效性：两种结果都出现
        assert any(v is None for v in expected.values())
        assert {1234, 12, 5, -3, 0} <= {v for v in expected.values() if v is not None}


class TestSummaryOrmMatchesMigration062:
    def test_roi_index_and_check(self) -> None:
        table = ProductRoiSummary.__table__
        idx = {i.name: i for i in table.indexes}
        assert [c.name for c in idx["uq_product_roi_summary"].columns] == [
            "tenant_id",
            "goods_main_id",
            "channel",
            "stat_date",
        ]
        names = {str(c.name) for c in table.constraints}
        assert "ck_product_roi_summary_channel" in names

    def test_pr_unique_index_not_declared(self) -> None:
        # NULLS NOT DISTINCT 由迁移原生 DDL 建，ORM 声明的话 create_all 会建出语义不同的索引
        names = {i.name for i in PrWorkProgressSummary.__table__.indexes}
        assert "uq_pr_work_progress_summary" not in names
        fk = next(iter(PrWorkProgressSummary.__table__.columns["goods_main_id"].foreign_keys))
        assert fk.ondelete == "CASCADE"
