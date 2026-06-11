# U06c 基础设施设计（Infrastructure Design）

> 单元：U06c — 博主导入适配器
> 结论：**零基础设施增量**。U06c 是纯应用层适配器，运行在 U03 + U06a 既有基础设施之上。

---

## 1. 增量清单：无

| 资源 | U06c 增量 | 复用来源 |
|---|---|---|
| PostgreSQL 表 | ❌ 无 | U03 `blogger` + U06a `import_batch`/`import_job`/`field_mapping` |
| Alembic migration | ❌ 无（无 DDL） | head 保持 010 |
| 表 schema 变更 | ❌ 无（写现有列） | U03 Blogger 现有字段 |
| R2 桶 / 路径 | ❌ 无 | U06a `imports/{tenant_id}/{batch_id}/`（private） |
| Celery 队列 / 任务 | ❌ 无 | U06a `run_import_batch`（default 队列） |
| Python 依赖 | ❌ 无 | re/int/Decimal（标准库）；openpyxl/csv（U06a） |
| env / 配置 | ❌ 无 | U06a `IMPORT_MAX_*` |
| Prometheus 指标 | ❌ 无 | U06a 5 指标（`source="manual_blogger"` label） |
| 权限 scope / seed | ❌ 无 | U06a importer.batch/mapping（migration 010） |
| Sentry | ❌ 无 | U06a `module=importer` |
| Zeabur 服务 | ❌ 无 | U01 backend + celery-worker |
| nginx | ❌ 无 | U06a `client_max_body_size 21m` |

> 唯一交付物：`backend/app/modules/importer/adapters/blogger.py`。

---

## 2. PostgreSQL（复用 U03 + U06a，无 DDL）
- adapter 写入 U03 `blogger`（`upsert_atomic` ON CONFLICT xiaohongshu_id）
- 目标列已存在（U03 migration 005），无新列/约束
- RLS：复用 U03 blogger tenant_isolation 策略 + U06a runner per-row SET LOCAL（NF-1）
- 索引：复用 U03 `uq_blogger_xiaohongshu_id`（partial UNIQUE，支撑 ON CONFLICT）

---

## 3. R2 / Celery（复用 U06a）
- 导入文件存储 + 解析由 U06a 处理；adapter 不直接碰 R2（只收解析后行 dict）
- adapter 在 U06a run_import_batch per-row 事务内被调用；worker 已注册（worker_process_init）

---

## 4. 部署与回滚

### 4.1 部署
- U06c = 纯代码增量（1 个 adapter 模块）；依赖 U03 + U06a 已部署
- 随 backend + celery-worker 镜像更新生效（双进程自动注册）
- 无 migration / 无停机 / 无部署顺序约束

### 4.2 回滚
- 移除 adapter 模块 → register_import_adapters 抛 ModuleNotFoundError → 仅 warning → manual_blogger 从白名单消失 → upload 422
- 已导入 blogger 数据不受影响；无 DDL 回滚

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新表 / 无 DDL / 无 migration | ✅ §1 + §2 |
| 无新 R2 / Celery / 依赖 / 配置 / 指标 / 权限 | ✅ §1 |
| 无新 Zeabur 服务 / nginx 不改 | ✅ §1 |
| 纯代码增量，可独立部署 + 安全回滚 | ✅ §4 |
