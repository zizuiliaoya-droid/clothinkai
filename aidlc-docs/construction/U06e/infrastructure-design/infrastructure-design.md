# U06e 基础设施设计（Infrastructure Design）

> 单元：U06e — 结算导入适配器
> 结论：**零基础设施增量**。U06e 是纯应用层适配器，运行在 U04/U05 + U06a 既有基础设施之上。

---

## 1. 增量清单：无

| 资源 | U06e 增量 | 复用来源 |
|---|---|---|
| PostgreSQL 表 | ❌ 无 | U05 `settlement`/`settlement_sequence` + U04 promotion + U06a import_* |
| Alembic migration | ❌ 无（无 DDL） | head 保持 010 |
| 表 schema 变更 | ❌ 无（写现有列 + 复用 next_settlement_sequence） | U05 现有字段 |
| R2 桶 / 路径 | ❌ 无 | U06a `imports/`（private） |
| Celery 队列 / 任务 | ❌ 无 | U06a `run_import_batch`（default 队列） |
| Python 依赖 | ❌ 无 | Decimal/date/uuid4（标准库）；openpyxl/csv（U06a） |
| env / 配置 | ❌ 无 | U06a `IMPORT_MAX_*` |
| Prometheus 指标 | ❌ 无 | U06a 5 指标（`source="manual_settlement"` label） |
| 权限 scope / seed | ❌ 无 | U06a importer.batch/mapping（migration 010） |
| Sentry | ❌ 无 | U06a `module=importer` |
| Zeabur 服务 / nginx | ❌ 无 | U01 backend + celery-worker / U06a 21m |

> 唯一交付物：`backend/app/modules/importer/adapters/settlement.py`。

---

## 2. PostgreSQL（复用 U04/U05 + U06a，无 DDL）
- adapter 读 U04 promotion（派生）；写 U05 settlement（INSERT）+ settlement_sequence（next_settlement_sequence UPDATE）
- 目标列 + sequence 表已存在（U05 migration 007），无新列/约束
- RLS：复用 U04/U05 表既有 tenant_isolation 策略 + U06a runner per-row SET LOCAL（NF-1）
- 唯一性：复用 U05 `uq_settlement_no` / `uq_settlement_promotion`（一对一冲突保证）/ `uq_settlement_request_event_id` / `uq_settlement_sequence`

---

## 3. R2 / Celery（复用 U06a）
- 文件存储 + 解析由 U06a 处理；adapter 只收解析后行 dict
- adapter 在 run_import_batch per-row 事务内被调用；worker 已注册

---

## 4. 部署与回滚

### 4.1 部署
- U06e = 纯代码增量（1 个 adapter 模块）；依赖 U04/U05 + U06a 已部署
- 随 backend + celery-worker 镜像更新生效（双进程自动注册）
- 无 migration / 无停机 / 无部署顺序约束

### 4.2 回滚
- 移除 adapter 模块 → register_import_adapters 抛 ModuleNotFoundError → 仅 warning → manual_settlement 从白名单消失 → upload 422
- 已导入 settlement 数据不受影响；无 DDL 回滚

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新表 / 无 DDL / 无 migration | ✅ §1 + §2 |
| 无新 R2 / Celery / 依赖 / 配置 / 指标 / 权限 | ✅ §1 |
| 无新 Zeabur 服务 / nginx 不改 | ✅ §1 |
| 纯代码增量，可独立部署 + 安全回滚 | ✅ §4 |
