# U06b 基础设施设计计划（Infrastructure Design Plan）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：**零基础设施增量** — 无新表/无 migration/无新服务/无新依赖/无新配置/无新指标/无部署变更
> 节奏：确认型（仅校验复用 U02 + U06a 既有基础设施，无新决策）

---

## 1. 基础设施增量结论：无

U06b 是纯应用层适配器组件，运行在 U06a/U02 既有基础设施之上：

| 资源 | U06b 增量 | 复用来源 |
|---|---|---|
| PostgreSQL 表 | **无**（不建表/不改 schema） | U02 style/sku/brand + U06a import_batch/import_job/field_mapping |
| Alembic migration | **无** | 不涉及 DDL |
| R2 | **无新桶/路径** | U06a `imports/{tenant_id}/{batch_id}/`（private） |
| Celery | **无新队列/任务** | U06a run_import_batch（default 队列） |
| env / 配置 | **无新变量** | U06a IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_BUCKET |
| Python 依赖 | **无**（Decimal 标准库；openpyxl/csv 复用 U06a） | U06a requirements |
| Prometheus | **无新指标** | U06a 5 指标（source=manual_style_sku label 区分） |
| Sentry | 复用 module=importer tag | U06a |
| 权限 seed | **无新 scope** | U06a importer.batch/mapping（migration 010 已 seed） |
| Zeabur 服务 | **不新增/不改** | U01 backend + celery-worker（同镜像，已含 openpyxl） |
| nginx | **不改** | U06a client_max_body_size 21m（已配） |

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — 是否需要新 migration？
[Answer] **否**。U06b 不建表、不改既有表 schema。adapter 写入 U02 style/sku 的现有列（无新列）。无 DDL → 无 migration。当前 head 保持 010（U06a）。

### Q2 — 是否需要权限 seed？
[Answer] **否**。U06b 复用 U06a 的 importer.batch:read/write + importer.mapping:write（migration 010 已 seed + default_roles 已配 pr/pr_manager/operations）。upload(source=manual_style_sku) 用 importer.batch:write，无新 scope。

### Q3 — Celery worker 镜像是否需调整？
[Answer] **否**。celery-worker 与 backend 同 U01 镜像，已含 openpyxl（U06a 加入 requirements）。worker_process_init 已注册 register_import_adapters（U06a），落地 adapters/style_sku.py 后自动注册。无镜像/部署变更。

### Q4 — 部署顺序约束？
[Answer] U06b 依赖 U02（style/sku 表）+ U06a（import 框架）均已部署（MVP 已交付）。U06b 是纯代码增量（新增 1 个 adapter 模块），随 backend 镜像更新部署即生效，无 migration 顺序依赖。回滚 = 移除 adapter 模块（register 时 ModuleNotFoundError 仅 warning，框架降级为该 source 不可用）。

### Q5 — 监控/告警增量？
[Answer] 复用 U06a 5 指标 + module=importer Sentry tag。manual_style_sku 的失败率/耗时通过 `import_*{source="manual_style_sku"}` label 在 U01 通用 Grafana 切分查看。无新告警规则（V1 评估按 source 设阈值）。

---

## 3. 生成产物（2 份文档）

### 3.1 infrastructure-design.md
- 增量结论：零基础设施增量（§1 表）
- 复用矩阵（U02 表 / U06a 框架 / R2 / Celery / 配置 / 指标 / 权限 / 部署）
- 部署与回滚（纯代码增量，随镜像更新；回滚 = 移除 adapter 模块）
- 一致性校验（无 DDL / 无新服务 / 无新依赖）

### 3.2 deployment-architecture.md
- 部署视图（U06b adapter 在 backend + celery-worker 双进程注册，复用 U06a 注册机制）
- 无新 Zeabur 服务 / 无 migration 步骤
- 监控复用（5 指标 source label）

---

## 4. 文件影响预估（Infrastructure Design 阶段仅文档）
- `aidlc-docs/construction/U06b/infrastructure-design/infrastructure-design.md`
- `aidlc-docs/construction/U06b/infrastructure-design/deployment-architecture.md`

---

**等待用户回复"继续"批准本计划（含预填的 5 个 [Answer]），开始生成 2 份基础设施设计文档。**
