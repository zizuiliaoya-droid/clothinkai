# U06a 基础设施设计计划（Infrastructure Design Plan）

> 单元：U06a — 统一导入框架  
> 范围：U06a 特异性基础设施增量；通用基础设施全部继承 U01 + shared-infrastructure  
> 关键差异：**Celery worker 任务发现扩展 + nginx body 上限 + R2 imports/ 路径 + worker Adapter 注册**

---

## 1. 单元上下文

### 1.1 与 U01-U05 基础设施的关系

U01 已建立：6 服务部署（frontend / backend / celery-worker / celery-beat / postgres / redis）+ R2 4 桶 + Sentry 2 项目 + GitHub Actions（ci / migrate / deploy-prod / deploy-staging）。  
U02 启用 pg_trgm。U04 引入 events + register_event_listeners + CI validate job。U05 补齐 shared attachment 表 + 009 staging seed + 启用真实 e2e-smoke。

**U06a 增量**：
- PostgreSQL：3 新表（import_batch / import_job / field_mapping）+ 约束 + 索引 + 2 RLS 策略 + permission seed 追加（importer.batch/mapping，NF-5）→ migration 010
- **R2 private 桶 `imports/` 子目录首次使用**（导入原始文件，U01 已 provisioning，U06a 仅消费；不经 attachment ORM，FB-A）
- **Celery worker 任务发现扩展**（NF-4：autodiscover 加 import_tasks）+ worker_process_init 注册 Adapter
- **nginx / ASGI body 上限**（NF-6：client_max_body_size 21m）
- env 变量：IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_RETENTION_DAYS / IMPORT_BUCKET
- Sentry：新增 `module=importer` tag
- Prometheus：5 个新指标
- main.py：register_import_adapters + import_router
- 依赖：openpyxl==3.1.5

### 1.2 关键部署约束
- **worker 与 backend 同镜像**（已是 U01 架构）→ openpyxl 装入共享 requirements，两进程都可用
- **Adapter 注册双进程**（NF-4）：HTTP（lifespan）+ worker（worker_process_init）；U06a 框架部署时 U06b/c/d/e 可能未部署 → register 缺失 warning 不阻塞（upload 时 source 白名单兜底）
- **nginx body 上限**（NF-6）：21m（略大于 20MB 业务上限，留 multipart 边界）；同步配 uvicorn/Starlette
- migration 010 单独 alembic upgrade（无 backfill，纯建表 + seed）
- U06a 框架可独立部署（不依赖 U06b/c/d/e）；无 Adapter 时 upload 任何 source 都 422（registry 空）

### 1.3 输入文档
- U06a functional-design / nfr-requirements / nfr-design（5 模式）
- U01 infrastructure-design + shared-infrastructure（通用基线）
- U05 deployment-architecture（migration + e2e-smoke 框架参考）

### 1.4 输出文档
- `U06a/infrastructure-design/infrastructure-design.md`（资源增量 + PG 表 + R2 path + Celery 发现 + nginx + env + Sentry/Prometheus）
- `U06a/infrastructure-design/deployment-architecture.md`（migration 010 + 部署流程 + worker 验证 + 回滚 + smoke）

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — Celery worker 资源（导入任务）
[Answer] 复用 U01 现有 celery-worker 服务（`--concurrency=2 --queues=default,backup`）。导入走 default 队列，与 cleanup 等共享。**不新建专用 worker / 队列**（MVP 导入非高频）。V1 数据量大时评估专用 import 队列 + 独立 worker（防大导入阻塞其他 default 任务）。

### Q2 — worker 内存（5 万行 + openpyxl）
[Answer] openpyxl read_only 流式 + 一次性读 R2 文件入内存（≤ 20MB）→ worker 内存峰值约 文件 20MB + 解析缓冲，单 batch < 200MB 可控。U01 worker 默认资源足够；不调整。`worker_max_tasks_per_child=200`（U01 已配）防内存累积。

### Q3 — nginx body 上限值（NF-6）
[Answer] `client_max_body_size 21m`（业务 20MB + 1MB multipart 边界）。配在 frontend nginx.conf（反代 backend 的 location）+ Zeabur 后端服务（如有独立网关）。uvicorn 无原生 body 上限配置 → 靠 Starlette/handler 兜底（NF-6 L2）。

### Q4 — R2 imports/ 路径与生命周期
[Answer] key = `imports/{tenant_id}/{batch_id}/{safe_filename}`，private 桶。MVP **保留原始文件**（retry 重读 + 审计），不配 R2 lifecycle 自动删除。`IMPORT_RETENTION_DAYS=0`（不清理）。V1 设保留期 + Celery beat 清理任务（`delete("private", key)`）。

### Q5 — migration 010 内容与顺序
[Answer] `010_u06a_create_import_tables.py`：建 import_batch / import_job / field_mapping + 约束（UNIQUE ×4）+ 索引 + 2 RLS（import_batch / import_job；field_mapping 也启 RLS）+ permission seed 追加（importer.batch:read/write + importer.mapping:write，NF-5）。**纯建表无 backfill**，单独 alembic upgrade。down_revision = 009。

### Q6 — permission seed 落地方式（NF-5）
[Answer] 010 migration 内 INSERT permission 表 3 个 scope + 关联到 default_roles（operations→importer.batch:read；pr/pr_manager→importer.batch:write；pr_manager/admin→importer.mapping:write）。幂等 NOT EXISTS。同时更新 default_roles.py 代码常量（保持 003 seed 与运行时一致）。

### Q7 — Adapter 缺失时的部署可用性
[Answer] U06a 框架独立部署：无任何 Adapter 注册时，`ImportAdapterRegistry.sources()` 为空 → upload 任何 source 都 422（IMPORT_SOURCE_UNKNOWN）。框架本身健康（/health 正常）。U06b/c/d/e 部署后各自 source 才可用。CI 不强制要求 Adapter 存在（与 U05 finance listener 不同，导入框架可空跑）。

### Q8 — CI 检查（导入框架）
[Answer] CI 新增轻量检查（不阻塞）：grep `app.tasks.import_tasks` 在 celery_app autodiscover 中命中（NF-4 防漏注册）。**不要求** Adapter 存在（框架可独立部署）。pytest 用 FakeImportAdapter 覆盖框架逻辑。

### Q9 — staging smoke（导入）
[Answer] MVP **不加导入专属 e2e-smoke**（导入依赖 U06b-e Adapter，框架单独无法端到端）。U06b 部署后再加 staging smoke（上传样例 CSV → 验证入库）。U06a 阶段 CI 跑 pytest（含 FakeImportAdapter 集成测试）即可。

### Q10 — 监控告警（导入）
[Answer] Prometheus 5 指标接入 U01 通用 Grafana（MVP 复用）。V1 专属看板：import_batch 成功率 / 平均行数 / 失败率趋势。告警阈值（V1）：`import_batch_total{status="failed"}` 突增 / `import_rows_total{result="failed"}` 比率 > 50%。Sentry `module=importer` tag 捕获解析致命失败 + adapter 缺失 + R2 不可达。

### Q11 — Zeabur 部署影响
[Answer] U06a 不新增 Zeabur 服务（复用 backend + celery-worker）。仅：① backend/worker 镜像装 openpyxl（requirements 更新触发重新构建）；② 注入 4 个 IMPORT_* env 变量（Zeabur Secrets，有默认值可不配）；③ nginx body 上限（frontend 服务配置）。migration 010 走现有 migrate.yml workflow。

### Q12 — 回滚
[Answer] migration 010 可逆（drop 3 表 + 移除 permission seed）。导入数据回滚：import_batch / import_job 删除不影响已入库的业务记录（业务记录由各 adapter 写入对应业务表，独立存在）。R2 imports/ 文件回滚时一并清理（V1 脚本）。无跨单元强约束（U06a 独立）。

---

## 3. 生成产物（2 份文档）

### 3.1 infrastructure-design.md
- 资源增量清单（PG 3 表 + R2 imports/ + Celery 发现 + nginx + env + Sentry/Prometheus）
- PostgreSQL：3 表 + 约束 + 索引 + RLS + permission seed
- R2：imports/ 路径规约 + 生命周期（MVP 保留）
- Celery：autodiscover import_tasks + worker_process_init 注册（NF-4）
- nginx / ASGI body 上限（NF-6）
- env 变量清单（IMPORT_MAX_FILE_MB / MAX_ROWS / RETENTION_DAYS / BUCKET）
- Sentry module=importer + Prometheus 5 指标
- 依赖 openpyxl==3.1.5

### 3.2 deployment-architecture.md
- migration 010 完整 DDL（3 表 + 约束 + 索引 + RLS + permission seed）
- 部署流程（migration 010 → 镜像构建含 openpyxl → env 注入 → nginx 配置 → 部署验证）
- worker 验证（autodiscover import_tasks 命中 + Adapter 注册日志）
- 回滚预案（010 downgrade + import 数据独立性）
- CI 增量（autodiscover grep，不阻塞）
- 部署前/后 checklist

---

## 4. 文件影响预估（Infrastructure Design 阶段仅文档）
- `aidlc-docs/construction/U06a/infrastructure-design/infrastructure-design.md`
- `aidlc-docs/construction/U06a/infrastructure-design/deployment-architecture.md`

---

**等待用户回复"继续"批准本计划（含预填的 12 个 [Answer]），开始生成 2 份基础设施设计文档。**
