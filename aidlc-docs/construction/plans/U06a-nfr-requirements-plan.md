# U06a NFR 需求计划（NFR Requirements Plan）

> 单元：U06a — 统一导入框架  
> 范围：U06a 特异性 NFR（异步导入 / 文件解析 / R2 中转 / Celery 重试 / 行级编排）；通用 NFR 全部继承 U01-U05  
> 节奏：增量式，仅列 U06a 新增；通用项引用基线

---

## 1. 与 U01-U05 NFR 基线的关系

### 1.1 完全继承
- 全部 U01 通用 NFR（多租户 RLS 双引擎 / 审计 / 健康检查 / Token / Sentry / Prometheus / structlog / pytest 框架 / R2 4 桶）
- U01 Celery 基线（celery-worker + celery-beat 服务 + default/backup 队列 + autoretry 模式）
- U01 R2 helper（`upload_bytes` / `get_signed_url` / `get_object`(client) / `delete` / `make_tenant_key`）— **U06a 直接复用，不依赖 U05 Attachment ORM**（FB-A）
- U05 模式（独立 bypass session 写失败记录 + 脱敏 audit；`SET app.tenant_id` 会话级用于系统任务路径）

### 1.2 U06a 增量 NFR 维度
- **异步导入吞吐**：单 batch ≤ 5 万行流式解析 + 行级独立事务的吞吐 / 时延 SLA
- **文件解析内存**：csv / openpyxl read_only 流式，内存 O(1) 行缓冲（不全量载入）
- **R2 中转**：upload 同步段（hash + R2 写 + 建 batch）时延 SLA；大文件上限
- **Celery 重试可靠性**：run_import_batch 失败语义（解析失败 vs 行级失败）+ 退避 + 幂等（重跑不产生重复业务记录，靠 adapter 幂等 + UNIQUE(batch_id,row_number)）
- **行级隔离**：一行失败不影响其他行（per-row 事务 + bypass 兜底）的正确性 SLA
- **worker 租户上下文**：`SET app.tenant_id` 会话级在 Celery 长事务序列中的正确性（RLS 生效验证）
- **可观测性**：导入专属 Prometheus 指标（batch / row / 时延 / 失败率）
- **容量**：import_batch / import_job 表增长（每 batch 最多 5 万 job 行）

### 1.3 与既有单元关键差异

| 维度 | U01-U05（HTTP 同步为主） | U06a（异步导入） |
|---|---|---|
| 主路径 | HTTP request/response | upload(HTTP) + run_import_batch(Celery) |
| 租户上下文 | 中间件 contextvars + SET LOCAL（per-request） | **worker SET app.tenant_id 会话级**（跨 per-row commit，FB-C） |
| 事务粒度 | 每 service 方法一事务 | **每行独立事务**（FB-C，一行失败不影响其他） |
| 失败处理 | 异常 → HTTP error | 行级失败 → import_job.failed（不中断 batch） |
| 文件 | U05 attachment ORM（settlement_proof） | **R2 直存 file_r2_key，不经 attachment ORM**（FB-A） |
| 重试 | U01 backup autoretry | run_import_batch 两类失败分流 + 行级幂等（FB-E） |

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — 解析吞吐 SLA
[Answer] 单 batch 5 万行（简单 adapter，如 style/sku upsert）目标 **≤ 5 分钟**完成（行级独立事务，约 150-200 行/秒）。冒烟测试用 1000 行 < 30s。不设硬 SLA 阻塞（异步任务，进度可查）。

### Q2 — upload 同步段 SLA
[Answer] upload 端点（hash + R2 写 + 建 batch + enqueue）目标 **P95 ≤ 2s**（20MB 文件 R2 写为主）。SHA256 流式计算，不全量载入内存。

### Q3 — 文件大小 / 行数上限
[Answer] `IMPORT_MAX_FILE_MB=20`（settings 可配）/ `IMPORT_MAX_ROWS=50000`。超限 422。XLSX 用 openpyxl read_only（流式，不全量解压到内存）。

### Q4 — Celery 队列与并发
[Answer] 复用 U01 **default 队列**（不新建队列；导入非高频）。worker 并发由 U01 `--concurrency=2` 控制；单 batch 内行处理**串行**（保证 per-row 事务顺序 + 进度可控）。V1 评估专用 import 队列 + 批量 upsert。

### Q5 — run_import_batch 失败与重试语义
[Answer] **任务级不 autoretry**（避免整文件重复处理）；行级异常计入 import_job.failed（不抛出中断 batch）。**解析阶段致命失败**（文件损坏/R2 读失败）→ batch.failed + error_summary，由用户手动 retry（端点级，FB-E）。Celery 任务本身仅对**基础设施异常**（DB 连接断）autoretry 1 次。

### Q6 — 行级幂等与重复防护
[Answer] 重跑（retry only_failed / 整文件）不产生重复业务记录：依赖 **① adapter.upsert 按业务键幂等**（U06b-e 保证）+ **② UNIQUE(batch_id, row_number) 防重复 job 行**（原地更新 attempt_count）。同 batch 重跑安全。

### Q7 — worker 租户上下文正确性
[Answer] run_import_batch 用 **engine_app 连接 + `SET app.tenant_id='<tid>'`（会话级，非 SET LOCAL）**，保证跨多个 per-row commit RLS 持续生效（FB-C）。bypass session 仅用于读 batch 元数据 + 写失败 import_job（系统级）。需 NFR 测试验证：跨租户行不会被错误 upsert（RLS 拦截）。

### Q8 — 文件存储清理（R2 生命周期）
[Answer] MVP **保留导入原始文件**（供 retry 重读 + 审计追溯）；不主动删除。V1 评估按 batch 保留期（如 90 天）+ Celery beat 清理任务（`delete("private", file_r2_key)`）。

### Q9 — 可观测性指标
[Answer] 新增 Prometheus 指标：
- `import_batch_total{source, status}`（Counter）
- `import_rows_total{source, result}`（Counter，result=success/failed）
- `import_batch_duration_seconds{source}`（Histogram）
- `import_file_size_bytes{source}`（Histogram）
- `import_retry_total{source}`（Counter）
structlog 记 batch_id / source / tenant_id（不记文件内容）；Sentry 捕获解析致命失败 + adapter 缺失。

### Q10 — 测试 DB / 框架
[Answer] 复用 U01 pytest 套件（共享测试 DB + 事务回滚）。导入异步任务测试**同步调用** run_import_batch（不经 Celery broker，直接 await 任务函数）。FakeImportAdapter 验证框架编排。Celery eager 模式备选（CELERY_TASK_ALWAYS_EAGER=True 测试环境）。

### Q11 — 安全（文件上传威胁）
[Answer] 文件类型白名单（扩展名 + MIME 双校验）；大小/行数上限防 DoS；CSV 公式注入防护（导出失败 CSV 时对 `=+-@` 开头字段加前缀 `'`，防 CSV injection）；解析不执行任何文件内嵌宏（openpyxl read_only 不执行宏）；R2 key 用 batch_id 隔离防路径穿越。

### Q12 — 数据一致性（部分失败）
[Answer] partial batch 语义明确：成功行已入库（各自事务 commit），失败行未入库；用户下载失败明细修正后 retry only_failed（不影响已成功行）。**不做整批回滚**（导入容忍部分成功，与 services.md §2.5 一致）。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-requirements.md
- 性能 SLA（解析吞吐 / upload 同步段 / 行级事务）
- 容量（import_batch / import_job 增长；5 万行/batch）
- 可靠性（Celery 失败语义 / 行级隔离 / 重跑幂等）
- worker 租户上下文正确性（RLS 跨 per-row commit）
- 安全（文件上传威胁模型 + CSV injection 防护）
- 可观测性（5 个新指标 + structlog + Sentry）
- 测试覆盖（FakeImportAdapter + 同步任务调用 + 行级隔离 + 跨租户 RLS）
- 故事映射（EP07-S07~S10 的 NFR 验收）

### 3.2 tech-stack-decisions.md
- 解析库：`openpyxl`（XLSX read_only）+ 标准库 `csv`（新增 requirements）
- 新增配置：IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_FILE_RETENTION_DAYS（V1 占位）
- Celery：复用 default 队列 + task_always_eager 测试模式
- 指标：复用 prometheus-fastapi-instrumentator + 自定义 Counter/Histogram（core/metrics.py 扩展）
- R2：复用 U01 AttachmentService helper（boto3 client.get_object / put_object）
- 测试：pytest + FakeImportAdapter + freezegun（退避时间）

---

## 4. 文件影响预估（NFR Requirements 阶段仅文档）
- `aidlc-docs/construction/U06a/nfr-requirements/nfr-requirements.md`
- `aidlc-docs/construction/U06a/nfr-requirements/tech-stack-decisions.md`

---

**等待用户回复"继续"批准本计划（含预填的 12 个 [Answer]），开始生成 2 份 NFR 需求文档。**
