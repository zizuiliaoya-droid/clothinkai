# U06d 基础设施设计计划（Infrastructure Design Plan）

> 单元：U06d — 推广导入适配器
> 范围：**零基础设施增量** — 无新表/migration/服务/依赖/配置/指标/部署变更
> 节奏：确认型（复用 U02/U03/U04 + U06a 既有基础设施）

---

## 1. 基础设施增量结论：无

| 资源 | U06d 增量 | 复用来源 |
|---|---|---|
| PostgreSQL 表 | ❌ 无 | U04 `promotion`/`promotion_sequence` + U02/U03 + U06a import_* |
| Alembic migration | ❌ 无 | head 保持 010 |
| R2 / Celery | ❌ 无 | U06a `imports/`（private） / run_import_batch（default 队列） |
| Python 依赖 | ❌ 无 | Decimal/date 标准库；openpyxl/csv 复用 U06a |
| env / 配置 | ❌ 无 | U06a IMPORT_MAX_* |
| Prometheus | ❌ 无 | U06a 5 指标（source=manual_promotion label） |
| 权限 seed | ❌ 无 | U06a importer.batch/mapping（migration 010） |
| Zeabur 服务 / nginx | ❌ 无 | U01 backend + celery-worker / U06a 21m |

> 唯一交付物：`adapters/promotion.py`。

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — 需要新 migration？
[Answer] **否**。不建表/不改 promotion/promotion_sequence schema（写现有列 + 复用 next_internal_sequence）。head 保持 010。

### Q2 — 权限 seed？
[Answer] **否**。复用 U06a importer.batch:read/write + importer.mapping:write。

### Q3 — worker 镜像调整？
[Answer] **否**。同 U01 镜像；worker_process_init 已注册 register_import_adapters。

### Q4 — 部署顺序约束？
[Answer] 依赖 U02/U03/U04（style/blogger/promotion 表 + sequence）+ U06a 均已部署。U06d 纯代码增量随镜像更新生效，无 migration 顺序约束/无停机。回滚 = 移除 adapter 模块（ModuleNotFoundError 仅 warning）。

### Q5 — 监控/告警？
[Answer] 复用 U06a 5 指标（source=manual_promotion label）+ module=importer Sentry tag。无新告警规则。

---

## 3. 生成产物（2 份文档）
- infrastructure-design.md：零增量结论 + 复用矩阵 + 部署/回滚
- deployment-architecture.md：复用 U01 6 服务 + 双进程注册 + 无 migration

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06d/infrastructure-design/{infrastructure-design,deployment-architecture}.md`

---

**等待用户回复"继续"批准本计划（含 5 个 [Answer]），开始生成 2 份基础设施设计文档。**
