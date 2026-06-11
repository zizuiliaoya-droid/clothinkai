# U02 NFR 需求计划（NFR Requirements Plan）

> 单元：U02 — 商品 / SKU 基础  
> 阶段：MVP 第 2 个单元  
> 范围：仅 U02 特异性 NFR；通用 NFR 见 `U01/nfr-requirements/nfr-requirements.md`

---

## 1. 单元上下文

### 1.1 本单元的 NFR 立场（与 U01 的关系）

U01 已建立完整 NFR 基线：
- 性能：API P95 ≤ 500ms（通用），关键路径 ≤ 200ms
- 可用性：99.5% / 7×24，单点故障容忍降级
- 安全：JWT + RBAC + 多租户 RLS + 审计 append-only
- 可维护性：mypy strict + ruff + pytest 70% 覆盖率
- 运维：Sentry + 健康检查 + 备份恢复

**U02 不重新规定**这些通用 NFR；本文档仅列出：
- U02 特异的容量/性能指标（万级 style + 50 万 sku）
- U02 特异的搜索/匹配 SLA（款号反查 P95 ≤ 300ms）
- U02 与 U04/U16 演化时的字段稳定契约

### 1.2 复用 U01 NFR 基线
- 全部错误码体系（U01 errors.py）
- 全部审计装饰器（U01 audit.py）
- 全部权限装饰器（U01 permissions.py）
- 全部 RLS 策略（U01 rls.py + Alembic 002 migration）
- 全部安全约束（输入验证 / SQL 注入防护 / 速率限制）

---

## 2. 计划步骤

### Step 1 — 分析功能设计
- [x] 1.1 读取 domain-entities.md（实体 / 索引 / RLS）
- [x] 1.2 读取 business-rules.md（性能预估章节 9）
- [x] 1.3 读取 business-logic-model.md（9 个 UC 的执行特征）

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 总结 U01 NFR 基线
- [x] 2.2 列出 U02 增量 NFR 维度
- [x] 2.3 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-requirements.md
- [x] 3.1 性能 SLA（列表 / 详情 / 创建 / 编辑 / 匹配查询）
- [x] 3.2 容量预估（style / sku / brand 行数 + 索引大小）
- [x] 3.3 可用性 / 可恢复性（数据备份延伸到 U02 表）
- [x] 3.4 安全（cost_price 字段保护、Brand 字典访问范围）
- [x] 3.5 可维护性（演化兼容 U04/U09/U10a/U16/U17）
- [x] 3.6 监控指标（Sentry tag、健康检查端点延伸）
- [x] 3.7 数据迁移规约（与 final.xlsx 历史导入的契合）

### Step 4 — 生成 tech-stack-decisions.md
- [x] 4.1 复用 U01 已选技术栈（pydantic v2 / sqlalchemy 2.0 async / asyncpg）
- [x] 4.2 U02 新增第三方依赖（无 / 有：列出版本）
- [x] 4.3 索引策略选择（B-tree vs GIN / pg_trgm 评估）
- [x] 4.4 模糊匹配实现（ILIKE vs pg_trgm vs Elasticsearch）
- [x] 4.5 软删 vs 硬删的实施权衡

### Step 5 — 提交完成消息 + 等待审批
- [x] 5.1 展示 "📊 NFR Requirements Complete - U02"
- [x] 5.2 等待用户 P1/P2 反馈或批准
- [x] 5.3 批准后写入 audit.md

---

## 3. 澄清问题（请填 [Answer]）

> 每问预填合理默认值，作答即代表确认。

### 3.1 容量与性能

**Q1**：每租户 Style 表预期最大行数？峰值 QPS（API 调用）？

[Answer]: 单租户 Style 上限 5 万行，Sku 上限 50 万行，Brand 字典 200 行；MVP 阶段全租户合计预期 10 万 style / 100 万 sku；峰值 QPS：列表查询 ≤ 50 QPS / 写操作 ≤ 10 QPS / match 查询 ≤ 100 QPS（PR 每天集中录推广时段）

**Q2**：列表接口（GET /api/styles/）P95 SLA？P99？

[Answer]: P95 ≤ 200ms（默认筛选条件 + 分页 20）；P99 ≤ 500ms；含 keyword ILIKE 模糊搜索时 P95 ≤ 400ms；返回 page_size=100 时 P95 ≤ 600ms；超时阈值 3s

**Q3**：写操作（POST/PUT）P95 SLA？

[Answer]: 单条创建 P95 ≤ 150ms（含 ORM 钩子 + audit 写入）；编辑 P95 ≤ 200ms（含 dict diff + audit 条件写）；批量导入不在 U02 范围（U06b 启用）

**Q4**：款号匹配 GET /api/styles/match P95 SLA？

[Answer]: 
- 精确匹配（?style_code=W001） P95 ≤ 50ms（B-tree 索引秒级返回）
- **模糊匹配 SLA 与索引方案绑定（避免 SLA 与方案不匹配）**：
  - **方案 A（采纳）**：U02 直接建 pg_trgm GIN 索引；模糊匹配 P95 ≤ 300ms，覆盖 5 万行 / 租户场景
  - 方案 B（备选）：仅 ILIKE，无 GIN，模糊匹配 P95 SLA 调宽到 ≤ 800ms（仅适合 1 万行以下租户）
- **U02 选 A**：alembic migration 中**直接执行** `CREATE EXTENSION pg_trgm;` + `CREATE INDEX style_search_trgm_idx ON style USING gin ((style_code || ' ' || style_name || ' ' || COALESCE(short_name, '')) gin_trgm_ops);`，确保模糊匹配在万级表上稳定 ≤ 300ms
- 不再使用"预启用扩展但不建索引"的占位策略（启用扩展本身不提升性能）
- 性能验证：U02 测试用例必须包含 `test_match_perf_with_5w_styles`（生成 5 万 mock style 后实测模糊匹配 P95 ≤ 300ms）

### 3.2 可用性 / 容错

**Q5**：U02 操作失败时的降级策略？

[Answer]: **严格区分"业务未匹配"和"系统失败"**：
- 创建失败（系统级 5xx / 异常 / DB 超时）→ 直接返回 5xx + Sentry 上报 + 前端提示用户重试，**绝不伪装成成功**
- 列表失败 → 不降级，直接报错（5xx + Sentry），前端提示重试
- **match 接口必须分两类返回**：
  - **业务未匹配**（合法查询无结果）：精确反查返回 `404 NOT_FOUND` 或 `{ matched: false, candidates: [] }`；模糊反查返回 `200 + { candidates: [], total: 0 }`；前端允许用户手动输入款号字符串继续录入
  - **系统失败**（DB 异常 / 超时 / RLS 错误 / 权限校验失败）：返回 `5xx` 或 `403`，**绝不返回空候选列表**；前端展示错误提示要求用户稍后重试，不允许此时手动输入（避免用户误以为商品库不存在该款号）
- 不引入缓存层（数据修改频率高，缓存收益小）

**Q6**：U02 表变更（migration）的执行方式？

[Answer]: 与 U01 Infrastructure 决策一致 — **schema migration 走专用 migration job**，不通过 Zeabur 滚动部署内联执行：
- 使用 `.github/workflows/migrate.yml`（U01 已建立）手动触发 `workflow_dispatch`
- 流程：PR 合并 → 部署前手动跑 `migrate.yml`（执行 alembic upgrade head）→ 验证 staging 通过 → 触发 `deploy-prod.yml` 部署应用
- 失败回滚：alembic downgrade 在 migration job 内执行；应用层不部署
- U02 4 张新表（style / sku / brand / style_detail_image）+ pg_trgm 扩展启用，全部由 alembic 单次 migration 落地
- 不需要数据回填（首次部署即填入）

### 3.3 安全

**Q7**：cost_price / purchase_price 字段是否需要数据库级加密？

- [ ] **A. 需要 pgcrypto 加密**（防 DBA 直查）
- [ ] **B. 不加密**（依赖应用层字段权限 + 审计）
- [ ] **C. 加密 cost_price，不加密 base_price**

[Answer]: B — 不加密。

**威胁模型说明（明确边界）**：
- 本决策**仅防御**：普通业务用户跨角色越权读取（PR/设计师/运营 不应看到 cost_price/purchase_price）
- 本决策**不防御**：DBA / 运维 / 拥有数据库直接读权限的人查看明文（视为可信内部人员）
- RLS 解决的是租户隔离（租户 A 看不到租户 B），**不解决字段保密**（同租户的 DBA 仍可读所有字段）
- 应用层防护：`clothing_app` 角色（业务连接）通过 service 层 BR-U02-41 + Pydantic schema 字段过滤；DBA 用 `clothing_bypass`（应急专用，仅高级管理员持有）
- 过度加密会破坏 U14 报表 SQL 聚合性能；审计日志记录所有变更
- 演进选项（V2 之后）：若客户合规要求或上市审计要求，再考虑 pgcrypto 字段级加密 + KMS 集成（届时由独立单元承担，约 1-2 人周）

**Q8**：Brand 字典是否允许跨租户共享（如全部租户共用品牌库）？

- [ ] **A. 严格隔离**（每租户独立维护）
- [ ] **B. 允许平台管理员预置共享品牌**
- [ ] **C. 全局共享**

[Answer]: A — 严格隔离；与多租户原则一致；后续若有"集团"需求由 U09 / V1 引入"租户组"机制后再考虑

**Q9**：style_code / sku_code 是否需要按业务规则强制格式（如必须以字母开头）？

[Answer]: 仅长度 + 字符集校验（≤64 字符 / 字母 / 数字 / 下划线 / 连字符），不强制业务格式；MVP 历史数据导入需要兼容用户已有命名（W001 / 24-S-001 / SKU-2024-001 等多种风格）；前端可按租户配置展示提示但后端不强制

**Q10**：image / detail_images 上传的安全约束？

[Answer]: 复用 U01 AttachmentService 全部约束 — 类型白名单（jpg/jpeg/png/webp/avif）、单文件 ≤ 5MB、总大小 ≤ 50MB / 款式（10 张详情图上限）；不在 U02 重新定义；MVP 阶段不引入 ClamAV 病毒扫描（V1 优化）

### 3.4 可维护性 / 演化

**Q11**：U02 创建的 ORM 模型在 U10a 扩展 design_status 状态机时的迁移路径？

[Answer]: U02 用 VARCHAR(16) 存 design_status，仅 2 个枚举值；U10a 直接扩展 Python Enum 添加新值即可（DB 字段无需变更）；同时新建 design_workflow 子表存历史状态变迁；不破坏 U02 已有数据

**Q12**：U16（订单）需要快照 base_price 到 order 表，U02 是否需要预留接口？

[Answer]: U02 仅保证 Sku.base_price 字段可读；快照逻辑由 U16 实施（在 U16 OrderService 调用 SkuService.get_for_order(sku_id) 取值）；U02 不需要专门的 snapshot endpoint

**Q13**：U06b（手动导入适配器）按 (tenant_id, style_code/sku_code) 幂等 upsert，U02 是否预留？

[Answer]: 是 — 数据库层 UNIQUE (tenant_id, style_code) 索引天然支持 ON CONFLICT；U06b 的 Adapter 通过 `SkuService.upsert_by_code()` 接口调用。

**边界约束（必须严格遵守）**：
- `upsert_sku` 与 `create_sku/update_sku` **共享同一套底层校验** — 复用 `_validate_sourcing_price()` / `_check_price_write_permission()` / Pydantic schema / DB UNIQUE 约束 / `@audit` 装饰器
- 不允许在 upsert 路径中"绕过"普通创建/编辑路径的任何业务规则：
  - 必填字段校验、价格非负、sourcing_type 一致性 — 完全相同
  - 字段写权限（cost/purchase 仅 admin/跟单/财务） — 完全相同（导入操作的 actor 必须是有写权限的角色）
  - 审计日志写入 — 完全相同（区分 `sku.create_via_import` 和 `sku.update_via_import` 两个 action 名）
  - 唯一约束冲突处理 — 数据库层 ON CONFLICT (tenant_id, sku_code) DO UPDATE
- 实施模式：在 `service.py` 内 `create_sku`、`update_sku`、`upsert_sku` 三个方法**共用** `_apply_sku_changes(sku, payload, user, audit_action)` 私有方法，仅入口分支不同
- U02 阶段 `upsert_by_code` 接口实现，但**不暴露 HTTP 端点**（U06b 启用时通过内部模块调用）；U02 测试覆盖单元测试 + 一个集成测试（直接调用 service 层）

### 3.5 监控

**Q14**：U02 需要哪些 Sentry transaction 标签 / 监控指标？

[Answer]: 
- **Sentry**（异常 + 慢事务抽样）：
  - 复用 U01 通用 tag（environment / tenant_id / actor_type）
  - 新增 tag：`module=product`
  - 用途：异常捕获、关键错误告警、慢事务（traces_sample_rate 抽样）
  - **不依赖 Sentry 计算 P95/P99**（trace sample rate 是抽样数据，不适合 SLA 监控）
- **Prometheus**（性能 SLA 监控）：
  - 通过 U01 已部署的 `prometheus-fastapi-instrumentator` 自动暴露 `/metrics` 端点
  - 关键指标：`http_request_duration_seconds`（histogram，按 handler 分桶）→ Prometheus / Grafana 计算 P50/P95/P99
  - U02 SLA（list P95 ≤ 200ms / match P95 ≤ 300ms / 写 P95 ≤ 200ms）以 Prometheus 指标为准
- 告警阈值（Prometheus alertmanager + Sentry 告警双通道）：
  - Prometheus：`histogram_quantile(0.95, http_request_duration_seconds_bucket{handler=~"/api/styles.*"}) > 1` 持续 5min → 告警
  - Sentry：style.create 错误率 > 5% / 任何 5xx 错误 → 即时告警
- U14 单元才落地业务指标看板（按租户、按品类的 product 增长率等）

**Q15**：除了 /health / /ready（U01 已实现），U02 是否需要新的健康端点？

[Answer]: 不需要；U02 模块依赖与 U01 一致（DB / Redis / R2），健康检查不增加新依赖；端点统一为 `/health`（liveness）和 `/ready`（readiness），与 U01 main.py 实现 + nfr-design-patterns.md / deployment-architecture.md 完全一致

### 3.6 数据迁移

**Q16**：final.xlsx 历史数据迁移到 Style/Sku 表的范围？

> 复习：在 INCEPTION 阶段已澄清 final.xlsx 仅作为字段结构参考，**不做历史数据完整迁移**；MVP 由租户在系统启用后自行手动录入或通过 U06b 适配器导入。

[Answer]: 确认不做历史数据迁移；MVP 阶段管理员可使用 final.xlsx 字段对照表了解字段映射，但实际数据由租户在 U06b（手动导入框架）就绪后通过 Excel 模板批量上传；U02 阶段提供 0 数据起步的能力

### 3.7 搜索性能升级路径

**Q17**：模糊匹配的索引方案与未来升级路径？

[Answer]: 
- **U02 决策（与 Q4 一致）**：直接建 pg_trgm GIN 索引，覆盖 5 万行 / 租户场景
  ```sql
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX style_search_trgm_idx ON style 
    USING gin (
      (style_code || ' ' || style_name || ' ' || COALESCE(short_name, '')) 
      gin_trgm_ops
    ) WHERE is_deleted = false;
  ```
- **服务层查询适配**：保留 ILIKE 表达式（PostgreSQL planner 在有 GIN trgm 索引时会自动使用），ORDER BY 部分 `similarity()` 函数排序候选相关性
- **性能基准**（5 万行测试数据 / 单租户）：
  - 无索引（仅 ILIKE）：P95 ~800ms（不可接受）
  - 有 GIN trgm：P95 ≤ 200ms（达标）
- **升级阈值**：
  - 单租户 style ≥ 50 万行 或 P95 > 500ms 持续 1 周 → 评估迁移到 PostgreSQL 全文搜索（tsvector + GIN）或独立 Elasticsearch（V2+ 范围）
  - 监控通过 Prometheus `http_request_duration_seconds{handler="/api/styles/match"}` 自动追踪

### 3.8 测试覆盖

**Q18**：U02 单元测试覆盖率最低门槛？

[Answer]: 复用 U01 全局门槛 ≥ 70%；具体到 U02：
- service.py：≥ 80%（核心业务逻辑必须测）
- repository.py：≥ 70%（CRUD + 关键查询）
- domain.py：≥ 90%（业务规则 BR-U02-NN 全覆盖）
- api.py：≥ 60%（端到端请求/响应/错误码）

集成测试场景必须覆盖：
- 创建 style + 重复 style_code 冲突
- 创建 sku + style_id 不存在 + sku_code 冲突
- 编辑 cost_price + 不同角色权限矩阵
- match 接口精确 + 模糊匹配
- 软删 style 受 active sku 阻塞
- 多租户隔离回归（租户 A 看不到租户 B 的 style）

---

## 4. 决策摘要（在用户填答后由 AI 整理）

> 此处在用户回复"填好了"之后，AI 总结所有 [Answer] 形成最终 NFR 清单，作为 nfr-requirements.md / tech-stack-decisions.md 的输入。
