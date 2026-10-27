"""069清理边界：只删缓存，恢复原DDL，日表窗口/覆盖记录/锁不变。"""

from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from app.modules.report.summary_refresh_service import SummaryRefreshService


def _migration():
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    revision = script.get_revision("069_remove_unused_shop_caches")
    assert revision.down_revision == "068_promotion_content_platform"
    return revision.module


def _ddl(direction: str) -> str:
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(_migration(), direction)()
    return output.getvalue()


def test_upgrade_only_drops_two_cache_tables():
    statements = [sql.strip() for sql in _ddl("upgrade").split(";") if sql.strip()]
    assert statements == ["DROP TABLE shop_week_summary", "DROP TABLE shop_month_summary"]


def test_downgrade_restores_empty_tables_with_rls_acl_and_metric_precision():
    sql = _ddl("downgrade")
    for table, bucket in (
        ("shop_week_summary", "week_start"),
        ("shop_month_summary", "period_month"),
    ):
        assert f"CREATE TABLE {table}" in sql
        assert f"{bucket} DATE NOT NULL" in sql
        assert f"CREATE UNIQUE INDEX uq_{table} ON {table} (tenant_id, {bucket})" in sql
        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in sql
        assert f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY' in sql
        assert f'CREATE POLICY tenant_isolation ON "{table}" FOR ALL TO clothing_app' in sql
        assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO clothing_app" in sql
    assert sql.count("refund_amount NUMERIC DEFAULT 0 NOT NULL") == 2
    assert sql.count("live_ad_spend NUMERIC(16, 2) DEFAULT 0 NOT NULL") == 2
    assert sql.count("REFERENCES tenant (id) ON DELETE RESTRICT") == 2
    assert "INSERT INTO" not in sql


@pytest.mark.asyncio
async def test_refresh_keeps_three_daily_tables_original_coverage_and_expanded_shop_span():
    tenant_id = uuid4()
    session = MagicMock()
    result = MagicMock()
    result.scalar_one.return_value = True
    session.execute = AsyncMock(return_value=result)
    service = SummaryRefreshService(session)
    service._refresh_product_roi = AsyncMock(return_value=1)
    service._refresh_pr_progress = AsyncMock(return_value=2)
    service._refresh_shop_daily = AsyncMock(return_value=3)
    lo, hi = date(2026, 9, 2), date(2026, 10, 2)

    counts = await service.refresh(tenant_id=tenant_id, date_from=lo, date_to=hi)

    assert counts == {
        "product_roi_summary": 1,
        "pr_work_progress_summary": 2,
        "shop_daily_summary": 3,
    }
    for refresh in (service._refresh_product_roi, service._refresh_pr_progress):
        assert refresh.await_args.args[:3] == (tenant_id, lo, hi)
    assert service._refresh_shop_daily.await_args.args[:3] == (
        tenant_id,
        date(2026, 8, 31),
        date(2026, 10, 31),
    )
    lock, coverage = session.execute.await_args_list
    assert "pg_try_advisory_xact_lock" in str(lock.args[0])
    assert "report_summary_coverage" in str(coverage.args[0])
    assert coverage.args[1]["lo"] == lo
    assert coverage.args[1]["hi"] == hi
