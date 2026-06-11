# U06b 基础设施设计（Infrastructure Design）

> 单元：U06b — 商品/SKU 导入适配器
> 结论：**零基础设施增量**。U06b 是纯应用层适配器，运行在 U02 + U06a 既有基础设施之上。

---

## 1. 增量清单：无

| 资源 | U06b 增量 | 复用来源 |
|---|---|---|
| PostgreSQL 表 | ❌ 无 | U02 `style` / `sku` / `brand` + U06a `import_batch` / `import_job` / `field_mapping` |
| Alembic migration | ❌ 无（无 DDL） | head 保持 010（U06a） |
| 表 schema 变更 | ❌ 无（写现有列） | U02 Style/Sku 现有字段 |
| R2 桶 / 路径 | ❌ 无 | U06a `imports/{tenant_id}/{batch_id}/`（private 桶） |
| Celery 队列 / 任务 | ❌ 无 | U06a `run_import_batch`（default 队列） |
| Python 依赖 | ❌ 无 | Decimal（标准库）；openpyxl/csv（U06a 已加） |
| env / 配置 | ❌ 无 | U06a `IMPORT_MAX_FILE_MB` / `IMPORT_MAX_ROWS` / `IMPORT_BUCKET` |
| Prometheus 指标 | ❌ 无 | U06a 5 指标（`source="manual_style_sku"` label 区分） |
| 权限 scope / seed | ❌ 无 | U06a `importer.batch:read/write` + `importer.mapping:write`（migration 010 已 seed） |
| Sentry | ❌ 无（复用 tag） | U06a `module=importer` |
| Zeabur 服务 | ❌ 无（不新增/不改） | U01 backend + celery-worker（同镜像） |
| nginx | ❌ 无（不改） | U06a `client_max_body_size 21m` |

> 唯一交付物是应用代码 `backend/app/modules/importer/adapters/style_sku.py`（无基础设施影响）。

---

## 2. PostgreSQL（复用 U02 + U06a，无 DDL）

- adapter 写入 U02 `style`（复用/创建）+ `sku`（`upsert_atomic` ON CONFLICT）+ 读 `brand`（brand_code → brand_id）
- 所有目标列已存在（U02 migration 004），无新列/无新约束
- RLS：复用 U02 表既有 tenant_isolation 策略 + U06a runner per-row `SET LOCAL app.tenant_id`（NF-1）
- 索引：复用 U02 `uq_style_code`（partial UNIQUE）/ `uq_sku_code`（partial UNIQUE，支撑 ON CONFLICT）

---

## 3. R2（复用 U06a，无新桶/路径）

- 导入文件存储完全由 U06a ImportService 处理（`imports/{tenant_id}/{batch_id}/{safe_filename}`，private 桶）
- adapter **不直接碰 R2**（文件解析由 U06a runner 的 `_parse_rows` 完成，adapter 只收解析后的行 dict）

---

## 4. Celery（复用 U06a，无新队列/任务）

- adapter 在 U06a `run_import_batch`（default 队列）的 per-row 事务内被调用
- worker 镜像与 backend 同 U01 镜像，已含 openpyxl（U06a requirements）
- `worker_process_init` 已注册 `register_import_adapters`（U06a）；落地 adapters/style_sku.py 后自动注册（NF-4 双进程）

---

## 5. 部署与回滚

### 5.1 部署
- U06b = 纯代码增量（新增 1 个 adapter 模块）
- 依赖 U02（style/sku 表）+ U06a（import 框架）均已部署（MVP 已交付）
- 随 backend + celery-worker 镜像更新部署即生效（两进程通过既有 register_import_adapters 自动注册）
- **无 migration 步骤 / 无部署顺序约束 / 无停机**

### 5.2 回滚
- 移除 adapter 模块 → `register_import_adapters` 的 `import_module("...style_sku")` 抛 ModuleNotFoundError → 仅 warning（U06a 设计）→ manual_style_sku 从 registry 白名单消失 → upload(source=manual_style_sku) 返回 422
- 已导入的 style/sku 数据不受影响（U02 表数据独立）
- 无数据迁移回滚（无 DDL）

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新表 / 无 DDL / 无 migration | ✅ §1 + §2 |
| 无新 R2 桶/路径 | ✅ §3 |
| 无新 Celery 队列/任务 | ✅ §4 |
| 无新依赖/配置/指标/权限 | ✅ §1 |
| 无新 Zeabur 服务 / nginx 不改 | ✅ §1 |
| 纯代码增量，可独立部署 + 安全回滚 | ✅ §5 |
