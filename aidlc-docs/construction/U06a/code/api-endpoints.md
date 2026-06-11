# U06a API 端点清单（8 端点）

> 前缀：`/api/imports`（router prefix）
> 鉴权：全部需登录 + 强制改密后；权限见各端点（NF-5）
> 错误：业务异常经全局 error handler 映射为对应 HTTP 码

---

## 上传

### POST /api/imports/upload
- **权限**：`importer.batch:write`
- **请求**：multipart/form-data — `source`（必填）/ `file`（必填，CSV/XLSX）/ `mapping_version`（可选）
- **响应**：`202 Accepted` — `{ batch_id, status: "processing", source }`
- **流程**：source 白名单 → 格式校验 → L2 大小兜底（≤20MB） → 流式 hash → DB 先行 INSERT（UNIQUE 原子去重，NF-2） → 写 R2（含 batch_id 路径） → `run_import_batch.delay` 异步触发
- **错误**：
  - `422` IMPORT_SOURCE_UNKNOWN（source 未注册）/ IMPORT_FORMAT_UNSUPPORTED（非 CSV/XLSX）/ IMPORT_FILE_TOO_LARGE（超 20MB）/ IMPORT_MAPPING_VERSION_NOT_FOUND
  - `409` IMPORT_DUPLICATE_FILE（同 tenant+source+hash 已存在，返回 existing_batch_id）
  - `500` IMPORT_STORAGE_ERROR（R2 写失败，已补偿无孤儿）

---

## 批次查询

### GET /api/imports/batches
- **权限**：`importer.batch:read`
- **查询**：`page` / `page_size`（≤100）/ `source` / `batch_status` / `created_at_from` / `created_at_to`
- **响应**：`200` — `ImportBatchPage { items[], total, page, page_size }`

### GET /api/imports/batches/{batch_id}
- **权限**：`importer.batch:read`
- **响应**：`200` ImportBatchResponse；`404` IMPORT_BATCH_NOT_FOUND（含跨租户保护）

---

## 重试 + 失败下载

### POST /api/imports/batches/{batch_id}/retry
- **权限**：`importer.batch:write`
- **流程**：原子 claim（NF-3，仅 partial/failed 且 retry_count<3）→ FB-E 两类分流（partial→only_failed / failed→整文件）→ `apply_async`（countdown 1/5/30s）
- **响应**：`200` ImportBatchResponse（status=processing, retry_count+1）
- **错误**：`404` 不存在；`409` IMPORT_RETRY_EXHAUSTED（retry_count≥3）/ IMPORT_BATCH_BUSY（正在处理中）

### GET /api/imports/batches/{batch_id}/errors/download
- **权限**：`importer.batch:read`
- **响应**：`200` text/csv（UTF-8 BOM + csv_safe 注入防护）— 列：row_number / error_detail / attempt_count / raw_data(JSON)
- **错误**：`404` 不存在

---

## 字段映射版本

### POST /api/imports/field-mappings
- **权限**：`importer.mapping:write`
- **请求**：`{ source, columns: [{ source_col, target_field, required?, type?, transform? }] }`
- **流程**：validate_mapping_config → next_version → 旧 active 同事务下线 → 插入新 active
- **响应**：`201` FieldMappingResponse
- **错误**：`422` IMPORT_MAPPING_INVALID（columns 校验失败）

### GET /api/imports/field-mappings
- **权限**：`importer.batch:read`
- **查询**：`source`（必填）
- **响应**：`200` FieldMappingResponse[]（version 倒序）

### GET /api/imports/field-mappings/active
- **权限**：`importer.batch:read`
- **查询**：`source`（必填）
- **响应**：`200` FieldMappingResponse | null

---

## 权限矩阵（NF-5）

| 角色 | importer.batch:read | importer.batch:write | importer.mapping:write |
|---|---|---|---|
| admin / platform_admin | ✅（*） | ✅（*） | ✅（*） |
| pr | ✅ | ✅ | — |
| pr_manager | ✅ | ✅ | ✅ |
| operations | ✅ | — | — |

> admin/platform_admin 持 `*` 通配；operations 另有 `importer.*:read` 覆盖 batch:read。
