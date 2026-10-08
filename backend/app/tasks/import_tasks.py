"""U06a 导入异步 Runner（run_import_batch）。

按 nfr-design-patterns.md P-U06a-01/02 实现：
- **NF-1**：per-row 事务内 ``SET LOCAL app.tenant_id``（事务级，绝不会话级 —— 防连接池串租）
- 双 session：bypass（元数据 / 失败 job / 汇总，系统级）+ app（per-row upsert，RLS 约束）
- **NF-4**：``worker_process_init`` 信号注册 Adapter（HTTP 进程注册 worker 看不到）
- **FB-E**：only_failed 用 import_job.raw_data 还原失败行，原地 UPDATE attempt_count
- **FB-C**：runner 持有 per-row 事务边界；adapter.upsert(session, tenant_id, actor_id) 不自 commit

成功 job 与业务记录同 per-row 事务（原子）；失败 job 用独立 bypass session 写（不被回滚带走）。
adapter 实现了 ``PostRowsImportAdapter`` 就在行循环之后、汇总之前调 ``after_rows``（异常隔离）。
"""

from __future__ import annotations

import io
import logging
import time
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

import sentry_sdk
from celery import Task
from celery.signals import worker_process_init
from sqlalchemy import Table, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, StatementError

from app.core.attachment import BucketKind
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import AsyncSessionApp, AsyncSessionBypass
from app.core.metrics import (
    import_batch_duration_seconds,
    import_batch_total,
    import_rows_total,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.importer.adapter import (
    BatchRunContext,
    ContextAwareImportAdapter,
    PostRowsImportAdapter,
)
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.models import ImportBatch, ImportJob
from app.modules.importer.outcome import BatchSeen, ImportRowContext, RowKind, RowOutcome
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.runner import run_async_task

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NF-4：worker 进程注册 Adapter（HTTP 进程的注册 worker 看不到）
# ---------------------------------------------------------------------------


@worker_process_init.connect
def _register_adapters_in_worker(**_kwargs: Any) -> None:
    """Celery worker 子进程启动时注册所有 Adapter（NF-4）。

    与 main.py lifespan 的 register_import_adapters 调用同一函数，保证
    HTTP 进程与 worker 进程都能 ImportAdapterRegistry.get(source)。
    """
    try:
        from app.main import register_import_adapters

        register_import_adapters()
        log.info("import_adapters_registered_in_worker")
    except Exception as exc:
        log.exception("import_adapter_worker_registration_failed")
        sentry_sdk.capture_exception(exc)


# ---------------------------------------------------------------------------
# Celery 任务入口
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.import_tasks.run_import_batch",
    queue="default",
)
def run_import_batch(self: Task, batch_id: str, only_failed: bool = False) -> dict[str, Any]:
    """异步执行导入批次解析 + 行级 upsert。

    Args:
        batch_id: ImportBatch.id（字符串，Celery JSON 序列化）。
        only_failed: True = 仅重跑 import_job.failed 行（FB-E partial 重试）。
    """
    return run_async_task(_run_import_batch(UUID(batch_id), only_failed))


# ---------------------------------------------------------------------------
# 主编排（session advisory lock + 双 session + per-row SET LOCAL）
# ---------------------------------------------------------------------------


async def _run_import_batch(batch_id: UUID, only_failed: bool = False) -> dict[str, Any]:
    """原子 claim 一个批次；原始投递与恢复投递并发时只允许一个 runner。"""
    lock_sql = text("SELECT pg_try_advisory_lock(" "hashtextextended(CAST(:batch_id AS text), 0))")
    unlock_sql = text("SELECT pg_advisory_unlock(" "hashtextextended(CAST(:batch_id AS text), 0))")
    async with AsyncSessionBypass() as lock_session:
        acquired = bool(
            (await lock_session.execute(lock_sql, {"batch_id": str(batch_id)})).scalar_one()
        )
        if not acquired:
            log.info(
                "import_batch_already_running",
                extra={"batch_id": str(batch_id)},
            )
            return {"status": "already_running"}
        try:
            return await _run_import_batch_claimed(batch_id, only_failed)
        finally:
            try:
                await lock_session.execute(unlock_sql, {"batch_id": str(batch_id)})
            except Exception as exc:
                log.warning(
                    "import_batch_advisory_unlock_failed",
                    extra={"batch_id": str(batch_id)},
                )
                sentry_sdk.capture_exception(exc)


async def _run_import_batch_claimed(batch_id: UUID, only_failed: bool = False) -> dict[str, Any]:
    # ── 1. 元数据读取 + 状态守卫（bypass，系统级）──
    async with AsyncSessionBypass() as meta_s:
        batch = await meta_s.get(ImportBatch, batch_id)
        if batch is None:
            return {"status": "not_found"}
        # runner 入口守卫：仅 processing 可执行（NF-3 防重复 / 已结束）
        if batch.status != "processing":
            log.warning(
                "import_batch_not_processing",
                extra={"batch_id": str(batch_id), "status": batch.status},
            )
            return {"status": "skipped_not_processing"}
        tenant_id = batch.tenant_id
        source = batch.source
        created_by = batch.created_by
        file_bucket = batch.file_bucket
        file_r2_key = batch.file_r2_key
        original_filename = batch.original_filename

    # ── 2. Adapter 取得（NF-4：worker 已注册；缺失 → batch.failed）──
    adapter = ImportAdapterRegistry.get(source)
    if adapter is None:
        await _mark_batch_failed(batch_id, "adapter_not_registered")
        import_batch_total.labels(source=source, status="failed").inc()
        return {"status": "failed", "reason": "adapter_not_registered"}

    mapping = await _load_mapping(source, tenant_id, batch.mapping_version)

    # ── 3. 取文件 + 解析（解析致命失败 → batch.failed，FB-E ①）──
    raw: bytes | None = None
    try:
        if only_failed:
            rows = await _load_failed_rows(batch_id)  # [(row_number, raw_data), ...]
        else:
            from app.core.attachment import attachment_service

            raw = attachment_service.get_object_bytes(cast("BucketKind", file_bucket), file_r2_key)
            rows = _parse_rows(raw, original_filename)
    except Exception as exc:
        await _mark_batch_failed(batch_id, f"parse_error:{type(exc).__name__}")
        sentry_sdk.capture_exception(exc)
        import_batch_total.labels(source=source, status="failed").inc()
        return {"status": "failed", "reason": "parse_error"}

    # 行数上限三层防护 L3（NF-6：upload 时无法预知行数）
    if len(rows) > settings.IMPORT_MAX_ROWS:
        await _mark_batch_failed(batch_id, "too_many_rows")
        import_batch_total.labels(source=source, status="failed").inc()
        return {"status": "failed", "reason": "too_many_rows"}

    # ── 4. 逐行处理（tenant_id_ctx 供 audit；RLS 靠 per-row SET LOCAL）──
    tok = tenant_id_ctx.set(tenant_id)
    start = time.perf_counter()
    counts = _BatchCounts()
    affected = _AffectedDates(getattr(adapter, "summary_date_field", None))
    # 每次执行（首跑、整文件重跑、只重跑失败行）一个，同批以第一个已提交的给值行为准（§4.2.1）
    batch_seen = BatchSeen()
    try:
        for row_number, row in rows:
            outcome = await _process_one_row(
                adapter,
                row,
                row_number,
                mapping,
                batch_id=batch_id,
                tenant_id=tenant_id,
                actor_id=created_by,
                source=source,
                batch_seen=batch_seen,
                affected=affected,
            )
            result = counts.add(outcome)
            import_rows_total.labels(source=source, result=result).inc()
        # 行之后的一段（商品资料导入读内嵌图补主图）：汇总之前做完，前端轮询到终态时结果已在
        if isinstance(adapter, PostRowsImportAdapter):
            first_raw = raw

            def load_file() -> bytes:
                if first_raw is not None:
                    return first_raw
                from app.core.attachment import attachment_service

                return attachment_service.get_object_bytes(
                    cast("BucketKind", file_bucket), file_r2_key
                )

            await _after_rows(
                adapter,
                BatchRunContext(
                    batch_id=batch_id,
                    tenant_id=tenant_id,
                    actor_id=created_by,
                    rows=rows,
                    mapping=mapping,
                    load_file=load_file,
                    app_session=AsyncSessionApp,
                    bypass_session=AsyncSessionBypass,
                ),
            )
    finally:
        tenant_id_ctx.reset(tok)
        import_batch_duration_seconds.labels(source=source).observe(time.perf_counter() - start)

    # ── 5. 汇总（bypass）──
    status = await _summarize_batch(batch_id, counts, only_failed)
    import_batch_total.labels(source=source, status=status).inc()

    # ── 6. 刷新受影响日期的报表汇总表（方案 2：导入完成后自动刷新）──
    if status in ("completed", "partial"):
        _enqueue_summary_refresh(tenant_id, affected, batch_id)
    return {"status": status, "imported": counts.imported, "failed": counts.failed}


async def _after_rows(adapter: PostRowsImportAdapter, run: BatchRunContext) -> None:
    """异常隔离：出错只记日志与 Sentry，不改批次状态、不回滚已提交的行。"""
    try:
        await adapter.after_rows(run)
    except Exception as exc:
        log.exception("import_after_rows_failed", extra={"batch_id": str(run.batch_id)})
        sentry_sdk.capture_exception(exc)


# RowKind → import_job.status（INSERTED / UPDATED 与旧来源的 success 同义）
_JOB_STATUS: dict[RowKind, str] = {
    RowKind.INSERTED: "success",
    RowKind.UPDATED: "success",
    RowKind.FILLED: "filled",
    RowKind.SKIPPED: "skipped",
    RowKind.CONFLICT: "conflict",
}


class _BatchCounts:
    """本次执行的行计数：五类按行互斥；带提示的行与补空对象数不互斥（§4.2）。"""

    def __init__(self) -> None:
        self.imported = 0
        self.filled = 0
        self.skipped = 0
        self.conflicted = 0
        self.failed = 0
        self.warning_count = 0
        self.filled_objects = 0

    def add(self, outcome: RowOutcome | None) -> str:
        """计一行，返回 import_rows_total 的 result 标签。"""
        if outcome is None:
            self.failed += 1
            return "failed"
        if outcome.warnings:
            self.warning_count += 1
        self.filled_objects += len(outcome.filled)
        status = _JOB_STATUS[outcome.kind]
        if status == "success":
            self.imported += 1
        elif status == "filled":
            self.filled += 1
        elif status == "skipped":
            self.skipped += 1
        else:
            self.conflicted += 1
        return status

    @property
    def total_rows(self) -> int:
        return self.imported + self.filled + self.skipped + self.conflicted + self.failed


class _AffectedDates:
    """本批次**成功提交**的行的业务日期范围（导入完成后刷新报表汇总表用）。

    adapter 用类属性 ``summary_date_field`` 声明哪一列是进报表的业务日期（千牛/万相台的
    ``date``、推广单的 ``cooperation_date``、刷单的 ``order_date``）。不声明的来源
    （博主、款式 SKU、结算……）不写任何汇总表依赖的数据，不触发刷新。

    失败行不计：它没进业务表，不影响报表。
    """

    def __init__(self, field: str | None) -> None:
        self.field = field
        self.lo: date | None = None
        self.hi: date | None = None

    def add(self, parsed: dict[str, Any]) -> None:
        if self.field is None:
            return
        value = parsed.get(self.field)
        # datetime 是 date 的子类，先归一，免得把时间也带进比较
        if isinstance(value, datetime):
            value = value.date()
        if not isinstance(value, date):
            return
        if self.lo is None or value < self.lo:
            self.lo = value
        if self.hi is None or value > self.hi:
            self.hi = value


def _enqueue_summary_refresh(tenant_id: UUID, affected: _AffectedDates, batch_id: UUID) -> None:
    """投递「刷新这批数据涉及的日期」。

    投递失败只记日志：导入本身已经成功，不能因为刷新没排上就把批次标成失败。漏掉的
    日子，窗口内的由每小时刷新补上；窗口外的维持旧汇总，页面上的「数据更新于」如实
    显示它是旧的，管理员可以手动刷新。
    """
    if affected.lo is None or affected.hi is None:
        return
    try:
        # 懒导入：summary_tasks → summary_refresh_service → report 模块，
        # 不让导入框架在加载时就依赖报表模块
        from app.tasks.summary_tasks import refresh_report_summaries_for_dates

        refresh_report_summaries_for_dates.apply_async(
            args=[str(tenant_id), affected.lo.isoformat(), affected.hi.isoformat()]
        )
    except Exception as exc:
        log.warning(
            "import_summary_refresh_enqueue_failed",
            extra={"batch_id": str(batch_id), "tenant_id": str(tenant_id)},
        )
        sentry_sdk.capture_exception(exc)


# ---------------------------------------------------------------------------
# 单行处理（NF-1 核心：per-row 事务内 SET LOCAL）
# ---------------------------------------------------------------------------


async def _process_one_row(
    adapter: Any,
    row: dict[str, Any],
    row_number: int,
    mapping: Any,
    *,
    batch_id: UUID,
    tenant_id: UUID,
    actor_id: UUID | None,
    source: str = "",
    batch_seen: BatchSeen | None = None,
    affected: _AffectedDates | None = None,
) -> RowOutcome | None:
    """每行独立事务 + per-row SET LOCAL（NF-1 防连接池串租）。返回行结果；None = 失败。

    成功 → 业务记录 + import_job(success / filled / skipped / conflict) 同事务原子提交。
    失败 → 独立 bypass session 写 import_job(failed)（防被业务事务回滚带走）。

    adapter 实现了 ``ContextAwareImportAdapter`` 就调 ``upsert_with_context``（8a-6），否则调旧的
    ``upsert`` 并把 ``(rid, inserted)`` 映射成 INSERTED / UPDATED。行提交成功后
    ``batch_seen.commit_row()``；失败先 ``discard_row()`` 再写失败行——没提交的行不能成为「第 M 行」。

    import_job 写入用 ``ON CONFLICT(batch_id, row_number)``：首次跑插入（attempt_count=1），
    重试时原地更新（attempt_count+1）—— FB-E only_failed 与首跑统一逻辑。
    """
    seen = batch_seen if batch_seen is not None else BatchSeen()
    try:
        parsed = adapter.parse_row(row, mapping)
        errs = adapter.validate(parsed)
        if errs:
            raise RowValidationError("; ".join(errs))

        async with AsyncSessionApp() as app_s:
            # NF-1：SET LOCAL（事务级），commit/rollback 后失效，绝不残留连接池。
            # 用 set_config(setting, value, is_local=true)：等价 SET LOCAL，但接受 bind
            # 参数（asyncpg 的 SET 语法不支持占位符 $1，会 syntax error）。
            await app_s.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            if isinstance(adapter, ContextAwareImportAdapter):
                ctx = ImportRowContext(
                    tenant_id=tenant_id,
                    source=source or str(getattr(adapter, "source", "")),
                    batch_id=batch_id,
                    row_number=row_number,
                    actor_id=actor_id,
                    batch_seen=seen,
                )
                outcome = await adapter.upsert_with_context(parsed, session=app_s, ctx=ctx)
            else:
                rid, inserted = await adapter.upsert(
                    parsed,
                    session=app_s,
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                )
                outcome = RowOutcome(
                    resource_id=rid, kind=RowKind.INSERTED if inserted else RowKind.UPDATED
                )
            await _upsert_job(
                app_s,
                batch_id=batch_id,
                tenant_id=tenant_id,
                row_number=row_number,
                row=row,
                status=_JOB_STATUS[outcome.kind],
                error_detail=None,
                target_resource_id=outcome.resource_id,
                notes=_job_notes(outcome),
            )
            await app_s.commit()
        seen.commit_row()
        # 提交之后才记：没提交成功的行不该触发报表刷新
        if affected is not None:
            affected.add(parsed)
        return outcome
    except Exception as exc:
        seen.discard_row()
        if isinstance(exc, StatementError):
            # 只记异常类名：不带 SQL 与参数（参数里可能有成本价，8a-7 / N11）
            log.warning(
                "import_row_db_error",
                extra={
                    "batch_id": str(batch_id),
                    "row_number": row_number,
                    "error_type": type(exc.orig or exc).__name__,
                },
            )
        # 失败行用独立 bypass session 写（不被业务回滚带走，FB-C + U05 模式）
        await _write_job_failed_bypass(
            batch_id=batch_id,
            tenant_id=tenant_id,
            row_number=row_number,
            row=row,
            error_detail=_row_error_detail(exc),
        )
        return None


def _job_notes(outcome: RowOutcome) -> dict[str, Any] | None:
    """行提示与补空明细（只有字段名、没有值）；两样都空 → None（落 SQL NULL）。"""
    if not outcome.warnings and not outcome.filled:
        return None
    return {
        "warnings": list(outcome.warnings),
        "filled": [record.to_json() for record in outcome.filled],
    }


# 重试改写行时保留原来的 notes.image（商品资料导入每款的主图结果）：图片段要靠它知道这一款这批之前的
# 结果（已有定论的不再取图、失败 / 跳过的在原行重新取），否则重跑的行会把它冲掉
_KEEP_IMAGE_NOTE = literal_column(
    "CASE WHEN import_job.notes -> 'image' IS NULL THEN excluded.notes "
    "ELSE COALESCE(excluded.notes, '{}'::jsonb) "
    "|| jsonb_build_object('image', import_job.notes -> 'image') END",
    JSONB,
)


async def _upsert_job(
    session: Any,
    *,
    batch_id: UUID,
    tenant_id: UUID,
    row_number: int,
    row: dict[str, Any],
    status: str,
    error_detail: str | None,
    target_resource_id: UUID | None,
    notes: dict[str, Any] | None = None,
) -> None:
    """INSERT ... ON CONFLICT(batch_id,row_number) DO UPDATE（首跑插入 / 重试更新）。

    用 core ``pg_insert``（绕过 ORM 租户钩子，显式带 tenant_id）；RLS 由调用方
    SET LOCAL（app 会话）或 bypass 角色（失败写）保证。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # ``__table__`` 静态类型是 FromClause，实际是 Table；insert() 需要 TableClause。
    stmt = pg_insert(cast("Table", ImportJob.__table__)).values(
        tenant_id=tenant_id,
        batch_id=batch_id,
        row_number=row_number,
        status=status,
        raw_data=row,
        error_detail=error_detail,
        target_resource_id=target_resource_id,
        notes=notes,
        attempt_count=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["batch_id", "row_number"],
        set_={
            "status": stmt.excluded.status,
            "raw_data": stmt.excluded.raw_data,
            "error_detail": stmt.excluded.error_detail,
            "target_resource_id": stmt.excluded.target_resource_id,
            "notes": _KEEP_IMAGE_NOTE,
            "attempt_count": ImportJob.attempt_count + 1,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)


async def _write_job_failed_bypass(
    *,
    batch_id: UUID,
    tenant_id: UUID,
    row_number: int,
    row: dict[str, Any],
    error_detail: str,
) -> None:
    """失败行用独立 bypass session 写（不被业务事务回滚带走）。"""
    async with AsyncSessionBypass() as fail_s:
        await _upsert_job(
            fail_s,
            batch_id=batch_id,
            tenant_id=tenant_id,
            row_number=row_number,
            row=row,
            status="failed",
            error_detail=error_detail,
            target_resource_id=None,
        )
        await fail_s.commit()


# ---------------------------------------------------------------------------
# 文件解析（csv / openpyxl read_only）
# ---------------------------------------------------------------------------


def _parse_rows(raw: bytes, filename: str) -> list[tuple[int, dict[str, Any]]]:
    """解析 CSV / XLSX 为 [(row_number, {col: value}), ...]（row_number 从 1 起，不含表头）。

    - CSV：utf-8-sig（兼容 BOM）+ DictReader
    - XLSX：openpyxl ``read_only=True, data_only=True``（流式 + 读公式计算值，不执行宏）
    """
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return _parse_csv(raw)
    if name.endswith(".xlsx"):
        return _parse_xlsx(raw)
    raise ValueError(f"unsupported file extension: {filename}")


def _parse_csv(raw: bytes) -> list[tuple[int, dict[str, Any]]]:
    import csv

    # 平台导出常带前置空行（如生意参谋千牛）→ 跳过开头完全空白的行后再取表头
    lines = raw.decode("utf-8-sig").splitlines()
    start = 0
    while start < len(lines) and lines[start].replace(",", "").strip() == "":
        start += 1
    text_stream = io.StringIO("\n".join(lines[start:]))
    reader = csv.DictReader(text_stream)
    rows: list[tuple[int, dict[str, Any]]] = []
    for idx, record in enumerate(reader, start=1):
        # 去除 None 键（多余列）+ 统一字符串
        clean = {
            (k or "").strip(): ("" if v is None else str(v))
            for k, v in record.items()
            if k is not None
        }
        rows.append((idx, clean))
    return rows


def _parse_xlsx(raw: bytes) -> list[tuple[int, dict[str, Any]]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        ws = wb.active
        rows: list[tuple[int, dict[str, Any]]] = []
        header: list[str] = []
        row_number = 0
        for excel_row in ws.iter_rows(values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in excel_row]
            if not header:
                # 平台导出常带前置空行/标题行（如生意参谋千牛表头在第 5 行）：
                # 跳过完全空白的前置行，第一行非空行作为表头
                if all(c == "" for c in cells):
                    continue
                header = cells
                continue
            row_number += 1
            record = {
                (header[j] if j < len(header) and header[j] else f"col_{j}"): (
                    "" if cell is None else str(cell)
                )
                for j, cell in enumerate(excel_row)
            }
            rows.append((row_number, record))
        return rows
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# bypass session 辅助（元数据 / 失败行 / 汇总）
# ---------------------------------------------------------------------------


async def _load_mapping(source: str, tenant_id: UUID, version: int | None) -> Any:
    """读取 field_mapping（按 batch.mapping_version；无版本 → None 恒等映射）。"""
    if version is None:
        return None
    from app.modules.importer.models import FieldMapping

    async with AsyncSessionBypass() as s:
        stmt = select(FieldMapping).where(
            FieldMapping.tenant_id == tenant_id,
            FieldMapping.source == source,
            FieldMapping.version == version,
        )
        return (await s.execute(stmt)).scalar_one_or_none()


async def _load_failed_rows(batch_id: UUID) -> list[tuple[int, dict[str, Any]]]:
    """only_failed 重试：用 import_job.raw_data 还原失败行（FB-E ②）。"""
    async with AsyncSessionBypass() as s:
        stmt = (
            select(ImportJob.row_number, ImportJob.raw_data)
            .where(ImportJob.batch_id == batch_id, ImportJob.status == "failed")
            .order_by(ImportJob.row_number.asc())
        )
        result = (await s.execute(stmt)).all()
    return [(int(rn), dict(rd)) for rn, rd in result]


async def _mark_batch_failed(batch_id: UUID, reason: str) -> None:
    """解析致命失败 / adapter 缺失 / 超行数 → batch.failed（bypass）。"""
    async with AsyncSessionBypass() as s:
        await s.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .values(status="failed", error_summary=reason[:2000], updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await s.commit()


async def _recount_from_jobs(s: Any, batch_id: UUID) -> _BatchCounts:
    """只重跑失败行时按 import_job 重新数七个计数（原地更新之后，§4.2）。"""
    by_status = dict(
        (
            await s.execute(
                select(ImportJob.status, func.count())
                .where(ImportJob.batch_id == batch_id)
                .group_by(ImportJob.status)
            )
        ).all()
    )
    warnings_len = func.jsonb_array_length(
        func.coalesce(ImportJob.notes["warnings"], text("'[]'::jsonb"))
    )
    filled_len = func.jsonb_array_length(
        func.coalesce(ImportJob.notes["filled"], text("'[]'::jsonb"))
    )
    warning_rows, filled_objects = (
        await s.execute(
            select(
                func.count().filter(warnings_len > 0),
                func.coalesce(func.sum(filled_len), 0),
            ).where(ImportJob.batch_id == batch_id)
        )
    ).one()
    counts = _BatchCounts()
    counts.imported = int(by_status.get("success", 0))
    counts.filled = int(by_status.get("filled", 0))
    counts.skipped = int(by_status.get("skipped", 0))
    counts.conflicted = int(by_status.get("conflict", 0))
    counts.failed = int(by_status.get("failed", 0))
    counts.warning_count = int(warning_rows or 0)
    counts.filled_objects = int(filled_objects or 0)
    return counts


async def _summarize_batch(batch_id: UUID, counts: _BatchCounts, only_failed: bool) -> str:
    """汇总段：更新 batch 七个计数 + 终态（completed / partial / failed）。

    - only_failed=False（首跑 / 整文件重试）：直接用本次执行的计数覆盖
    - only_failed=True（partial 重试）：按 import_job 重新数一遍（原地更新后）

    状态：没有失败行（且至少一行）→ completed；全部失败（含 0 行）→ failed；其余 partial。
    补空 / 重复已跳过 / 冲突都不算失败（8a-6；旧逻辑「imported == 0 判失败」会把全是跳过的
    批次判成失败）。
    """
    async with AsyncSessionBypass() as s:
        final = await _recount_from_jobs(s, batch_id) if only_failed else counts
        total_rows = final.total_rows
        failed_n = final.failed

        if failed_n == 0 and total_rows > 0:
            status = "completed"
        elif failed_n == total_rows:
            status = "failed"
        else:
            status = "partial"

        await s.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .values(
                status=status,
                total_rows=total_rows,
                imported=final.imported,
                failed=failed_n,
                filled=final.filled,
                skipped=final.skipped,
                conflicted=final.conflicted,
                warning_count=final.warning_count,
                filled_objects=final.filled_objects,
                error_summary=(f"{failed_n} 行失败" if failed_n else None),
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        await s.commit()
    return status


def _sanitize(exc: Exception) -> str:
    """脱敏行级错误信息（截断 + 仅类型 + message，不含 SQL / 栈）。"""
    msg = getattr(exc, "message", None) or str(exc)
    return f"{type(exc).__name__}: {msg}"[:1000]


# 导入路径上可能被两个批次同时撞到的唯一索引（goods_style_item 的成员行只挂在新商品上，撞不到，不列）
_CONCURRENT_UNIQUE = (
    "uq_style_code",
    "uq_sku_code",
    "uq_goods_main_code",
    "uq_blogger_xiaohongshu_id",
    "uq_import_conflict_pending",
)


def _row_error_detail(exc: Exception) -> str:
    """行失败原因。数据库异常绝不写 str(exc)：它带 [SQL] 与 [parameters]，参数里可能有成本价。"""
    if isinstance(exc, StatementError):  # DBAPIError 的父类；绑定参数处理失败（也带参数）同样拦住
        orig = exc.orig
        if isinstance(exc, IntegrityError) and any(
            name in str(orig) for name in _CONCURRENT_UNIQUE
        ):
            return "与另一批次同时写入，请重试"
        return f"数据库错误（{type(orig or exc).__name__}），请重试"
    return _sanitize(exc)


__all__ = ["run_import_batch"]
