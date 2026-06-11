# U03 NFR 需求计划（NFR Requirements Plan）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性 NFR；通用 NFR 全部继承 `U01/nfr-requirements/`

---

## 1. 与 U01/U02 NFR 基线的关系

U01 已建立全部通用 NFR；U02 已落地：
- 字段权限硬编码过渡模式 + TODO U09 路径
- 审计敏感值脱敏策略
- 数据库原子 upsert + 与 partial UNIQUE 对齐
- 软删 + 引用检查 + 后续单元注入路径
- match 降级语义（业务未匹配 vs 系统失败）

**U03 直接复用 U02 全部模式，本文档仅列特异性指标**：
- 容量：1763+ 博主 / 租户（远小于 U02 5 万 style）
- 性能 SLA（与 U02 一致：list P95 ≤ 200ms，写 P95 ≤ 200ms）
- JSONB tag GIN 索引（U02 没有）
- 字段权限作用对象：quote / wechat / phone（U02 是 cost_price / purchase_price）

---

## 2. 计划步骤

### Step 1 — 分析功能设计
- [x] 1.1 读取 U03 3 份功能设计文档
- [x] 1.2 与 U01/U02 NFR 基线对齐复用边界

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U03 增量 NFR 维度
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-requirements.md
- [x] 3.1 性能 SLA（list / 详情 / 创建 / 编辑 / search）
- [x] 3.2 容量预估（≤ 3000 博主 / 租户）
- [x] 3.3 字段权限威胁模型（quote/wechat/phone 不加密理由）
- [x] 3.4 监控指标（复用 U02 Prometheus + Sentry 体系）
- [x] 3.5 测试覆盖（与 U02 同等门槛）

### Step 4 — 生成 tech-stack-decisions.md
- [x] 4.1 复用 U01/U02 全部技术栈
- [x] 4.2 索引策略（GIN trgm 单字段 + GIN JSONB）
- [x] 4.3 upsert 策略（与 U02 完全一致）

### Step 5 — 提交完成消息 + 等待审批
- [x] 5.1 展示 "📊 NFR Requirements Complete - U03"
- [x] 5.2 等待用户审批

---

## 3. 澄清问题（请填 [Answer]）

> 因 U03 高度复用 U02 模式，仅 6 个核心问题需要确认。

### 3.1 容量与性能

**Q1**：单租户 Blogger 表预期上限？峰值 QPS？

[Answer]: 
- MVP 上限 3000 博主 / 租户（按业务文档 1763+ × 1.5 倍冗余）
- V1 上限 1 万（含跨平台扩展）
- V2 上限 5 万
- 峰值 QPS：list 30 QPS / search 50 QPS（PR 集中筛选时段）/ 写 5 QPS

**Q2**：性能 SLA？

[Answer]:
- `GET /api/bloggers/`（多筛选 + 分页 20）：P95 ≤ 200ms / P99 ≤ 500ms
- `GET /api/bloggers/?keyword=`（GIN trgm）：P95 ≤ 150ms
- `GET /api/bloggers/?category_tag=穿搭`（GIN JSONB）：P95 ≤ 100ms
- 写操作：P95 ≤ 150ms
- 详情 `GET /api/bloggers/{id}`：P95 ≤ 80ms
- 与 U02 SLA 同等量级

### 3.2 安全

**Q3**：quote / wechat / phone 字段是否需要 DB 加密？

- [ ] **A. 加密** (pgcrypto)
- [ ] **B. 不加密**（依赖 RLS + 应用层字段权限）

[Answer]: B — 不加密，与 U02 cost_price 决策一致。

威胁模型边界：
- **本决策仅防御**：普通业务用户跨角色越权读取（设计师 / 跟单 / 运营 不应看到 quote/wechat/phone）
- **本决策不防御**：DBA / 运维 / 拥有 DB 直接读权限的人查看明文（视为可信内部人员）
- **应用层防护**：service 层 BR-U03-41 + Pydantic schema 字段过滤
- **审计**：所有 quote / wechat / phone 变更全部进 audit_log（仅记 `*_changed: true` 标记，不存历史值）

V2+ 演进：若客户合规要求或上市审计要求 → 引入 pgcrypto 字段级加密 + KMS 集成（独立单元承担）。

### 3.3 监控

**Q4**：U03 需要哪些自定义 Prometheus 指标？

[Answer]: 
- 复用 U01/U02 通用指标（http_request_duration_seconds 等）
- 新增 1 个自定义指标：`blogger_search_results_count` (Histogram, buckets: [0, 1, 5, 20, 100]) — 监控搜索零结果率
- 不新增 Counter（upsert 调用统计可继承 U02 sku_upsert_total 模式扩展，但 U03 阶段不实施）
- Sentry tag：新增 `module=blogger`

### 3.4 数据迁移

**Q5**：1763+ 博主历史数据迁移方式？

[Answer]: 不在 U03 阶段实施。MVP 启用后由 U06c（博主导入适配器）通过 Excel 模板批量上传，调用 `BloggerService.upsert_by_xiaohongshu_id` 内部 API 入库；U03 提供 0 数据起步能力。

### 3.5 测试覆盖

**Q6**：U03 测试覆盖率门槛？

[Answer]: 与 U02 同等门槛
- `service.py` ≥ 80%
- `repository.py` ≥ 70%
- `domain.py` ≥ 90%
- `api.py` ≥ 60%

集成测试必须覆盖：
1. EP04-S01 创建博主 + xiaohongshu_id 重复 409
2. EP04-S01 重复时返回 existing_blogger_id 引导
3. EP04-S02 编辑 quote + 字段权限矩阵（4 角色：admin/pr/finance/designer）
4. EP04-S02 audit_log 含 `quote_changed: true`（脱敏）
5. EP04-S03 多筛选组合（关键字 + 范围 + tag）
6. EP04-S03 PR 角色看不到 quote / wechat / phone
7. JSONB tag 包含查询命中 GIN 索引
8. upsert INSERT/UPDATE 路径 + 复用同一套校验
9. 软删 + 引用检查（U03 阶段始终允许）
10. 多租户隔离回归

---

## 4. 决策摘要（用户填答后由 AI 整理）

> 用户回复"继续"后，AI 总结 [Answer] 形成最终 NFR 清单。
