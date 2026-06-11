# U06b 非功能需求（NFR Requirements）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：U06b 特异性 NFR 增量（adapter 解析/校验/upsert 正确性与性能）；框架级 NFR 全部继承 U06a，通用 NFR 继承 U01-U05
> 定位：**极小增量**（无新表/端点/Celery 任务/依赖/配置/指标）

---

## 1. 与基线的关系

### 1.1 完全继承（不重复定义）
- **U06a 框架 NFR**：异步导入吞吐（5 万行 ≤ 5 分钟）/ upload P95 ≤ 2s / 解析内存 O(1) / Celery 失败语义 4 类 / 行级隔离（per-row 事务 + bypass 兜底，FB-C）/ worker 租户上下文（per-row SET LOCAL，NF-1）/ 文件威胁模型 / CSV injection / 5 个导入指标
- **U02 NFR**：`SkuRepository.upsert_atomic` 原子性（P-U02-03 ON CONFLICT RETURNING is_inserted）/ GIN trgm 搜索 / 价格字段 MVP 阶段对 product:write 角色可见（U09 后切字段级）
- **U01 通用 NFR**：多租户 RLS / 审计 / Prometheus / Sentry / structlog / pytest

### 1.2 U06b 增量（仅 4 项）
1. **解析正确性**：Decimal 价格转换（不用 float）+ 空值/必填语义
2. **upsert 幂等正确性**：重跑 / 重复 sku_code 不产生重复 sku；style 复用不误覆盖
3. **adapter 性能贡献**：每行 ≤2 DB 往返，不拖累 U06a 5 万行 SLA
4. **跨租户正确性**：runner per-row SET LOCAL 下 style/sku 必带正确 tenant_id

---

## 2. 性能 NFR

### 2.1 adapter 每行开销预算

| 操作 | DB 往返 | 说明 |
|---|---|---|
| style 解析 | 1 | `get_by_code(style_code)` 命中复用 / 未命中 INSERT+flush |
| brand 软关联 | 0 或 1 | 仅 brand_code 非空时查 `(tenant, brand_code)` |
| sku upsert | 1 | `upsert_atomic` 单条 ON CONFLICT RETURNING |
| **合计** | **≤ 2（无 brand）/ ≤ 3（有 brand）** | 常数往返，不随文件大小变化 |

- 不拖累 U06a SLA：5 万行 × ~3 往返 ≈ 15 万次，行级串行 ~150-200 行/秒 → ≤ 5 分钟达标
- **不引入批量 upsert**（MVP）：行级独立事务语义优先（一行失败不影响其他，FB-C）；批量优化留 V1

### 2.2 解析开销
- Decimal 转换：纯 Python `Decimal(str)`，O(1)/字段，无 DB / 无显著开销
- raw_data 保真：直接存原始行 dict（不深拷贝转换），内存 O(行宽)

### 2.3 容量
- 无 U06b 特有增量：复用 U06a `IMPORT_MAX_ROWS=50000`；5 万行 = 最多 5 万 sku upsert + ≤5 万 style 查（多数复用，实际 distinct style 远小于行数）
- import_job 每 batch ≤ 5 万行（U06a 容量基线）

---

## 3. 正确性 NFR（U06b 核心）

### 3.1 Decimal 精度

| 规则 | 要求 |
|---|---|
| 价格类型 | `decimal.Decimal`（**禁用 float**，避免 0.1+0.2 类精度丢失） |
| 千分位 | 解析前去除 `,`（如 `"1,299.00"` → `Decimal("1299.00")`） |
| 精度保真 | 保留输入精度；DB 列 Numeric(10,2) 兜底 2 位 |
| 非法值 | 无法解析 / 负数 → **行校验失败**（不静默置 0/None） |
| 空值 | 空字符串 / None → None（可空字段） |

### 3.2 upsert 幂等

| 场景 | 保证机制 |
|---|---|
| 同文件重复 upload | U06a 框架层 hash 去重 → 409（不到 adapter） |
| 同 batch retry（only_failed） | 失败行重跑；成功行不重处理（only_failed 仅扫 status=failed）+ UNIQUE(batch_id,row_number) 原地更新 |
| 文件内重复 sku_code | U02 `ON CONFLICT(tenant_id, sku_code) WHERE is_deleted=false DO UPDATE`：第二次为 UPDATE 路径（is_inserted=False），不报错不重复 |
| style 复用 | 既有 style **不更新字段**（仅复用 id，Q4）；导入不覆盖系统内维护的款式资料 |

### 3.3 跨租户正确性
- runner per-row `SET LOCAL app.tenant_id`（NF-1）+ ORM before_flush 注入：adapter 创建的 Style 必带 batch.tenant_id
- `upsert_atomic` 显式传 tenant_id（与会话 SET LOCAL 一致）
- **NFR 测试要求**：runner 跑完后查 style/sku.tenant_id == batch.tenant_id（真实 adapter 验证，区别于 U06a FakeAdapter 写 brand 的验证）

---

## 4. 可靠性 NFR

| 维度 | 处理（继承 U06a） |
|---|---|
| 行级校验失败 | validate 返回错误列表 → runner 写 import_job.failed（error_detail），per-row 事务隔离 |
| 行级 upsert 异常 | adapter 抛出 → runner 捕获写 failed（独立 bypass session，防回滚带走） |
| style INSERT 并发冲突 | uq_style_code partial UNIQUE → 该行 failed，retry 时 get_by_code 命中复用（Q4） |
| partial batch | 成功行已 commit，失败行未入库；不整批回滚；retry only_failed |

---

## 5. 安全 NFR
- 复用 U06a 文件上传威胁模型（白名单 / 上限 / 路径隔离 / openpyxl read_only 不执行宏）
- 失败下载 CSV injection 防护由 U06a `csv_safe` 处理（raw_data 中危险前缀转义）
- **raw_data 保真存储**（不转换），失败下载时才经 csv_safe
- 价格字段不回显到 structlog（adapter 不打印 parsed 值；MVP 阶段价格对 product:write 角色可见，U09 后切字段级）
- 无 U06b 特有安全增量

---

## 6. 可观测性 NFR
- **复用 U06a 5 个指标**（label `source="manual_style_sku"` 自动区分）：import_batch_total / import_rows_total / import_batch_duration_seconds / import_file_size_bytes / import_retry_total
- **不新增指标**（避免指标爆炸；按 source label 切分已足够）
- adapter 内**不逐行打点**（防 5 万行日志/指标洪泛）；汇总由 U06a runner 统一记

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | parse_row（Decimal 千分位 / 空值 / str strip / 各 type）+ validate（必填 6 项 / 数值非负 / sourcing_type 白名单 / 长度上限 各失败分支）— 纯函数无 DB |
| 集成 | 注册真实 adapter → upload 样本 CSV → _run_import_batch → 断言：style/sku 入库 + 复用既有 style 不覆盖 + Decimal 精度 + brand 软关联 + partial（缺字段失败行）+ retry only_failed 幂等 + tenant_id 正确 |
| 覆盖率 | adapter ≥ 85%（继承 U01 service 基线） |
| 异步调用 | 直接 await `_run_import_batch`（不经 broker）；monkeypatch AsyncSession + mock get_object_bytes 注入样本 CSV（仿 U06a test_import_runner，committed 数据 + 清理） |

> U06a 用 FakeImportAdapter 验框架；U06b 用**真实 StyleSkuImportAdapter** 验业务正确性（style/sku 入库 + Decimal）。

---

## 8. 故事 NFR 映射

| 故事 | U06b 特有 NFR 验收 |
|---|---|
| EP07-S07 上传 | source=manual_style_sku 端到端入库 style+sku；每行 ≤2-3 DB 往返不拖累 SLA |
| EP07-S08 去重 | 复用 U06a hash 去重（框架层，adapter 无关） |
| EP07-S09 映射版本 | manual_style_sku mapping=None 回退内置默认；自定义 v2 覆盖 |
| EP07-S10 失败下载/重试 | 缺字段行 → failed → 下载 + retry only_failed；sku upsert 幂等（重跑不重复） |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 每行 DB 往返预算量化（≤2/3） | ✅ §2.1 |
| Decimal 精度（禁 float + 千分位 + 非法值失败） | ✅ §3.1 |
| upsert 幂等（4 场景） | ✅ §3.2 |
| style 复用不覆盖 | ✅ §3.2 + §3.3 |
| 跨租户 tenant_id 正确（真实 adapter 验证） | ✅ §3.3 + §7 |
| 复用 U06a 5 指标无新增 | ✅ §6 |
| 无新依赖/服务/配置 | ✅ §1.1（见 tech-stack-decisions） |
