# U06a 功能设计计划（Functional Design Plan）

> 单元：U06a — 统一导入框架  
> 阶段：MVP 第 6 个单元（导入并行支线 L2；依赖仅 U01）  
> 依赖：**U01 全部就绪**（core/attachment 的 R2 helper `upload_bytes`/`get_signed_url` + Celery + 多租户 contextvars + 审计 + 异常框架）。**不依赖 U05 attachment ORM 表**（见 §0 FB-A）  
> 节奏：单批生成（框架层，不含具体业务 Adapter — 那些在 U06b/c/d/e）

---

## 0. 已应用 P1/P2 反馈修正（5 条 + 3 个 Open Question）

| # | 反馈 | 修正内容 |
|---|---|---|
| **FB-A** | 依赖声明与 attachment FK 冲突；attachment 是 U05 补齐的 shared 基础设施，U06a 声明"仅依赖 U01"会断链 | **U06a 不使用 attachment ORM 表 / 不建 FK**。import 原始文件用 **U01 原生 R2 helper**（`AttachmentService.upload_bytes` / `get_signed_url` / `delete`，这些在 U01 就存在，与 U05 的 Attachment ORM 无关）。`import_batch` 直接存 `file_r2_key`（VARCHAR）+ `file_bucket`（固定 "private"）。依赖严格 = U01。 |
| **FB-B** | 走 attachment 会被 `ALLOWED_PURPOSES` 白名单（仅 settlement_proof）拦成 422 | 既然不走 attachment ORM/通用 attachment API，**不涉及 ALLOWED_PURPOSES**。r2_key 前缀固定 `imports/{tenant_id}/{batch_id}/{safe_filename}`，bucket=`private`，由 ImportService 内部直接调 `upload_bytes` 写入（后端中转上传，见 FB-E 上传流程）。 |
| **FB-C** | Adapter 契约缺 session/actor/tenant context；Celery worker 无 HTTP CurrentActiveUser；adapter 各自 commit 却没传 session；RLS/tenant filter 可能不生效 | **Runner 统一持有事务边界与租户上下文**。Adapter 签名改为 `async def upsert(self, parsed, *, session: AsyncSession, tenant_id: UUID, actor_id: UUID \| None) -> tuple[UUID, bool]`（不再接收 `User`）。`run_import_batch` worker 内：① 读 `batch.tenant_id` → `tenant_id_ctx.set(...)`；② 用 `engine_app` 连接执行 **`SET app.tenant_id`（session 级，非 LOCAL，跨 per-row 事务存活）**；③ 逐行 `BEGIN/COMMIT` 独立事务，Adapter 复用 runner 传入的 session；④ actor_id = `batch.created_by`。失败行写 import_job 用**独立 bypass session**（防被该行回滚带走，复用 U05 `_log_event_dispatch_failure` 模式）。 |
| **FB-D** | 状态与去重范围前后不一致（processing vs pending；同 hash vs 同 source+hash） | **状态统一**：upload **直接创建 `processing`**（匹配 EP07-S07 验收原文）+ 同请求内 enqueue Celery；**去掉 pending**。终态 `completed`/`partial`/`failed`。**去重统一**：唯一约束 = `UNIQUE(tenant_id, source, file_hash)`（明确记为对 EP07-S08 的精确化 refinement — 同文件总是同 source，加 source 只放开"同字节用于不同导入目标"的罕见合法场景，不削弱 story 防重复语义）。 |
| **FB-E** | retry 语义不可落地（parse 失败无行级 failed rows；缺 UNIQUE(batch_id,row_number)；更新原 job 还是新增；retry_count 递增时机） | **两类失败分流**：① **解析阶段失败**（文件读不出/格式错 → `failed` 且无 import_job 行）→ retry **重新整文件解析**（从 R2 重读）；② **行级失败**（`partial`，有 import_job.failed 行）→ retry **仅重跑 failed 行**。新增 `UNIQUE(batch_id, row_number)`。retry **原地更新 import_job 行**（`attempt_count += 1` + 刷新 status/error_detail，不新增 attempt 记录，MVP 简化）。`import_batch.retry_count` 在 **retry 端点触发时（enqueue 前）递增**，> 3 → 409 拒绝。退避 1s/5s/30s 由 retry 端点 `countdown` 控制。 |

### Open Questions 回答

| 问题 | 回答 |
|---|---|
| OQ1：U06a 是否仍"只依赖 U01"？ | **是**。通过 FB-A 不引用 U05 attachment 表实现；只用 U01 的 R2 helper。 |
| OQ2：上传是后端 multipart 接收并上传 R2，还是复用 presigned PUT attachment API？ | **后端 multipart 中转**（FB-E）：后端接收 multipart → **流式计算 SHA256** → 查去重（409）→ `upload_bytes` 写 R2 → 创建 batch(processing) → enqueue。**不用 presigned PUT**（hash 必须服务端先算才能在建 batch 前去重；且 import 文件是后端中转工件，非前端直传场景）。 |
| OQ3：同文件不同 mapping_version 是否允许重新导入？ | **否**。`UNIQUE(tenant_id, source, file_hash)` 即 409，与 mapping_version 无关。mapping_version 仅记录在 batch 上（审计/可追溯），不放开去重。需要换映射重跑属罕见运维场景，V1 评估加 `force` 标志。 |

---

## 1. 单元上下文

### 1.1 覆盖故事

| 故事 | 阶段 | 说明 |
|---|---|---|
| EP07-S07 | MVP | 手动上传 Excel/CSV → 后端中转算 hash + 上传 R2 → 创建 import_batch（status=**processing**）+ 异步 Celery 解析入库 |
| EP07-S08 | MVP | 文件 hash 去重（SHA256）→ `UNIQUE(tenant_id, source, file_hash)` 已存在返回 409（提示 batch_id）|
| EP07-S09 | MVP | 字段映射版本管理（field_mapping v1/v2…，旧版本不删，同 (tenant,source) 仅一个 active，历史 batch 记录所用 version）|
| EP07-S10 | MVP | 失败行下载 CSV（原始数据 + error_detail）+ 重试（行级失败仅重跑 failed 行；解析失败重跑整文件；指数退避 1s/5s/30s，最多 3 次）|

> EP07-S01 是 Epic Overview（U06a + U06b/c/d/e + U12 + U13 并集承担），本单元不单独实现。

### 1.2 职责边界（关键决策）

**U06a 的职责（框架层）**：
- 通用上传 API（`POST /api/import/upload` multipart：file + source [+ mapping_version]）— 后端中转
- `import_batch` / `import_job` / `field_mapping` 三张 ORM 表（**均不含 attachment FK**，FB-A）
- file_hash SHA256 去重（`UNIQUE(tenant_id, source, file_hash)` → 409，FB-D）
- 异步解析编排 `run_import_batch(batch_id)` Celery 任务（runner 持有事务 + 租户上下文，FB-C）
- **ImportAdapter 协议 + ImportAdapterRegistry**（注册中心；具体 Adapter 由 U06b/c/d/e 注册，与 U05 listener 注册同模式）
- FieldMappingService（版本管理 + active 切换）
- 失败明细下载（CSV StreamingResponse，含原始行 + error_detail）
- 重试（FB-E 两类失败分流 + 退避 + 上限 3 + retry_count）
- batch 列表 / 详情查询

**U06a 不做（其他单元）**：
- ❌ 具体业务 Adapter（StyleSku/Blogger/Promotion/Settlement → U06b/c/d/e；Qianniu/Wanxiangtai/Huitun → U13）
- ❌ attachment ORM 表 / 通用 attachment API（U05 已建的 shared 基础设施，U06a 不引用，FB-A）
- ❌ 平台凭据 credential + 加密（U12 / V1）
- ❌ 自动采集 crawler_task + Worker pull（U13 / V1）
- ❌ data_quality_issue 数据质量看板（U13 / V1）
- ❌ 字段级权限改造（U09；本单元用权限字符串 import:read / import:write 过渡）

### 1.3 与下游 Adapter 的契约（U06b/c/d/e 实现，FB-C 修订）

```python
# modules/importer/adapter.py（U06a 定义协议）
class ImportAdapter(Protocol):
    source: str          # 业务来源标识，如 "manual_style_sku" / "manual_blogger"
    target_table: str    # 目标表名（用于审计 / 展示）

    def parse_row(self, row: dict[str, Any], mapping: FieldMapping) -> dict[str, Any]:
        """按 field_mapping 把原始列名映射成目标字段 + 类型转换（纯函数，不碰 DB）。"""

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空 = 通过；纯函数）。"""

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,      # ← runner 持有并传入（FB-C）
        tenant_id: UUID,            # ← 显式租户（worker 无 HTTP CurrentUser）
        actor_id: UUID | None,      # ← = batch.created_by
    ) -> tuple[UUID, bool]:
        """幂等 upsert，按业务键。返回 (resource_id, is_inserted)。
        不自行 commit — 事务边界由 runner 控制（每行独立事务）。"""
```

> `ImportAdapterRegistry.register(source, adapter)` / `get(source)`；`run_import_batch` 按 `batch.source` 取 adapter。
> U06b/c/d/e 在各自模块 `register()`，main.py 启动时加载（与 U05 finance/promotion listener 注册同模式）。
> **upload 时即校验 `source` 在 registry 白名单**（不在 → 422，不建 batch）；runner 内二次防御（registry.get 失败 → batch.failed + 原因）。

### 1.4 Celery worker 租户上下文与事务（FB-C 落地要点）

```
run_import_batch(batch_id):
  1. bypass session 读 batch（拿 tenant_id / source / file_r2_key / created_by / mapping_version）
  2. registry.get(batch.source)（缺失 → batch.failed + return）
  3. 从 R2 下载文件（get_signed_url 或直接 client.get_object）
  4. 解析（csv / openpyxl read_only）→ 行迭代器
     ├─ 解析失败 → batch.status=failed, total_rows=0, error_summary=...; return（FB-E ①）
  5. tenant_id_ctx.set(batch.tenant_id)
     用 engine_app 连接：SET app.tenant_id = '<tenant_id>'（session 级，跨 per-row 事务存活）
  6. 逐行：
       BEGIN（per-row 独立事务）
         parsed = adapter.parse_row(row, mapping)
         errs = adapter.validate(parsed)
         if errs: raise → 记 import_job.failed（独立 bypass session 写）
         rid, inserted = await adapter.upsert(parsed, session=app_session, tenant_id=..., actor_id=...)
         import_job.success(target_resource_id=rid)
       COMMIT（成功）/ ROLLBACK（失败，failed 行用 bypass session 另写，不被回滚）
  7. 汇总：imported / failed → batch.status = completed(全成功) / partial(部分失败) / failed(全失败)
  8. 写 audit（import.batch_completed，脱敏）
```

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — import_batch 状态机（FB-D 修订）
[Answer] **4 状态**：`processing`（upload 即创建，Celery 解析中）→ `completed`（全部成功）/ `partial`（部分行失败）/ `failed`（解析失败或全行失败）。**无 pending**（upload 同请求内 enqueue）。重试：partial/failed → processing。

### Q2 — import_job 粒度（FB-E 修订）
[Answer] **每行一条**：row_number + status[success/failed] + raw_data(JSONB 原始行) + error_detail + target_resource_id + **attempt_count**。`UNIQUE(batch_id, row_number)`。retry 原地更新（attempt_count += 1）。MVP 单文件 ≤ 5 万行；10 万级分区留 V1。

### Q3 — 文件存储（FB-A / FB-B 修订）
[Answer] **R2 private 桶，key=`imports/{tenant_id}/{batch_id}/{safe_filename}`**，通过 **U01 R2 helper `upload_bytes` 后端中转写入**（不经 attachment ORM、不经通用 attachment API、不涉 ALLOWED_PURPOSES）。`import_batch.file_r2_key`(VARCHAR) + `file_bucket`(固定 "private")。失败明细 CSV 实时生成（StreamingResponse，不持久化）。

### Q4 — file_hash 去重范围（FB-D 修订）
[Answer] **`UNIQUE(tenant_id, source, file_hash)`**（SHA256）。同租户同来源同文件 → 409（提示已有 batch_id）。明确记为 EP07-S08 的精确化（不削弱防重复语义）。

### Q5 — 异步解析触发（FB-D 修订）
[Answer] upload **同步**：算 hash → 去重检查 → upload_bytes 写 R2 → 创建 batch(**processing**) → `run_import_batch.delay(batch_id)`（Celery default 队列）→ 返回 batch（202 语义）。

### Q6 — 文件格式与解析库
[Answer] **CSV + XLSX**：CSV 标准库 `csv`；XLSX `openpyxl`（read_only 流式）。MIME/扩展名校验 + 大小上限（默认 20MB，settings 可配）+ 行数上限（默认 5 万，超限 422）。新增配置 `IMPORT_MAX_FILE_MB` / `IMPORT_MAX_ROWS`。

### Q7 — field_mapping 结构
[Answer] **JSONB**：`{"columns":[{"source_col":"商品编码","target_field":"style_code","required":true,"type":"str","transform":null},...]}`。表：(tenant_id, source, version, mapping_config, is_active, created_by)。`UNIQUE(tenant_id, source, version)` + 部分唯一 `UNIQUE(tenant_id, source) WHERE is_active`（同 source 仅一个 active；新建自动 inactivate 旧 active）。

### Q8 — 重试策略落点（FB-E 修订）
[Answer] retry 端点：retry_count（上限 3，超 → 409）→ enqueue `run_import_batch`（行级失败传"仅 failed 行"模式；解析失败重跑整文件）；退避用 Celery `countdown`（1s/5s/30s 按 retry_count 取）。行内异常计入 import_job，不整体 autoretry。

### Q9 — 行级事务边界（FB-C 修订）
[Answer] **每行独立事务**（runner 持有 engine_app session + SET app.tenant_id 会话级）；adapter.upsert 不自行 commit，runner per-row commit/rollback；failed 行用**独立 bypass session** 写 import_job（防回滚带走，复用 U05 模式）。

### Q10 — 权限
[Answer] **`import:write`（上传/重试）+ `import:read`（查询/下载）**，require_permission。created_by 记录上传者；下载/重试同租户 import:read/write 即可（运营协作）。

### Q11 — Adapter 缺失处理
[Answer] upload 时校验 source ∈ registry 白名单（不在 → 422，不建 batch）；runner 内二次防御（registry.get 失败 → batch.failed + 原因，防 Adapter 模块未部署）。

### Q12 — 测试中的 Adapter
[Answer] 测试注册 `FakeImportAdapter`（内存 upsert + 可配置第 N 行失败 + 可配置 upsert 幂等），验证框架编排（解析/分发/行级结果/两类重试/下载/租户上下文）。真实 Adapter 端到端在 U06b/c/d/e 测。

---

## 3. 生成产物（3 份功能设计文档）

### 3.1 domain-entities.md
- `ImportBatch`（id, tenant_id, source, file_hash, original_filename, **file_r2_key**, **file_bucket**, mapping_version, status, total_rows, imported, failed, retry_count, error_summary, created_by, created_at, updated_at）— **无 attachment FK**（FB-A）
- `ImportJob`（id, tenant_id, batch_id [FK], row_number, status, raw_data JSONB, error_detail, target_resource_id, attempt_count, created_at, updated_at）— `UNIQUE(batch_id, row_number)`（FB-E）
- `FieldMapping`（id, tenant_id, source, version, mapping_config JSONB, is_active, created_by, created_at, updated_at）
- 3 Enum：ImportBatchStatus（4：processing/completed/partial/failed，FB-D）/ ImportJobStatus（2：success/failed）/ ImportSource（白名单占位，U06b-e 扩展）
- ER 图（Mermaid）+ 唯一约束（`UNIQUE(tenant_id, source, file_hash)` / `UNIQUE(tenant_id, source, version)` / 部分 `UNIQUE(tenant_id, source) WHERE is_active` / `UNIQUE(batch_id, row_number)`）
- ImportAdapter Protocol + Registry 契约（FB-C 新签名，含 session/tenant_id/actor_id）
- 演化路线（U06b-e Adapter / U12 credential / U13 crawler_task + data_quality_issue）

### 3.2 business-rules.md
- 上传校验（格式/大小/行数/source 白名单）
- file_hash 去重（SHA256 流式 + `(tenant_id,source,file_hash)` 409，FB-D）
- field_mapping 版本切换（新建自动 inactivate 旧 active；历史 batch 记 version）
- 行级解析/校验/upsert 编排（每行独立事务 + bypass session 失败兜底 + worker 租户上下文，FB-C）
- 重试规则（FB-E 两类失败分流 + 退避 + 上限 3 + retry_count 递增时机 + attempt_count）
- 失败下载（CSV 含原始行 + error_detail）
- 状态机推进（processing → completed/partial/failed，FB-D）
- 错误码矩阵（409 去重/超 retry / 422 格式·行数·source 白名单 / 404 batch）

### 3.3 business-logic-model.md
- UC-1 upload（中转算 hash → 去重 → upload_bytes → 建 batch(processing) → enqueue，FB-E 上传流程）
- UC-2 run_import_batch（Celery runner：bypass 读 batch → 取 adapter → R2 下载 → 解析 → 设租户上下文 → 逐行独立事务 → 汇总，FB-C 时序）
- UC-3 field_mapping 版本管理
- UC-4 失败下载（StreamingResponse）
- UC-5 retry（行级失败 vs 解析失败两路，FB-E）
- 端到端时序（upload → Celery → adapter upsert → batch 汇总）
- Adapter 注册契约时序（registry + main.py 加载）

---

## 4. 验收对齐（INCEPTION unit-of-work.md U06a 验收）
- ✅ 相同 hash 文件返回 409（`UNIQUE(tenant_id, source, file_hash)`）
- ✅ 失败行可下载 CSV（含 error_detail）
- ✅ 重试只跑 failed 行（行级失败路径；解析失败重跑整文件，FB-E）
- ✅ EP07-S07~S10 全覆盖
- ✅ 依赖严格 = U01（不引用 U05 attachment 表，FB-A）

---

## 5. 文件影响预估（Functional Design 阶段仅文档）
- `aidlc-docs/construction/U06a/functional-design/domain-entities.md`
- `aidlc-docs/construction/U06a/functional-design/business-rules.md`
- `aidlc-docs/construction/U06a/functional-design/business-logic-model.md`

---

**等待用户回复"继续"批准本计划（含 5 条反馈修正 + 12 个 [Answer]），开始生成 3 份功能设计文档。**
