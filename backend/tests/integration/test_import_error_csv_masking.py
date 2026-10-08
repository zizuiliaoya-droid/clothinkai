"""8a-7：失败明细 CSV 按查看者的字段权限脱敏（设计 §4.6.1，N6、N11）。

- 商品资料：PR / 主管下载 → 「成本价」「采购价」为「***」，其余列原值；跟单、运营 → 原值
- 博主：运营下载 → 「微信」「手机号」「报价」为「***」；PR → 原值
- 自定义映射把成本价指到「进价」、文件里另有「成本价」原列 → 两列都遮
- 跑真实 runner：adapter 抛参数里带 ``Decimal('60.00')`` 的 ``IntegrityError`` / ``DataError``
  → 行失败原因不带 SQL 与参数，PR 下载的 CSV 里没有 60.00
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.auth.service import AuthService
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.field_mapping_service import FieldMappingService
from app.modules.importer.models import ImportJob
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.schemas import FieldMappingColumn, FieldMappingCreate
from app.modules.importer.service import ImportService
from app.tasks.import_tasks import _run_import_batch

STYLE_ROW = {
    "款式编码": "MK8A01",
    "款式名称": "测试款",
    "SKU编码": "MK8A01-红-M",
    "颜色": "红",
    "尺码": "M",
    "成本价": "60.00",
    "采购价": "55.00",
    "基本售价": "199.00",
}
BLOGGER_ROW = {
    "小红书ID": "xhs8a01",
    "昵称": "博主甲",
    "微信": "wx_8a",
    "手机号": "13800000000",
    "报价": "500",
    "粉丝数": "12000",
}


@pytest.fixture
def registered() -> Iterator[None]:
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.register(StyleSkuImportAdapter())
    ImportAdapterRegistry.register(BloggerImportAdapter())
    try:
        yield
    finally:
        ImportAdapterRegistry._adapters.clear()
        ImportAdapterRegistry._adapters.update(saved)


def _rows(data: bytes) -> list[dict[str, Any]]:
    text_ = data.decode("utf-8").lstrip("\ufeff")
    out = []
    for rec in csv.DictReader(io.StringIO(text_)):
        rec["raw"] = json.loads(rec["raw_data"])
        out.append(rec)
    return out


async def _download(
    session: AsyncSession, factory: Any, tenant: Any, role_code: str, batch_id: UUID
) -> bytes:
    role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
    user = await factory.user(tenant, roles=[role])
    perms = await AuthService(session).load_effective_permissions(user.id)
    return await ImportService(session).build_error_csv(batch_id, user, perms)


async def _failed_job(session: AsyncSession, tenant_id: UUID, batch_id: UUID, raw: dict) -> None:
    session.add(
        ImportJob(
            id=uuid4(),
            tenant_id=tenant_id,
            batch_id=batch_id,
            row_number=1,
            status="failed",
            raw_data=raw,
            error_detail="颜色不能为空",
            attempt_count=1,
        )
    )
    await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered")
class TestErrorCsvMasking:
    @pytest.mark.parametrize("role_code", ["pr", "pr_manager"])
    async def test_pr_style_sku_prices_masked(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        import_batch_factory: Any,
        role_code: str,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            batch = await import_batch_factory.batch(source="manual_style_sku", status="partial")
            await _failed_job(session, tenant_a.id, batch.id, {**STYLE_ROW, "采购价": ""})
            data = await _download(session, factory, tenant_a, role_code, batch.id)
            (row,) = _rows(data)
            assert row["raw"]["成本价"] == "***"
            assert row["raw"]["采购价"] == ""  # 空值不遮
            for key in ("款式编码", "款式名称", "SKU编码", "颜色", "尺码", "基本售价"):
                assert row["raw"][key] == STYLE_ROW[key]
            assert row["error_detail"] == "颜色不能为空"
            assert "60.00" not in data.decode("utf-8")
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize("role_code", ["merchandiser", "operations", "admin"])
    async def test_product_roles_see_prices(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        import_batch_factory: Any,
        role_code: str,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            batch = await import_batch_factory.batch(source="manual_style_sku", status="partial")
            await _failed_job(session, tenant_a.id, batch.id, STYLE_ROW)
            (row,) = _rows(await _download(session, factory, tenant_a, role_code, batch.id))
            assert row["raw"] == STYLE_ROW
        finally:
            tenant_id_ctx.reset(token)

    async def test_operations_blogger_contacts_masked(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        import_batch_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            batch = await import_batch_factory.batch(source="manual_blogger", status="partial")
            await _failed_job(session, tenant_a.id, batch.id, BLOGGER_ROW)
            (row,) = _rows(await _download(session, factory, tenant_a, "operations", batch.id))
            assert row["raw"]["微信"] == "***"
            assert row["raw"]["手机号"] == "***"
            assert row["raw"]["报价"] == "***"
            for key in ("小红书ID", "昵称", "粉丝数"):
                assert row["raw"][key] == BLOGGER_ROW[key]

            (pr_row,) = _rows(await _download(session, factory, tenant_a, "pr", batch.id))
            assert pr_row["raw"] == BLOGGER_ROW
        finally:
            tenant_id_ctx.reset(token)

    async def test_custom_mapping_masks_mapped_and_builtin_columns(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        import_batch_factory: Any,
    ) -> None:
        """自定义映射把成本价指到「进价」：「进价」与导出里仍在的「成本价」原列都遮。

        8a-4：映射走真实保存流程（``FieldMappingService.create_version``，过目录校验——必填与
        颜色组都要映射）；自定义映射一个目标字段只读一列，不再存别名。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            role = (
                await session.execute(select(Role).where(Role.code == "merchandiser"))
            ).scalar_one()
            owner = await factory.user(tenant_a, roles=[role])
            owner_perms = await AuthService(session).load_effective_permissions(owner.id)
            columns = [
                ("款号", "style_code"),
                ("商品编码", "sku_code"),
                ("商品名称", "style_name"),
                ("颜色", "color"),
                ("规格", "size"),
                ("进价", "cost_price"),
                ("拿货价", "purchase_price"),
            ]
            mapping = await FieldMappingService(session).create_version(
                FieldMappingCreate(
                    source="manual_style_sku",
                    columns=[FieldMappingColumn(source_col=s, target_field=t) for s, t in columns],
                ),
                owner,
                owner_perms,
            )
            batch = await import_batch_factory.batch(
                source="manual_style_sku", status="partial", mapping_version=mapping.version
            )
            raw = {
                "款号": "MK8A02",
                "进价": "61.00",
                "成本价": "60.00",
                "拿货价": "52.00",
                "采购价": "51.00",
                "颜色": "蓝",
            }
            await _failed_job(session, tenant_a.id, batch.id, raw)
            (row,) = _rows(await _download(session, factory, tenant_a, "pr", batch.id))
            assert row["raw"] == {
                "款号": "MK8A02",
                "进价": "***",
                "成本价": "***",
                "拿货价": "***",
                "采购价": "***",
                "颜色": "蓝",
            }
        finally:
            tenant_id_ctx.reset(token)


def _style_csv(suffix: str) -> bytes:
    return (
        "款式编码,款式名称,SKU编码,颜色,尺码,成本价,采购价\n"
        f"MK{suffix},测试款,MK{suffix}-1,红,M,60.00,55.00\n"
        f"MK{suffix},测试款,MK{suffix}-2,红,L,60.00,55.00\n"
    ).encode()


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunnerDbErrorNotLeaked:
    """N11：真实 runner 写入的行失败原因不含 SQL 与参数，PR 下载的 CSV 里没有成本价。"""

    async def test_integrity_and_data_error(
        self,
        engine: Any,
        session: AsyncSession,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
        registered: None,
    ) -> None:
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)

        suffix = uuid4().hex[:8]
        csv_bytes = _style_csv(suffix)
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service, "get_object_bytes", lambda bucket, key: csv_bytes
        )

        params = {"sku_code": f"MK{suffix}-1", "cost_price": Decimal("60.00")}
        sql = "INSERT INTO sku (sku_code, cost_price) VALUES ($1, $2)"

        async def _boom(self: Any, parsed: dict, **_: Any) -> Any:
            if parsed["sku_code"].endswith("-1"):
                orig = Exception('duplicate key value violates unique constraint "uq_sku_code"')
                raise IntegrityError(sql, params, orig)
            raise DataError(sql, params, None)  # type: ignore[arg-type]

        # 8a-4：商品资料 adapter 走 upsert_with_context（runner 优先调它）
        monkeypatch.setattr(StyleSkuImportAdapter, "upsert_with_context", _boom)

        batch_id = uuid4()
        async with Maker() as seed:
            tenant_id = (
                await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
            ).scalar_one()
            await seed.execute(
                text(
                    "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                    "original_filename, file_r2_key, file_bucket, status, total_rows, "
                    "imported, failed, retry_count, created_at, updated_at) "
                    "VALUES (:id, :tid, 'manual_style_sku', :h, 'styles.csv', :k, "
                    "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": tenant_id,
                    "h": suffix,
                    "k": f"imports/{tenant_id}/{batch_id}/styles.csv",
                },
            )
            await seed.commit()

        token = tenant_id_ctx.set(tenant_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["failed"] == 2

            async with Maker() as check:
                details = (
                    (
                        await check.execute(
                            text(
                                "SELECT error_detail FROM import_job WHERE batch_id = :b "
                                "ORDER BY row_number"
                            ),
                            {"b": batch_id},
                        )
                    )
                    .scalars()
                    .all()
                )
            assert details == ["与另一批次同时写入，请重试", "数据库错误（DataError），请重试"]

            class _T:
                id = tenant_id

            data = (await _download(session, factory, _T(), "pr", batch_id)).decode("utf-8")
            assert "60.00" not in data
            assert "[SQL" not in data
            assert "[parameters" not in data
            assert "与另一批次同时写入，请重试" in data
        finally:
            tenant_id_ctx.reset(token)
            async with Maker() as c:
                await c.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
                await c.commit()
