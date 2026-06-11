# U06a NFR 设计计划（NFR Design Plan）

> 单元：U06a — 统一导入框架  
> 范围：U06a 特异性 NFR 设计模式 + 逻辑组件；通用模式继承 U01-U05

---

## 0. 已应用 P1/P2 反馈修正（6 条）

| # | 反馈 | 修正内容 |
|---|---|---|
| **NF-1**（P1 连接池串租） | 会话级 `SET app.tenant_id` 在连接归还 pool 后泄漏给下个任务（db.py 用 `SET LOCAL`） | **改用 per-row 事务内 `SET LOCAL app.tenant_id`**：每行 `BEGIN → SET LOCAL app.tenant_id → upsert + job → COMMIT`。SET LOCAL 事务级，commit/rollback 后自动失效，绝不泄漏到 pool。撤销原 Q2"会话级"决策。 |
| **NF-2**（P1 TOCTOU + 孤儿 R2） | SELECT 去重 → R2 写 → 建 batch 顺序在并发上传同文件时会双写 R2 + 一个 UNIQUE 插入失败留孤儿文件 | **DB 先行 + 补偿删除**：① 先 INSERT import_batch（status=processing），靠 `UNIQUE(tenant_id,source,file_hash)` 原子捕获并发重复 → IntegrityError 转 409；② 插入成功后才 upload_bytes 写 R2（key 含 batch_id）；③ R2 写失败 → 补偿 DELETE batch 行 + 500。撤销原 Q6"先 R2 后 DB"顺序。 |
| **NF-3**（P1 批次并发互斥） | retry / runner 无同 batch 互斥，重复点击 / 原任务未结束会并发跑同 batch | **原子 processing claim**：retry 端点用 `UPDATE import_batch SET status='processing', retry_count=retry_count+1 WHERE id=? AND status IN ('partial','failed') AND retry_count<3 RETURNING`（0 行 = 已在跑/已耗尽 → 409）；runner 入口同样以 status 守卫 + 行级 `SELECT ... FOR UPDATE` 防并发改同 job。 |
| **NF-4**（P2 Celery 发现） | celery_app 仅 autodiscover backup/cleanup，run_import_batch 可能找不到 | celery_app.autodiscover_tasks 列表**新增 `app.tasks.import_tasks`**；logical-components 明确记录此改动。 |
| **NF-5**（P2 权限命名） | 计划用 `import:read/write`，现有默认角色是 `importer.*:*` / `importer.*:read`（default_roles.py） | **统一为 `importer.batch:read` / `importer.batch:write` / `importer.mapping:write`**（符合 U01 Q12=B `module.sub:action`）；logical-components 列出 default_roles + permission seed 更新（operations 加 importer.batch:read；pr/pr_manager/admin 加 importer.batch:write + importer.mapping:write）。 |
| **NF-6**（P2 multipart body 上限） | 20MB 校验在 handler 内，挡不住已落盘的超大 multipart | NFR pattern 补 **ASGI/网关层 body 上限**：uvicorn/Starlette 请求体上限 + nginx `client_max_body_size 21m`（略大于 20MB 业务上限留 multipart 边界）；handler 内校验作为业务兜底（双层防护）。 |

---

## 1. 单元上下文

### 1.1 与 U01-U05 NFR Design 的关系

完全继承：
- U01 9 个通用模式（多租户 RLS 双引擎 / 审计脱敏 / 错误处理 / 监控 / 健康检查 / Celery autoretry / R2 helper / structlog / pytest）
- U05 模式（独立 bypass session 写失败记录 + 脱敏 audit；系统任务 `SET app.tenant_id`）
- U04/U05 注册机制（register_*() + main.py lifespan 加载 + 缺失 warning 不阻塞）— **U06a 复用为 ImportAdapterRegistry 注册**

### 1.2 U06a 增量（5 个新模式）

| 模式 | 解决问题 | 章节 |
|---|---|---|
| **P-U06a-01** 导入 Runner 事务+租户上下文 | run_import_batch 在 Celery 内 per-row 事务边界 + **per-row `SET LOCAL app.tenant_id`**（NF-1 防连接池串租）+ 失败行 bypass 兜底（FB-C） | §2 |
| **P-U06a-02** ImportAdapter 协议 + Registry | 框架/适配器解耦；注册 main.py + worker 双加载；**Celery autodiscover 加 import_tasks**（NF-4）；source 白名单 | §3 |
| **P-U06a-03** DB 先行上传 + hash 去重 | **先 INSERT batch（UNIQUE 原子去重）→ 再写 R2 → 失败补偿删除**（NF-2 防 TOCTOU/孤儿）；U01 helper（非 attachment ORM，FB-A） | §4 |
| **P-U06a-04** 两类失败重试 + 批次互斥 | 解析失败重跑整文件 vs only_failed；**原子 processing claim 防并发 retry**（NF-3）+ retry_count 时机 + 退避（FB-E） | §5 |
| **P-U06a-05** 安全文件处理 | 白名单 + **ASGI/网关 body 上限 + handler 业务兜底双层**（NF-6）+ openpyxl read_only/data_only + CSV injection 转义 + **importer.batch/mapping 权限对齐**（NF-5） | §6 |

### 1.3 输入文档
- U06a functional-design 3 文档
- U06a nfr-requirements 2 文档
- U01 nfr-design（Celery / R2 helper / 监控模式）+ U05 nfr-design（bypass session 兜底 / SET app.tenant_id / 注册机制）

### 1.4 输出文档
- `U06a/nfr-design/nfr-design-patterns.md`（5 个增量模式）
- `U06a/nfr-design/logical-components.md`（U06a 新增组件清单 + Adapter 注册 + main.py 扩展）

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — Runner session 持有方式（P-U06a-01）
[Answer] run_import_batch 内**显式管理两个 session**：① `AsyncSessionBypass`（读 batch 元数据 + 写失败 import_job + 汇总，系统级绕 RLS）；② `AsyncSessionApp`（行级 adapter.upsert，受 RLS 约束）。成功行的 import_job(success) 用 app session 在同 per-row 事务内写（与业务记录同事务，保证一致）；失败行的 import_job(failed) 用独立 bypass session 写（防回滚带走，FB-C）。

### Q2 — 租户上下文设置方式（P-U06a-01，NF-1 修订）
[Answer] **per-row 事务内 `SET LOCAL app.tenant_id`**（不用会话级 SET）。每行：`BEGIN → SET LOCAL app.tenant_id='<tid>' → adapter.upsert + INSERT import_job(success) → COMMIT`。SET LOCAL 事务级，commit/rollback 后自动失效，**连接归还 pool 时不残留**（NF-1：会话级 SET 会泄漏给下个复用该连接的任务）。tenant_id_ctx 仍设（供 ORM 钩子 / audit 读取），与 HTTP 路径 db.py 的 SET LOCAL 模式一致。

### Q3 — 行级事务的成功 job 写入时机（P-U06a-01）
[Answer] **同 per-row 事务内**：`BEGIN → SET LOCAL app.tenant_id → adapter.upsert(业务记录) → INSERT import_job(success) → COMMIT`。业务记录与其 import_job 原子（要么都成功要么都回滚）。失败时 ROLLBACK 整个 per-row 事务，再用独立 bypass session 写 import_job(failed)。

### Q4 — Adapter 注册加载点（P-U06a-02，NF-4 修订）
[Answer] ① main.py lifespan 新增 `register_import_adapters()`（与 register_event_listeners 并列，逐个 try-import U06b/c/d/e register，ModuleNotFoundError → warning 不阻塞）。② **worker 进程也加载**：celery_app 的 `worker_process_init` 信号调用同一 register 函数（HTTP 进程注册 worker 看不到）。③ **NF-4：celery_app.autodiscover_tasks 列表新增 `app.tasks.import_tasks`**，否则 run_import_batch.delay 找不到任务。

### Q5 — Registry 数据结构与线程安全（P-U06a-02）
[Answer] 进程内 **类级 dict**（`ImportAdapterRegistry._adapters: dict[str, ImportAdapter]`）。注册在启动期单线程完成（lifespan / worker init），运行期只读，无需锁。提供 register / get / sources / clear（测试用）。

### Q6 — upload 顺序：DB 先行 + 补偿删除（P-U06a-03，NF-2 修订）
[Answer] **DB 先行**（防 TOCTOU/孤儿，NF-2）：① 流式算 SHA256；② 生成 batch_id；③ **先 INSERT import_batch(status=processing, file_hash, file_r2_key 预计算, ...)** —— 并发同文件由 `UNIQUE(tenant_id,source,file_hash)` 原子拦截，捕获 IntegrityError → 409（不写 R2）；④ INSERT 成功后才 `upload_bytes` 写 R2；⑤ R2 写失败 → **补偿 DELETE batch 行**（同事务未 commit 则 rollback；已 commit 则显式 delete）+ 500。绝不出现"R2 有文件但无 batch"或"两请求都写 R2"。

### Q7 — R2 读取封装（P-U06a-03）
[Answer] 给 U01 `AttachmentService` 加薄封装 `get_object_bytes(bucket, key) -> bytes`（boto3 client.get_object + Body.read）。属 U01 helper 合理扩展（与 U05 Attachment ORM 无关，不破坏 FB-A）。MVP 一次性读入内存（≤ 20MB 可控）；V1 评估流式 TextIOWrapper。

### Q8 — 退避 + 批次互斥（P-U06a-04，NF-3 修订）
[Answer] retry 端点**原子 claim**（NF-3）：`UPDATE import_batch SET status='processing', retry_count=retry_count+1 WHERE id=? AND status IN ('partial','failed') AND retry_count<3 RETURNING *`。0 行 = 已在跑 / 已耗尽 / 状态不符 → 409（IMPORT_RETRY_EXHAUSTED 或 IMPORT_BATCH_BUSY）。claim 成功才 `apply_async(kwargs={"only_failed":...}, countdown=BACKOFF[retry_count])`，`BACKOFF={1:1,2:5,3:30}`。runner 入口二次守卫 status='processing'；改 failed job 时 `SELECT ... FOR UPDATE` 防并发。

### Q9 — only_failed 行还原（P-U06a-04）
[Answer] only_failed=True 时：查 `import_job WHERE batch_id AND status='failed' ORDER BY row_number` → 用 `job.raw_data`（JSONB 原始行）还原 → parse/validate/upsert → **原地 UPDATE 该 job**（attempt_count += 1，status/error_detail/target_resource_id 刷新）。不新增 job 行（UNIQUE(batch_id,row_number) 保证）。

### Q10 — CSV injection 转义位置（P-U06a-05）
[Answer] 仅在**失败明细下载导出 CSV** 时转义（`_csv_safe`：`=+-@` 开头加 `'` 前缀）。导入解析时**不转义**（原样存 raw_data，保真）。放在 domain 层纯函数，下载 service 调用。

### Q11 — 文件大小/行数校验时机（P-U06a-05，NF-6 修订）
[Answer] **三层防护**：① **ASGI/网关层 body 上限**（NF-6：nginx `client_max_body_size 21m` + uvicorn/Starlette 请求体上限），在 multipart 落盘前挡超大请求防 DoS；② upload handler 内累计字节校验 ≤ 20MB（业务兜底）→ 422；③ run_import_batch 解析时流式计数行数 ≤ 5 万（upload 时未解析无法预知行数）→ batch.failed。

### Q11b — 权限命名对齐现有角色（P-U06a-05，NF-5 修订）
[Answer] 用 **`importer.batch:read` / `importer.batch:write` / `importer.mapping:write`**（符合 U01 Q12=B `module.sub:action`，替代原 `import:read/write`）。logical-components 同步更新 default_roles + permission seed：operations 角色加 `importer.batch:read`（现有是 `importer.*:read` 通配，已覆盖但显式列出）；pr / pr_manager / admin 加 `importer.batch:write` + `importer.mapping:write`（mapping 创建限管理员场景，pr_manager+admin）。permission 表 seed 新增这 3 个 scope。

### Q12 — 指标埋点位置（监控）
[Answer] `import_file_size_bytes` 在 upload 端点观测；`import_batch_total{status}` / `import_batch_duration_seconds` / `import_rows_total{result}` 在 run_import_batch 汇总段观测；`import_retry_total` 在 retry 端点观测。structlog 在 runner 各阶段记 batch_id/source/tenant_id。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-design-patterns.md（5 个增量模式）
- **P-U06a-01** 导入 Runner 事务+租户上下文（双 session 策略 + **per-row SET LOCAL app.tenant_id**（NF-1）+ per-row 事务 + 失败 bypass 兜底）+ 完整 runner 伪代码
- **P-U06a-02** ImportAdapter 协议 + Registry（注册机制 + main.py/worker 双加载 + **autodiscover 加 import_tasks**（NF-4）+ source 白名单）
- **P-U06a-03** DB 先行上传 + hash 去重（**先 INSERT batch（UNIQUE 原子）→ 再写 R2 → 失败补偿删除**（NF-2）+ U01 helper + get_object_bytes 封装）
- **P-U06a-04** 两类失败重试 + 批次互斥（解析失败 vs only_failed + **原子 processing claim**（NF-3）+ retry_count 时机 + 退避 BACKOFF）
- **P-U06a-05** 安全文件处理（白名单 + **三层大小/行数校验（网关+handler+解析）**（NF-6）+ openpyxl read_only/data_only + CSV injection 转义 + **importer.batch/mapping 权限**（NF-5））
- 5 个指标埋点位置汇总

### 3.2 logical-components.md（U06a 新增组件）
- modules/importer 组件清单（enums / models / models[含 UNIQUE 约束] / schemas / exceptions / domain[csv_safe + hash] / adapter[Protocol] / registry / repository[含原子 claim + FOR UPDATE] / field_mapping_service / service / deps / api）
- tasks/import_tasks.py（run_import_batch + worker_process_init 注册）
- core/metrics.py 扩展（5 指标）
- core/config.py 扩展（4 配置）
- core/attachment.py 扩展（get_object_bytes 薄封装）
- core/celery_app.py 扩展（autodiscover 加 import_tasks，NF-4）
- main.py 扩展（register_import_adapters + import_router）
- **default_roles.py + permission seed 更新（importer.batch/mapping 权限，NF-5）**
- 部署层：nginx client_max_body_size + uvicorn body 上限（NF-6）
- 启动序列 + 依赖图 + 与 U06b/c/d/e 注册契约

---

## 4. 文件影响预估（NFR Design 阶段仅文档）
- `aidlc-docs/construction/U06a/nfr-design/nfr-design-patterns.md`
- `aidlc-docs/construction/U06a/nfr-design/logical-components.md`

---

**等待用户回复"继续"批准本计划（含 6 条反馈修正 NF-1~6 + 13 个 [Answer]），开始生成 2 份 NFR 设计文档。**
