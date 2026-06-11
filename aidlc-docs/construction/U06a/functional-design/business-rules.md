# U06a 业务规则（Business Rules）

> 单元：U06a — 统一导入框架  
> 覆盖故事：EP07-S07（上传）/ S08（hash 去重）/ S09（字段映射版本）/ S10（失败下载与重试）  
> 关键修订：FB-A（不用 attachment 表）/ FB-C（runner 持有事务+租户）/ FB-D（状态去重统一）/ FB-E（retry 两类失败分流）

---

## 1. 上传与文件处理（EP07-S07）

| 规则 | 描述 |
|---|---|
| BR-U06a-01 | 上传 `POST /api/import/upload`（multipart：file + source [+ mapping_version]），需权限 `import:write` |
| BR-U06a-02 | `source` 必须 ∈ `ImportAdapterRegistry.sources()`，否则 422（`IMPORT_SOURCE_UNKNOWN`），**不创建 batch**（FB：upload 时白名单校验） |
| BR-U06a-03 | 文件扩展名 ∈ {.csv, .xlsx}，MIME 校验；否则 422（`IMPORT_FORMAT_UNSUPPORTED`） |
| BR-U06a-04 | 文件大小 ≤ `IMPORT_MAX_FILE_MB`（默认 20MB），否则 422（`IMPORT_FILE_TOO_LARGE`） |
| BR-U06a-05 | 数据行数（不含表头）≤ `IMPORT_MAX_ROWS`（默认 5 万），否则 422（`IMPORT_TOO_MANY_ROWS`）；解析阶段在 run_import_batch 内二次校验（upload 时若能快速预估则提前拒） |
| BR-U06a-06 | upload 流程（FB-E / OQ2 后端中转）：① 流式读文件计算 **SHA256**；② 查去重（BR-U06a-10）；③ 写 R2 private（U01 `upload_bytes`，key=`imports/{tenant_id}/{batch_id}/{safe_filename}`）；④ 创建 `import_batch`（status=**processing**，FB-D）；⑤ `run_import_batch.delay(batch_id)`；⑥ 返回 batch（202 语义，body 含 batch_id + status） |
| BR-U06a-07 | `safe_filename` = 原始文件名去除路径分隔符与控制字符；R2 key 用 batch_id 隔离，避免同名覆盖 |
| BR-U06a-08 | 上传文件**不经 attachment ORM 表 / 通用 attachment API**（FB-A/FB-B）；`import_batch.file_r2_key` 直存，`file_bucket='private'` |

---

## 2. 文件 hash 去重（EP07-S08）

| 规则 | 描述 |
|---|---|
| BR-U06a-10 | 去重键 = `UNIQUE(tenant_id, source, file_hash)`（FB-D）。SHA256 在 upload **写 R2 之前**算（OQ2：服务端先算才能在建 batch 前去重） |
| BR-U06a-11 | 命中已存在 batch → 返回 **409**（`IMPORT_DUPLICATE_FILE`），message 含已有 `batch_id`，**不重复写 R2、不创建新 batch** |
| BR-U06a-12 | 不同 `source` 的相同字节文件允许各自导入（语义不同目标表）；同 source 同字节即重复 |
| BR-U06a-13 | mapping_version 不影响去重（OQ3：同文件不同 mapping 不放开重导；需要换映射重跑属罕见运维，V1 评估 force 标志） |

---

## 3. 字段映射版本管理（EP07-S09）

| 规则 | 描述 |
|---|---|
| BR-U06a-20 | `POST /api/import/field-mappings`（需 `import:write`，管理员场景）：新建 `field_mapping`，version = 该 (tenant, source) 当前 max + 1（首个 = 1） |
| BR-U06a-21 | 新建时**自动把同 (tenant, source) 旧 active 置 false**，新建记录 is_active=true（部分唯一约束保证同 source 仅一个 active，EP07-S09） |
| BR-U06a-22 | 旧版本记录**永久保留**（不删），供历史 batch 追溯 |
| BR-U06a-23 | upload 时若未显式传 mapping_version → 用当前 active 版本；显式传则用指定版本（须存在，否则 422）；batch 快照 `mapping_version` |
| BR-U06a-24 | run_import_batch 解析时按 batch.mapping_version 取对应 field_mapping（不取 active，保证历史 batch 重跑用原版本） |
| BR-U06a-25 | mapping_config 校验：columns 非空；每列 source_col / target_field 非空；type ∈ 白名单；date/datetime 的 transform 必填且为合法 strptime 格式 |

---

## 4. 异步解析与行级编排（EP07-S07，FB-C）

| 规则 | 描述 |
|---|---|
| BR-U06a-30 | `run_import_batch(batch_id)`（Celery default 队列）：用 **bypass session** 读 batch（拿 tenant_id/source/file_r2_key/created_by/mapping_version） |
| BR-U06a-31 | registry.get(batch.source) 缺失 → batch.status=`failed` + error_summary="adapter_not_registered" + return（FB：runner 二次防御 Adapter 未部署） |
| BR-U06a-32 | 从 R2 读文件（`get_signed_url` 或 client.get_object）；解析（csv / openpyxl read_only 流式） |
| BR-U06a-33 | **解析阶段失败**（文件损坏/格式错/超行数）→ batch.status=`failed`, total_rows=0, error_summary=脱敏原因, **不建 import_job 行**（FB-E ①） |
| BR-U06a-34 | 解析成功后设租户上下文（FB-C）：`tenant_id_ctx.set(batch.tenant_id)` + 用 engine_app 连接执行 **`SET app.tenant_id = '<tid>'`（会话级，非 LOCAL，跨 per-row 事务存活）** |
| BR-U06a-35 | 逐行 **独立事务**（每行 BEGIN/COMMIT）：parse_row → validate → upsert(session, tenant_id, actor_id=created_by)；成功 COMMIT + 写 import_job(success, target_resource_id)；失败 ROLLBACK 该行 |
| BR-U06a-36 | 行失败兜底（FB-C）：validate 错误或 upsert 异常 → 用**独立 bypass session** 写 import_job(failed, error_detail 脱敏)，防被该行回滚带走（复用 U05 `_log_event_dispatch_failure` 模式） |
| BR-U06a-37 | error_detail 脱敏：记 validate 错误描述 / 异常类型 + code，**不记** SQL / 完整堆栈 / 敏感字段值 |
| BR-U06a-38 | 汇总：imported = success 行数，failed = failed 行数；batch.status = `completed`（failed=0）/ `partial`（0<failed<total）/ `failed`（failed=total 或解析失败） |
| BR-U06a-39 | adapter.upsert **不自行 commit**（runner 控制事务边界，FB-C） |

---

## 5. 失败下载与重试（EP07-S10，FB-E）

| 规则 | 描述 |
|---|---|
| BR-U06a-40 | `GET /api/import/batches/{id}/errors/download`（需 `import:read`）：StreamingResponse CSV，列 = raw_data 原始列 + `error_detail` + `row_number`；仅含 import_job.status='failed' 行（实时生成，不落库） |
| BR-U06a-41 | batch.failed=0 时下载 → 返回空 CSV（仅表头）或 404（`IMPORT_NO_FAILED_ROWS`）；采用空 CSV（前端友好） |
| BR-U06a-42 | `POST /api/import/batches/{id}/retry`（需 `import:write`）：前置 retry_count < 3，否则 409（`IMPORT_RETRY_EXHAUSTED`）；**enqueue 前 retry_count += 1**（FB-E 递增时机） |
| BR-U06a-43 | **重试两类分流**（FB-E）：① batch 之前是 `failed` 且无 import_job 行（解析失败）→ 重跑整文件（从 R2 重读，重新解析）；② batch 是 `partial`（有 failed 行）→ **仅重跑 status='failed' 的 import_job 行**（按 raw_data 还原，原地更新该 job：attempt_count += 1 + 刷新 status/error_detail/target_resource_id） |
| BR-U06a-44 | 重试退避：Celery `countdown` 按 retry_count 取 1s（第1次）/ 5s（第2次）/ 30s（第3次）；最多 3 次（与 retry_count 上限一致） |
| BR-U06a-45 | 重试后重新汇总 batch.status（仅 failed 行全部转 success → completed；仍有 failed → partial） |
| BR-U06a-46 | 重试不改 file_hash / file_r2_key / 不放开去重（同文件仍占用 UNIQUE 槽位） |

---

## 6. 多租户与权限

| 规则 | 描述 |
|---|---|
| BR-U06a-50 | 所有表继承 TenantScopedModel + RLS；HTTP 路径靠中间件设租户上下文；**Celery worker 路径靠 runner 显式 `SET app.tenant_id`**（BR-U06a-34，FB-C） |
| BR-U06a-51 | 权限：`import:write`（upload/retry/创建 mapping）/ `import:read`（list/get/下载）；同租户内不限本人（运营协作） |
| BR-U06a-52 | batch / job / mapping 查询自动按 tenant_id 过滤（RLS + ORM 钩子双保险） |
| BR-U06a-53 | 审计：upload（import.upload）/ retry（import.retry）/ mapping 新建（import.field_mapping.create）/ batch 完成（import.batch_completed）写 audit_log，脱敏（不记文件内容/敏感字段值） |

---

## 7. 错误码矩阵

| 场景 | HTTP | code |
|---|---|---|
| source 不在 registry 白名单 | 422 | IMPORT_SOURCE_UNKNOWN |
| 格式不支持（非 csv/xlsx） | 422 | IMPORT_FORMAT_UNSUPPORTED |
| 文件超大 | 422 | IMPORT_FILE_TOO_LARGE |
| 行数超限 | 422 | IMPORT_TOO_MANY_ROWS |
| mapping_version 指定但不存在 | 422 | IMPORT_MAPPING_VERSION_NOT_FOUND |
| mapping_config 校验失败 | 422 | IMPORT_MAPPING_INVALID |
| 同 (tenant,source,hash) 重复文件 | 409 | IMPORT_DUPLICATE_FILE |
| retry 超过 3 次 | 409 | IMPORT_RETRY_EXHAUSTED |
| batch 不存在 | 404 | IMPORT_BATCH_NOT_FOUND |
| Adapter 未注册（runner 内） | — | batch.status=failed + error_summary（非 HTTP，异步） |
| 解析失败（异步） | — | batch.status=failed + error_summary |
| R2 未配置 / 上传失败 | 500 | IMPORT_STORAGE_ERROR |

---

## 8. 性能预估

| 维度 | 预估 |
|---|---|
| 单文件行数 | MVP ≤ 5 万行；10 万级分区留 V1 |
| 解析方式 | 流式（csv 逐行 / openpyxl read_only），内存 O(1) 行缓冲 |
| 行级入库 | 每行独立事务（一致性优先；批量优化留 V1，按 adapter 能力） |
| 上传响应 | upload 同步部分仅 hash + R2 写 + 建 batch，目标 P95 ≤ 1s（大文件 R2 写为主） |
| 解析吞吐 | 异步 Celery，单 batch 串行行处理；并发 batch 由 worker 并发度控制 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| EP07-S07 上传 → processing + 异步解析 | ✅ BR-01~08 |
| EP07-S08 hash 去重 409 | ✅ BR-10~13 |
| EP07-S09 映射版本（旧不删 + active 切换 + 历史快照） | ✅ BR-20~25 |
| EP07-S10 失败下载 + 仅 failed 行重试 + 退避 | ✅ BR-40~46 |
| FB-A 不用 attachment 表 | ✅ BR-08 |
| FB-C runner 事务 + 租户上下文 | ✅ BR-30~39, BR-50 |
| FB-D 状态/去重统一 | ✅ BR-06, BR-10 |
| FB-E retry 两类分流 + retry_count 时机 | ✅ BR-42~45 |
