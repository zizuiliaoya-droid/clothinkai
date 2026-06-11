"""U06a 集成测试：失败明细 CSV 下载（csv_safe injection 防护 + UTF-8 BOM）。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.models import ImportJob
from app.modules.importer.service import ImportService


async def _add_failed_job(session, tenant_id, batch_id, row_number, raw_data, error):
    job = ImportJob(
        id=uuid4(),
        tenant_id=tenant_id,
        batch_id=batch_id,
        row_number=row_number,
        status="failed",
        raw_data=raw_data,
        error_detail=error,
        attempt_count=1,
    )
    session.add(job)
    await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
class TestErrorsDownload:
    async def test_csv_safe_escapes_formula(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
    ) -> None:
        """raw_data / error_detail 中危险前缀（=+-@）导出时加单引号转义。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(status="partial", failed=1)
            await _add_failed_job(
                session,
                tenant_a.id,
                batch.id,
                1,
                {"name": "=cmd|'/c calc'!A1"},
                "=DANGER",
            )
            svc = ImportService(session)
            data = await svc.build_error_csv(batch.id, user)
            text = data.decode("utf-8")

            assert text.startswith("\ufeff")  # BOM
            # error_detail 以危险字符开头 → csv_safe 加 ' 前缀
            assert "'=DANGER" in text
            # raw_data 以 JSON 形式整体入单元格（首字符 '{'，Excel 不解析为公式），
            # 危险公式内容被安全包裹，不需逐值转义
            assert "=cmd" in text  # 内容保真
            assert text.count("'=cmd") == 0  # 未被错误地额外转义（JSON 包裹已安全）
        finally:
            tenant_id_ctx.reset(token)

    async def test_normal_values_not_escaped(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(status="partial", failed=1)
            await _add_failed_job(
                session, tenant_a.id, batch.id, 2, {"name": "正常名称"}, "字段缺失"
            )
            svc = ImportService(session)
            text = (await svc.build_error_csv(batch.id, user)).decode("utf-8")
            assert "正常名称" in text
            assert "字段缺失" in text
        finally:
            tenant_id_ctx.reset(token)

    async def test_only_failed_rows_included(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(status="partial", failed=1)
            await _add_failed_job(
                session, tenant_a.id, batch.id, 1, {"a": "fail"}, "err"
            )
            # success 行（不应出现在下载中）
            session.add(
                ImportJob(
                    id=uuid4(),
                    tenant_id=tenant_a.id,
                    batch_id=batch.id,
                    row_number=2,
                    status="success",
                    raw_data={"a": "ok"},
                    attempt_count=1,
                )
            )
            await session.flush()
            svc = ImportService(session)
            text = (await svc.build_error_csv(batch.id, user)).decode("utf-8")
            assert "fail" in text
            assert "ok" not in text
        finally:
            tenant_id_ctx.reset(token)
