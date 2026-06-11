# U06a 业务逻辑模型（Business Logic Model）

> 单元：U06a — 统一导入框架  
> 范围：5 个 Use Case 流程 + 端到端时序 + Adapter 注册契约  
> 关键修订：FB-A（U01 R2 helper）/ FB-C（runner 事务+租户）/ FB-D（processing 起点）/ FB-E（retry 两类分流）

---

## UC-1：上传文件创建导入批次（EP07-S07 + S08，后端中转）

**Actor**：PR / 运营（`import:write`）  
**入口**：`POST /api/import/upload`（multipart：file + source [+ mapping_version]）

```
1. 鉴权 + require_permission("import","write")
2. 校验 source ∈ ImportAdapterRegistry.sources()
   └─ 否 → 422 IMPORT_SOURCE_UNKNOWN（不建 batch）
3. 校验扩展名/MIME ∈ {csv, xlsx}
   └─ 否 → 422 IMPORT_FORMAT_UNSUPPORTED
4. 流式读取 file → 计算 SHA256（同时校验大小 ≤ 20MB）
   └─ 超大 → 422 IMPORT_FILE_TOO_LARGE
5. 去重：SELECT import_batch WHERE (tenant_id, source, file_hash)
   └─ 命中 → 409 IMPORT_DUPLICATE_FILE（message 含已有 batch_id；不写 R2/不建 batch）
6. 解析 mapping_version：
   ├─ 未传 → 取当前 active field_mapping.version（无 active → NULL 恒等映射）
   └─ 显式传 → 校验存在（否则 422 IMPORT_MAPPING_VERSION_NOT_FOUND）
7. 生成 batch_id（uuid4）→ r2_key = imports/{tenant_id}/{batch_id}/{safe_filename}
8. AttachmentService.upload_bytes("private", r2_key, file_bytes, content_type)   ← U01 R2 helper（FB-A）
   └─ R2 未配置/失败 → 500 IMPORT_STORAGE_ERROR
9. INSERT import_batch(status=processing, file_hash, file_r2_key, file_bucket=private,
                       mapping_version, total_rows=0, created_by=user.id)        ← FB-D 直接 processing
10. audit("import.upload", batch_id)；commit
11. run_import_batch.delay(batch_id)                                              ← Celery 异步
12. 返回 202 + ImportBatchResponse(batch_id, status=processing)
```

**关键**：hash 必须在写 R2 / 建 batch **之前**算（OQ2），否则无法在创建前去重。

---

## UC-2：异步解析入库（EP07-S07，Celery runner，FB-C）

**Actor**：系统（Celery worker，无 HTTP CurrentUser）  
**入口**：`run_import_batch(batch_id)`（tasks/import_tasks.py）

```
1. async with AsyncSessionBypass() as s0:                       ← bypass 读 batch（系统任务）
       batch = await s0.get(ImportBatch, batch_id)
       （取 tenant_id / source / file_r2_key / created_by / mapping_version）
2. adapter = ImportAdapterRegistry.get(batch.source)
   └─ None → batch.status=failed, error_summary="adapter_not_registered"；return   ← FB runner 二次防御
3. mapping = FieldMappingService.get_by_version(batch.source, batch.mapping_version)  （可能 None=恒等）
4. 从 R2 读文件（get_object(file_bucket, file_r2_key)）→ 解析（csv / openpyxl read_only）
   └─ 解析异常/超行数 → batch.status=failed, total_rows=0, error_summary=脱敏；return  ← FB-E ① 解析失败
5. 设租户上下文（FB-C 关键）：
       tenant_id_ctx.set(batch.tenant_id)
       async with AsyncSessionApp() as app_s:
           await app_s.execute(text("SET app.tenant_id = :tid"), {"tid": str(batch.tenant_id)})  ← 会话级非 LOCAL
6. for row_number, row in enumerate(rows, start=1):
       try:
           parsed = adapter.parse_row(row, mapping)
           errs = adapter.validate(parsed)
           if errs: raise RowValidationError(errs)
           rid, inserted = await adapter.upsert(parsed, session=app_s,
                                                tenant_id=batch.tenant_id, actor_id=batch.created_by)
           await app_s.commit()                                   ← per-row 独立事务成功
           _write_job(success, row_number, raw=row, target=rid)   ← 成功 job（同 app_s 或随后）
       except Exception as exc:
           await app_s.rollback()                                 ← 该行回滚
           _write_job_failed(row_number, raw=row, error=脱敏(exc))  ← 独立 bypass session 写（FB-C 兜底）
7. 汇总（bypass session）：
       imported = COUNT(job.success), failed = COUNT(job.failed)
       batch.status = completed(failed=0) / partial(0<failed<total) / failed(failed=total)
       batch.total_rows / imported / failed 更新
8. audit("import.batch_completed", batch_id, {imported, failed})；commit
```

> **per-row 事务 + 失败行 bypass 写**：保证"一行失败不影响其他行"（services.md §2.5），且失败记录不被该行 rollback 带走。
> **SET app.tenant_id 会话级**：跨多个 per-row commit 仍有效（LOCAL 会在每次 commit 后失效，故用 SET 而非 SET LOCAL，FB-C）。

---

## UC-3：字段映射版本管理（EP07-S09）

**Actor**：管理员（`import:write`）  
**入口**：`POST /api/import/field-mappings`（source + mapping_config）

```
1. 鉴权 + 校验 mapping_config（columns 非空 / 字段完整 / type 白名单 / date transform 合法）
   └─ 否 → 422 IMPORT_MAPPING_INVALID
2. version = (SELECT COALESCE(MAX(version),0)+1 FROM field_mapping WHERE tenant_id+source)
3. 事务内：
       UPDATE field_mapping SET is_active=false WHERE (tenant_id, source) AND is_active   ← 旧 active 下线
       INSERT field_mapping(source, version, mapping_config, is_active=true, created_by)   ← 新版本生效
   （部分唯一 UNIQUE(tenant_id, source) WHERE is_active 保证同 source 仅一个 active）
4. audit("import.field_mapping.create", {source, version})；commit
5. 返回 FieldMappingResponse
```

读：`GET /api/import/field-mappings?source=...`（list 全版本）/ `GET .../active`（当前生效）。

> 历史 batch 用 `batch.mapping_version` 快照，不受后续版本切换影响（EP07-S09："查询历史 batch 时记录使用的版本"）。

---

## UC-4：失败明细下载（EP07-S10）

**Actor**：PR / 运营（`import:read`）  
**入口**：`GET /api/import/batches/{id}/errors/download`

```
1. 鉴权 + 取 batch（不存在 → 404 IMPORT_BATCH_NOT_FOUND）
2. 查 import_job WHERE batch_id AND status='failed' ORDER BY row_number
3. StreamingResponse(CSV)：
       表头 = raw_data 的列并集 + ["row_number", "error_detail"]
       每行 = job.raw_data 各列值 + row_number + error_detail
4. Content-Disposition: attachment; filename="errors_{batch_id}.csv"
5. failed=0 → 返回仅表头空 CSV（前端友好；不报错）
```

> 实时生成，不落库。用户修正后可重新上传（新文件新 hash）或走 retry（若原 batch 仍可重试）。

---

## UC-5：重试（EP07-S10，FB-E 两类分流）

**Actor**：PR / 运营（`import:write`）  
**入口**：`POST /api/import/batches/{id}/retry`

```
1. 鉴权 + 取 batch（不存在 → 404）
2. 前置：batch.retry_count < 3，否则 409 IMPORT_RETRY_EXHAUSTED
3. retry_count += 1（enqueue 前递增，FB-E 时机）；batch.status=processing；commit
4. countdown = {1:1s, 2:5s, 3:30s}[retry_count]
5. 分流（FB-E）：
   ├─ 情况①：batch 原为 failed 且无 import_job 行（解析阶段失败）
   │         → run_import_batch.apply_async((batch_id,), countdown=countdown)   ← 重跑整文件
   └─ 情况②：batch 原为 partial（有 failed 行）
             → run_import_batch.apply_async((batch_id,),
                   kwargs={"only_failed": True}, countdown=countdown)            ← 仅重跑 failed 行
6. 返回 ImportBatchResponse(status=processing)

run_import_batch(only_failed=True) 行为差异：
   - 不重新解析整文件；查 import_job WHERE status='failed'
   - 对每条 failed job：用 job.raw_data 还原行 → parse/validate/upsert
   - 原地更新该 job：attempt_count += 1，成功→status=success+target_resource_id，失败→刷新 error_detail
   - 重新汇总 batch.status（仅 failed 全转 success → completed；仍有 failed → partial）
```

---

## 6. 端到端时序（upload → 入库 → 汇总）

```
PR/运营 ──upload(file,source)──▶ ImportService.upload
                                   │ SHA256 + 去重 + upload_bytes(R2 private) + INSERT batch(processing)
                                   │ commit + run_import_batch.delay
                                   ▼ (202 batch_id)
                          [Celery worker] run_import_batch(batch_id)
                                   │ bypass 读 batch → registry.get(source)
                                   │ R2 get_object → 解析
                                   │ SET app.tenant_id（会话级）
                                   │ for each row: [BEGIN parse→validate→adapter.upsert→COMMIT] / [ROLLBACK+failed job(bypass)]
                                   │ 汇总 → batch.status=completed/partial/failed
                                   ▼
PR/运营 ──GET batch──▶ imported/failed
        ──GET errors/download──▶ 失败明细 CSV（partial 时）
        ──POST retry──▶ 仅 failed 行重跑（partial）/ 整文件重跑（failed 解析失败）
```

---

## 7. Adapter 注册契约时序（与 U06b/c/d/e）

```
[启动] main.py lifespan:
    register_import_adapters():
        try: from app.modules.importer.adapters.style_sku import register as r_sku; r_sku()   # U06b
        except ModuleNotFoundError: log.warning(...)   # 适配器未部署不阻塞框架
        ... (U06c/d/e 同)

[运行] upload: 校验 source ∈ ImportAdapterRegistry.sources()
[运行] run_import_batch: adapter = ImportAdapterRegistry.get(batch.source)

U06b/c/d/e 各自 adapters/<x>.py:
    class XxxImportAdapter:  # 实现 ImportAdapter 协议
        source = "manual_xxx"; target_table = "xxx"
        def parse_row(...); def validate(...); async def upsert(parsed,*,session,tenant_id,actor_id)
    def register(): ImportAdapterRegistry.register(XxxImportAdapter())
```

> 与 U05 finance/promotion listener 注册同模式：框架定义协议+registry，业务单元注册实现，main.py 启动加载，缺失模块 warning 不阻塞。

---

## 8. 与下游/相关单元的引用契约

| 单元 | 契约 |
|---|---|
| U06b/c/d/e | 实现 ImportAdapter（parse_row/validate/upsert(session,tenant_id,actor_id)）+ register()；复用各自 repository 的幂等 upsert |
| U13 | 自动采集 Worker 上传文件后复用 `run_import_batch`；Qianniu/Wanxiangtai/Huitun Adapter 注册到同 registry；data_quality_issue 由 U13 在 adapter 内或汇总后写 |
| U12 | credential 与 importer 解耦（U13 用）；本单元不引用 |
| U09 | import:read/write 权限字符串 → 字段级权限体系（本单元 require_permission 过渡） |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| UC 覆盖 EP07-S07~S10 | ✅ UC-1~5 |
| FB-A 文件用 U01 R2 helper（upload_bytes/get_object） | ✅ UC-1 step8 / UC-2 step4 |
| FB-C runner 持有事务 + SET app.tenant_id 会话级 + adapter 收 session/tenant/actor | ✅ UC-2 step5-6 |
| FB-D upload 直接 processing | ✅ UC-1 step9 |
| FB-E retry 两类分流 + only_failed + attempt_count | ✅ UC-5 |
| 每行独立事务 + 失败 bypass 写 | ✅ UC-2 step6 |
| Adapter 注册与 U05 listener 同模式 | ✅ §7 |
