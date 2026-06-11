# U10b NFR 需求计划（NFR Requirements Plan）

> 单元：U10b — 平台商品映射
> 范围：极小增量 NFR（唯一约束并发 / 反查性能 / 多租户）；通用 NFR 继承 U01-U10a
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增依赖
- [Answer] **零新增运行时依赖**：纯 CRUD + UNIQUE 约束；复用 SQLAlchemy / Pydantic。

### Q2 — 唯一约束并发
- [Answer] create 依赖 DB UNIQUE(tenant, platform, platform_id) 兜底；并发重复插入 → IntegrityError 捕获转 409（不依赖先查后插的 TOCTOU）。create_or_update 用 SELECT-then-insert/update（导入串行，冲突重试可接受）。

### Q3 — 反查性能
- [Answer] find_by_platform_id 命中 UNIQUE 索引（tenant+platform+platform_id）O(1)；按款式反查走 idx(tenant, style_id)；P95 ≤ 100ms。

### Q4 — 容量
- [Answer] 单租户预估平台商品 ≤ 数万；列表分页（默认 20）；无重计算。

### Q5 — 多租户
- [Answer] platform_product TenantScopedModel + RLS；列表/反查显式 tenant 过滤（防御 + 测试确定性）。

### Q6 — 安全
- [Answer] 写操作 require_permission(product.platform:write) + 引用校验防跨租户挂接（style/sku 必须本租户，RLS 保证 get 返回 None → 422）；写 audit。

### Q7 — 监控
- [Answer] 不新增自定义 Prometheus 指标；structlog 记映射写操作；冲突/校验失败计入既有 HTTP 4xx。

### Q8 — 测试
- [Answer] 单元：引用校验逻辑（如有 domain）。集成：create 成功 / 重复 409 / create_or_update 幂等 / find_by_platform_id 命中+未命中 / 引用校验 422 / 跨租户隔离 / 删除。API：鉴权 + OpenAPI。

---

## 2. 执行步骤

- [x] 2.1 `U10b/nfr-requirements/nfr-requirements.md`：唯一约束并发(IntegrityError→409) + 反查性能 + 多租户 RLS + 安全(引用校验防跨租户) + migration 014 + 测试 + 故事映射 + 一致性校验
- [x] 2.2 `U10b/nfr-requirements/tech-stack-decisions.md`：零新增依赖 + platform_product 模型落点 + PlatformProductService + create_or_update upsert + IntegrityError catch + migration 014 + 测试落点
- [x] 2.3 诊断器无警告（nfr-requirements.md spec-format 假阳性 IGNORE）+ 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 需求文档。**
