# U06a 基础设施设计（Infrastructure Design）

> 单元：U06a — 统一导入框架  
> 范围：U06a 基础设施增量；通用基础设施全部继承 U01 + shared-infrastructure  
> 关键差异：Celery worker 任务发现扩展 + nginx body 上限 + R2 imports/ 路径 + worker Adapter 注册

---

## 1. 资源增量清单

| 资源 | 增量 | 来源 |
|---|---|---|
| PostgreSQL | 3 新表（import_batch / import_job / field_mapping）+ 约束 + 索引 + 2 RLS + permission seed | migration 010 |
| R2 | private 桶 `imports/` 子目录首次使用（U01 已 provisioning，仅消费，不经 attachment ORM，FB-A） | U01 |
| Celery | autodiscover 加 `app.tasks.import_tasks`（NF-4）+ worker_process_init 注册 Adapter | core/celery_app.py |
| nginx / ASGI | `client_max_body_size 21m`（NF-6） | frontend/nginx.conf + uvicorn/Starlette |
| env | IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_RETENTION_DAYS / IMPORT_BUCKET | core/config.py |
| 依赖 | openpyxl==3.1.5 | requirements.txt |
| Sentry | `module=importer` tag | 复用 U01 backend 项目 |
| Prometheus | 5 个新指标 | core/metrics.py |
| Zeabur 服务 | **不新增**（复用 backend + celery-worker） | U01 |

---

## 2. PostgreSQL

### 2.1 新表（migration 010）
- `import_batch`：导入批次。约束 `UNIQUE(tenant_id, source, file_hash)`（NF-2 并发去重权威）+ status/retry_count CHECK。索引 idx_import_batch_tenant_status / idx_import_batch_source。
- `import_job`：行级结果。`UNIQUE(batch_id, row_number)`（NF-3 行幂等）+ status CHECK。索引 idx_import_job_batch_status。FK batch_id ON DELETE CASCADE。
- `field_mapping`：映射版本。`UNIQUE(tenant_id, source, version)` + 部分 `UNIQUE(tenant_id, source) WHERE is_active`。索引 idx_field_mapping_active。

> 均继承 TenantScopedModel → 自动 tenant_id + RLS（与 U01-U05 同模式）。RLS 策略走 `enable_rls_sql`（U06a code gen 阶段已修为单条 DO 块，asyncpg 兼容，见 MVP-end Build & Test 修复）。

### 2.2 permission seed（NF-5）
010 migration 内 INSERT permission 表 + role_permission 关联：
- `importer.batch:read` → admin / operations / pr / pr_manager
- `importer.batch:write` → admin / pr / pr_manager
- `importer.mapping:write` → admin / pr_manager
幂等（NOT EXISTS）。同步更新 default_roles.py 代码常量（003 seed 与运行时一致）。

---

## 3. R2 imports/ 路径

| 项 | 值 |
|---|---|
| 桶 | private（IMPORT_BUCKET，固定） |
| key | `imports/{tenant_id}/{batch_id}/{safe_filename}` |
| 写入 | upload service `attachment_service.upload_bytes("private", key, ...)`（U01 helper，FB-A） |
| 读取 | run_import_batch `attachment_service.get_object_bytes("private", key)`（U01 helper 新增薄封装） |
| 生命周期 | MVP 保留（retry 重读 + 审计）；IMPORT_RETENTION_DAYS=0 不清理；V1 加 beat 清理 |
| 隔离 | tenant_id + batch_id 双层路径前缀，防跨租户/跨批次访问；safe_filename 去路径分隔符防穿越 |

> **不经 attachment ORM 表 / 通用 attachment API / ALLOWED_PURPOSES**（FB-A）。仅用 U01 R2 helper 直接读写。

---

## 4. Celery 任务发现 + Adapter 注册（NF-4）

```python
# core/celery_app.py
celery_app.autodiscover_tasks([
    "app.tasks.backup_tasks",
    "app.tasks.cleanup_tasks",
    "app.tasks.import_tasks",   # ← U06a 新增（NF-4，否则 run_import_batch.delay 找不到）
])

from celery.signals import worker_process_init

@worker_process_init.connect
def _register_adapters_in_worker(**_kwargs):
    """worker 进程注册 Adapter（HTTP 进程的注册 worker 看不到，NF-4）。"""
    from app.main import register_import_adapters
    register_import_adapters()
```

- **同镜像双进程**：backend（HTTP）+ celery-worker 同 U01 镜像 → openpyxl 装入共享 requirements，两进程都可用
- HTTP 进程：main.py lifespan `register_import_adapters()`
- worker 进程：`worker_process_init` 信号 `register_import_adapters()`
- 缺失 Adapter（U06b-e 未部署）→ warning 不阻塞；registry 空时 upload 任何 source 都 422（框架可独立部署，Q7）

---

## 5. nginx / ASGI body 上限（NF-6）

```nginx
# frontend/nginx.conf —— 反代 backend 的 location
location /api/ {
    client_max_body_size 21m;   # 业务 20MB + 1MB multipart 边界
    proxy_pass http://backend:8000;
    ...
}
```
- L1 网关：nginx 21m（multipart 落盘前挡超大请求，防 DoS）
- L2 handler：upload service 累计字节 ≤ IMPORT_MAX_FILE_MB（20MB）→ 422（业务兜底）
- L3 解析：run_import_batch 流式计数行数 ≤ IMPORT_MAX_ROWS → batch.failed
- uvicorn 无原生 body 上限 → 靠 nginx + Starlette/handler（如 Zeabur 无独立 nginx 网关，则前端服务的 nginx 仍是主要拦截点）

---

## 6. 环境变量增量

| 变量 | 默认 | 说明 |
|---|---|---|
| IMPORT_MAX_FILE_MB | 20 | 上传文件大小上限 |
| IMPORT_MAX_ROWS | 50000 | 单文件数据行数上限 |
| IMPORT_RETENTION_DAYS | 0 | 0=MVP 不清理；V1 设保留期 |
| IMPORT_BUCKET | private | 导入文件 R2 桶 |

> 均有默认值，Zeabur 可不配（用默认）；需调整时在 Secrets 注入。

---

## 7. Sentry + Prometheus

### 7.1 Sentry
- `module=importer` tag；捕获：解析致命失败 / adapter 缺失 / R2 不可达 / migration 异常
- 复用 U01 backend Sentry 项目（DSN 不变）

### 7.2 Prometheus（5 指标，core/metrics.py）
| 指标 | 类型 | 标签 |
|---|---|---|
| import_batch_total | Counter | source, status |
| import_rows_total | Counter | source, result |
| import_batch_duration_seconds | Histogram | source |
| import_file_size_bytes | Histogram | source |
| import_retry_total | Counter | source |

接入 U01 通用 Grafana（MVP）；V1 专属导入看板。

---

## 8. 依赖

```diff
# requirements.txt
+ openpyxl==3.1.5   # U06a XLSX 解析（read_only + data_only）
```
backend + celery-worker 同镜像，一次更新两进程生效。

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| PG 3 表 + 约束 + RLS + permission seed | ✅ §2 |
| R2 imports/ 不经 attachment ORM（FB-A） | ✅ §3 |
| Celery autodiscover import_tasks + worker 注册（NF-4） | ✅ §4 |
| nginx body 上限三层（NF-6） | ✅ §5 |
| env 4 变量 | ✅ §6 |
| permission importer.batch/mapping（NF-5） | ✅ §2.2 |
| 不新增 Zeabur 服务 | ✅ §1 |
| 框架可独立部署（Q7） | ✅ §4 |
