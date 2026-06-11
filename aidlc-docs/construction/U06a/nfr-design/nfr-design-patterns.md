# U06a NFR 设计模式（NFR Design Patterns）

> 单元：U06a — 统一导入框架  
> 范围：5 个增量模式（P-U06a-01~05）；通用模式继承 U01-U05  
> 已应用 6 条 P1/P2 反馈修正（NF-1~6）

---

## P-U06a-01：导入 Runner 事务 + 租户上下文（FB-C + NF-1）

### 问题
run_import_batch 跑在 Celery worker（无 HTTP CurrentUser / 无中间件设租户上下文）。需保证：
- 行级独立事务（一行失败不影响其他，FB-C）
- 每行 adapter.upsert 受 RLS 约束（按 batch.tenant_id 隔离）
- **租户上下文不泄漏到连接池**（NF-1：会话级 SET 残留风险）
- 失败行记录不被该行回滚带走

### 方案：双 session + per-row SET LOCAL

```python
# tasks/import_tasks.py
async def _run_import_batch(batch_id: UUID, only_failed: bool = False) -> dict:
    # ── 1. 元数据读取 + 状态守卫（bypass，系统级）──
    async with AsyncSessionBypass() as meta_s:
        batch = await meta_s.get(ImportBatch, batch_id)
        if batch is None:
            return {"status": "not_found"}
        # NF-3 runner 入口守卫：仅 processing 可执行
        if batch.status != "processing":
            log.warning("import_batch_not_processing", extra={"batch_id": str(batch_id), "status": batch.status})
            return {"status": "skipped_not_processing"}
        tenant_id = batch.tenant_id
        source = batch.source
        created_by = batch.created_by
        file_r2_key = batch.file_r2_key
        mapping_version = batch.mapping_version

    # ── 2. Adapter 解析（NF-4：worker 已注册）──
    adapter = ImportAdapterRegistry.get(source)
    if adapter is None:
        await _mark_batch_failed(batch_id, "adapter_not_registered")
        import_batch_total.labels(source=source, status="failed").inc()
        return {"status": "failed", "reason": "adapter_not_registered"}

    mapping = await _load_mapping(source, mapping_version)  # bypass session

    # ── 3. 取文件 + 解析（解析致命失败 → batch.failed，FB-E ①）──
    try:
        raw = attachment_service.get_object_bytes(batch.file_bucket, file_r2_key)  # NF-2/FB-A: U01 helper
        rows = _parse_rows(raw, batch.original_filename)   # csv / openpyxl read_only
    except Exception as exc:  # noqa: BLE001
        await _mark_batch_failed(batch_id, f"parse_error:{type(exc).__name__}")
        sentry_sdk.capture_exception(exc)
        import_batch_total.labels(source=source, status="failed").inc()
        return {"status": "failed", "reason": "parse_error"}

    # only_failed：用 import_job.raw_data 还原失败行（FB-E ②）
    if only_failed:
        rows = await _load_failed_rows(batch_id)   # [(row_number, raw_data), ...]

    if len(rows) > settings.IMPORT_MAX_ROWS:       # NF-6 行数限在解析阶段
        await _mark_batch_failed(batch_id, "too_many_rows")
        return {"status": "failed", "reason": "too_many_rows"}

    # ── 4. 设上下文（tenant_id_ctx 供 ORM 钩子/audit；RLS 靠 per-row SET LOCAL）──
    tok = tenant_id_ctx.set(tenant_id)
    start = time.perf_counter()
    imported = failed = 0
    try:
        for row_number, row in _iter(rows, only_failed):
            ok = await _process_one_row(
                adapter, row, row_number, mapping,
                tenant_id=tenant_id, actor_id=created_by, only_failed=only_failed,
            )
            if ok:
                imported += 1
                import_rows_total.labels(source=source, result="success").inc()
            else:
                failed += 1
                import_rows_total.labels(source=source, result="failed").inc()
    finally:
        tenant_id_ctx.reset(tok)
        import_batch_duration_seconds.labels(source=source).observe(time.perf_counter() - start)

    # ── 5. 汇总（bypass）──
    status = await _summarize_batch(batch_id, imported, failed, only_failed)
    import_batch_total.labels(source=source, status=status).inc()
    return {"status": status, "imported": imported, "failed": failed}


async def _process_one_row(adapter, row, row_number, mapping, *, tenant_id, actor_id, only_failed) -> bool:
    """每行独立事务 + per-row SET LOCAL（NF-1 防连接池串租）。"""
    try:
        parsed = adapter.parse_row(row, mapping)
        errs = adapter.validate(parsed)
        if errs:
            raise RowValidationError("; ".join(errs))

        async with AsyncSessionApp() as app_s:
            # NF-1：SET LOCAL（事务级），commit/rollback 后自动失效，绝不残留 pool
            await app_s.execute(
                text("SET LOCAL app.tenant_id = :tid"), {"tid": str(tenant_id)}
            )
            rid, _inserted = await adapter.upsert(
                parsed, session=app_s, tenant_id=tenant_id, actor_id=actor_id
            )
            # 成功 job 与业务记录同 per-row 事务（原子，Q3）
            await _write_job_success(app_s, row_number, row, rid, only_failed)
            await app_s.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        # 失败行用独立 bypass session 写（防被回滚带走，FB-C + U05 模式）
        await _write_job_failed_bypass(row_number, row, _sanitize(exc), only_failed)
        return False
```

### 关键点
- **NF-1 核心**：`SET LOCAL app.tenant_id` 在每个 per-row 事务内重新设置；事务结束（commit/rollback）即失效，连接归还 pool 时无残留。**绝不用会话级 `SET`**（会泄漏给复用连接的下个任务）。
- 双 session：bypass（元数据/失败 job/汇总，系统级）+ app（per-row upsert，RLS 约束）
- 成功 job 同 per-row 事务（原子）；失败 job 独立 bypass（不被回滚带走）
- only_failed：原地 UPDATE import_job（attempt_count += 1）

---

## P-U06a-02：ImportAdapter 协议 + Registry（FB-C + NF-4）

### 注册机制（与 U05 listener 同模式）

```python
# modules/importer/registry.py
class ImportAdapterRegistry:
    _adapters: dict[str, ImportAdapter] = {}

    @classmethod
    def register(cls, adapter: ImportAdapter) -> None:
        cls._adapters[adapter.source] = adapter   # 启动期单线程，运行期只读

    @classmethod
    def get(cls, source: str) -> "ImportAdapter | None":
        return cls._adapters.get(source)

    @classmethod
    def sources(cls) -> frozenset[str]:
        return frozenset(cls._adapters.keys())

    @classmethod
    def clear(cls) -> None:   # 测试用
        cls._adapters.clear()
```

### 双进程加载（NF-4 关键）

```python
# main.py lifespan（HTTP 进程）
def register_import_adapters() -> None:
    for mod, reg in [
        ("app.modules.importer.adapters.style_sku", "register"),   # U06b
        ("app.modules.importer.adapters.blogger", "register"),     # U06c
        ("app.modules.importer.adapters.promotion", "register"),   # U06d
        ("app.modules.importer.adapters.settlement", "register"),  # U06e
    ]:
        try:
            module = importlib.import_module(mod)
            getattr(module, reg)()
        except ModuleNotFoundError:
            log.warning("import_adapter_module_not_found", extra={"module": mod})  # 未部署不阻塞

# core/celery_app.py — worker 进程也要注册（HTTP 注册 worker 看不到）
from celery.signals import worker_process_init

@worker_process_init.connect
def _register_adapters_in_worker(**_kwargs):
    from app.main import register_import_adapters
    register_import_adapters()

# NF-4：autodiscover 必须包含 import_tasks，否则 run_import_batch.delay 找不到任务
celery_app.autodiscover_tasks([
    "app.tasks.backup_tasks",
    "app.tasks.cleanup_tasks",
    "app.tasks.import_tasks",   # ← U06a 新增
])
```

> upload 端点校验 `source ∈ ImportAdapterRegistry.sources()`（不在 → 422）；runner 内 `registry.get` 二次防御（缺失 → batch.failed）。

---

## P-U06a-03：DB 先行上传 + hash 去重（FB-A/FB-D + NF-2）

### 问题（NF-2）
原"SELECT 去重 → R2 写 → 建 batch"顺序：并发上传同文件时两请求都查不到重复、都写 R2，然后一个 UNIQUE 插入失败 → 孤儿 R2 文件。

### 方案：DB 先行（UNIQUE 原子去重）+ 补偿删除

```python
# modules/importer/service.py
async def upload(self, file, source, mapping_version=None) -> ImportBatch:
    # 0. source 白名单（NF：upload 时拒未注册）
    if source not in ImportAdapterRegistry.sources():
        raise ImportSourceUnknownError(source)

    # 1. 流式算 SHA256 + 大小校验（NF-6 handler 兜底层）
    file_hash, size_bytes, content = await self._read_and_hash(file)  # 超 20MB → 422
    import_file_size_bytes.labels(source=source).observe(size_bytes)

    # 2. 解析 mapping_version（active 或指定）
    resolved_version = await self._resolve_mapping_version(source, mapping_version)

    # 3. NF-2 DB 先行：先 INSERT batch，靠 UNIQUE(tenant,source,hash) 原子拦并发
    batch_id = uuid4()
    r2_key = f"imports/{tenant_id}/{batch_id}/{_safe_filename(file.filename)}"
    batch = ImportBatch(
        id=batch_id, tenant_id=tenant_id, source=source,
        file_hash=file_hash, original_filename=file.filename,
        file_r2_key=r2_key, file_bucket=settings.IMPORT_BUCKET,
        mapping_version=resolved_version, status="processing",
        created_by=user.id,
    )
    self._session.add(batch)
    try:
        await self._session.flush()        # UNIQUE 冲突在此抛 IntegrityError
    except IntegrityError:
        await self._session.rollback()
        existing = await self._repo.find_by_hash(tenant_id, source, file_hash)
        raise ImportDuplicateFileError(batch_id=existing.id if existing else None)  # 409

    # 4. flush 成功才写 R2（key 含 batch_id，不会与并发冲突）
    try:
        self._attachment.upload_bytes(content, bucket=settings.IMPORT_BUCKET,
                                      key=r2_key, content_type=_content_type(file))
    except Exception as exc:  # noqa: BLE001
        # NF-2 补偿：R2 写失败 → 删 batch 行（尚未 commit → rollback 即可）
        await self._session.rollback()
        raise ImportStorageError() from exc   # 500，无孤儿（batch 未 commit，R2 未写成功）

    await self._audit.log(action="import.upload", resource="import_batch",
                          resource_id=batch_id, after={"source": source, "file_hash": file_hash})
    await self._session.commit()
    run_import_batch.delay(str(batch_id))     # 异步触发
    return batch
```

### 关键点
- **DB 先行**：UNIQUE(tenant_id, source, file_hash) 是并发去重的唯一权威；不靠 SELECT（TOCTOU）
- **R2 key 含 batch_id**：每请求独立 key，并发不互相覆盖
- **补偿**：flush 后、commit 前写 R2；R2 失败 rollback batch（无孤儿）。若已 commit 后才发现 R2 异常（不会发生于本顺序），则需显式 `attachment_service.delete`
- 用 **U01 `upload_bytes` / `get_object_bytes`**（FB-A：不经 Attachment ORM / ALLOWED_PURPOSES）

---

## P-U06a-04：两类失败重试 + 批次互斥（FB-E + NF-3）

### 原子 processing claim（NF-3 批次互斥）

```python
# modules/importer/repository.py
async def claim_for_retry(self, batch_id, tenant_id) -> ImportBatch | None:
    """原子领取 batch 用于 retry：仅 partial/failed 且 retry_count<3 可领。
    0 行 = 已在跑 / 已耗尽 / 状态不符 → service 转 409。"""
    stmt = (
        update(ImportBatch)
        .where(
            ImportBatch.id == batch_id,
            ImportBatch.tenant_id == tenant_id,
            ImportBatch.status.in_(["partial", "failed"]),
            ImportBatch.retry_count < 3,
        )
        .values(status="processing", retry_count=ImportBatch.retry_count + 1,
                updated_at=func.now())
        .returning(ImportBatch)
        .execution_options(synchronize_session=False)
    )
    row = (await self._session.execute(stmt)).fetchone()
    if row is None:
        return None
    batch = row[0]
    await self._session.refresh(batch)
    return batch
```

```python
# service.retry
async def retry(self, batch_id, user) -> ImportBatch:
    batch = await self._repo.get_by_id(batch_id)
    if batch is None:
        raise ImportBatchNotFoundError(batch_id)
    claimed = await self._repo.claim_for_retry(batch_id, user.tenant_id)  # NF-3 原子
    if claimed is None:
        # 区分耗尽 vs 忙碌
        if batch.retry_count >= 3:
            raise ImportRetryExhaustedError(batch_id)        # 409
        raise ImportBatchBusyError(batch_id)                 # 409（已在 processing）
    await self._session.commit()
    import_retry_total.labels(source=batch.source).inc()

    # FB-E 两类分流
    only_failed = (batch.failed > 0)   # partial 有 failed 行 → only_failed；解析失败(failed 行=0)→整文件
    countdown = {1: 1, 2: 5, 3: 30}[claimed.retry_count]
    run_import_batch.apply_async(
        args=[str(batch_id)], kwargs={"only_failed": only_failed}, countdown=countdown
    )
    return claimed
```

### only_failed 行级并发防护
- 改 failed job 前 `SELECT ... FOR UPDATE`（防同 batch 两个 runner 并发改同 job —— 正常被 claim 互斥挡住，FOR UPDATE 是二次防御）
- 原地 UPDATE import_job（attempt_count += 1），靠 `UNIQUE(batch_id, row_number)` 不产生重复行

---

## P-U06a-05：安全文件处理（FB Q11 + NF-5 + NF-6）

### 三层大小/行数防护（NF-6）

| 层 | 校验 | 位置 |
|---|---|---|
| L1 网关 | `client_max_body_size 21m`（nginx）+ uvicorn `--limit-max-requests` / Starlette body 上限 | 部署配置（multipart 落盘前挡） |
| L2 handler | 累计读取字节 ≤ IMPORT_MAX_FILE_MB（20MB）→ 422 | upload service（业务兜底） |
| L3 解析 | 流式计数行数 ≤ IMPORT_MAX_ROWS（5 万）→ batch.failed | run_import_batch（upload 时无法预知行数） |

### 文件解析安全
- openpyxl `load_workbook(read_only=True, data_only=True)`：流式 + 读公式计算值（不读公式串，不执行宏）
- 扩展名 + MIME 双白名单（.csv / .xlsx）
- R2 key `imports/{tenant_id}/{batch_id}/` + safe_filename（去路径分隔符/控制字符）防穿越

### CSV injection 防护（仅下载导出，Q10）
```python
# modules/importer/domain.py
_DANGEROUS = ("=", "+", "-", "@")
def csv_safe(value: str) -> str:
    """失败明细 CSV 导出时对危险前缀加 ' 防 Excel 公式执行。导入解析不转义（raw_data 保真）。"""
    if value and value[0] in _DANGEROUS:
        return "'" + value
    return value
```

### 权限对齐（NF-5）
- scope：`importer.batch:read`（list/get/下载）/ `importer.batch:write`（upload/retry）/ `importer.mapping:write`（创建映射，管理员场景）
- default_roles 更新：operations += importer.batch:read；pr/pr_manager += importer.batch:write；pr_manager/admin += importer.mapping:write
- permission 表 seed 新增这 3 个 scope（003 seed 同步，或 U06a migration 追加）

---

## 指标埋点位置汇总

| 指标 | 埋点 |
|---|---|
| `import_file_size_bytes{source}` | upload service（算 hash 后） |
| `import_batch_total{source,status}` | run_import_batch 汇总段 + adapter 缺失/解析失败分支 |
| `import_rows_total{source,result}` | _process_one_row 成功/失败 |
| `import_batch_duration_seconds{source}` | run_import_batch finally |
| `import_retry_total{source}` | service.retry（claim 成功后） |

---

## 一致性校验

| 模式 | 反馈 | 结果 |
|---|---|---|
| P-U06a-01 | NF-1 per-row SET LOCAL（非会话级） | ✅ _process_one_row |
| P-U06a-02 | NF-4 autodiscover import_tasks + worker 注册 | ✅ celery_app |
| P-U06a-03 | NF-2 DB 先行 + UNIQUE 原子 + 补偿删除 | ✅ upload |
| P-U06a-04 | NF-3 原子 claim + FOR UPDATE | ✅ claim_for_retry |
| P-U06a-05 | NF-5 权限对齐 + NF-6 三层 body 上限 | ✅ §权限/三层防护 |
| 全部 | FB-A 用 U01 R2 helper不碰 Attachment ORM | ✅ get_object_bytes/upload_bytes |
