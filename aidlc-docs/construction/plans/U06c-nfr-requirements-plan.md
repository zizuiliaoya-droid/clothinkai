# U06c NFR 需求计划（NFR Requirements Plan）

> 单元：U06c — 博主导入适配器
> 范围：U06c 特异性 NFR（adapter 解析/校验/upsert 正确性）；框架级 NFR 继承 U06a，通用 NFR 继承 U01-U05
> 节奏：**极小增量**（与 U06b 同构但更简单 —— 单实体，无新表/端点/Celery/依赖/配置/指标）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 / upload P95 / 解析内存 / Celery 失败语义 / 行级隔离 / NF-1 SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U03 NFR（`BloggerRepository.upsert_atomic` 原子性 / GIN trgm + JSONB 搜索 / 联系方式字段权限过渡 PRICE/CONTACT_VISIBLE_ROLES，U09 前 MVP 不强制字段级）
- U01 通用 NFR

### 1.2 U06c 增量（3 项，比 U06b 少 1 项）
1. **解析正确性**：list（标签分隔串 → JSONB 数组）+ int（follower_count）+ Decimal（quote，禁 float）
2. **upsert 幂等正确性**：重跑 / 重复 xiaohongshu_id 不产生重复 blogger（U03 ON CONFLICT）
3. **跨租户正确性**：runner per-row SET LOCAL 下 blogger.tenant_id 正确

> 比 U06b 少"每行 ≤2-3 DB 往返"维度（单实体仅 1 次 upsert，往返恒为 1）。

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 每行 DB 往返
[Answer] **恒为 1 次**（单次 upsert_atomic；无 style 查/brand 查）。比 U06b 更轻；不拖累 U06a SLA。

### Q2 — 类型解析正确性
[Answer] list（`_split_tags`：按 `;；,，` 拆分 + strip + 去空，纯 Python O(字段长)）；int（去千分位 + int，非法/负数 → 校验失败）；Decimal（复用 U06b _to_decimal，禁 float）。空值 → str/int/decimal→None，list→[]。

### Q3 — upsert 幂等
[Answer] 同文件 upload → U06a hash 409；同 batch retry only_failed / 文件内重复 xiaohongshu_id → U03 `ON CONFLICT(tenant,xiaohongshu_id) DO UPDATE`（第二次 UPDATE，is_inserted=False）。验证：重跑不重复 blogger；文件内同 ID 两行 → 第二行 UPDATE。

### Q4 — 跨租户正确性
[Answer] 复用 U06a NF-1（runner per-row SET LOCAL + ORM 钩子）；upsert_atomic 显式传 tenant_id。NFR 测试：runner 创建的 blogger.tenant_id == batch.tenant_id（真实 adapter 验证）。

### Q5 — 容量
[Answer] 复用 U06a IMPORT_MAX_ROWS=50000。5 万行 = 最多 5 万 blogger upsert。import_job 每 batch ≤ 5 万行。无特有增量。

### Q6 — 可观测性
[Answer] 复用 U06a 5 指标（label source=manual_blogger）。无新增。adapter 不逐行打点。

### Q7 — 安全
[Answer] 复用 U06a 文件威胁模型 + csv_safe。U06c 增量：**联系方式字段（wechat/phone）不回显到 structlog**（结合 U03 CONTACT_VISIBLE_ROLES，MVP 阶段对有 blogger:write 角色可见，U09 后切字段级）。raw_data 保真。

### Q8 — 测试
[Answer] 真实 BloggerImportAdapter：unit（parse_row 含 _split_tags/int/Decimal + validate）+ integration（注册 → upload 样本 CSV → runner → blogger 入库 + 标签 JSONB + 同 ID UPDATE 幂等 + partial + tenant_id）。adapter ≥ 85%。复用 U06a test_import_runner 模式。

---

## 3. 生成产物（2 份文档）
- nfr-requirements.md：与基线关系 + 3 项增量 + 性能（每行 1 往返）+ 正确性（list/int/Decimal + 幂等 + 跨租户）+ 安全（联系方式日志不回显）+ 复用 5 指标 + 测试
- tech-stack-decisions.md：复用 U06a/U03（无新依赖/服务/配置/指标）；唯一增量 adapters/blogger.py + _split_tags

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06c/nfr-requirements/{nfr-requirements,tech-stack-decisions}.md`

---

**等待用户回复"继续"批准本计划（含 8 个 [Answer]），开始生成 2 份 NFR 需求文档。**
