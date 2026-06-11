# U08 NFR 需求计划（NFR Requirements Plan）

> 单元：U08 — 发文进度看板
> 范围：U08 特异性 NFR 增量（聚合查询性能 / TimeRange 解析 / 只读 RLS / null 安全）；通用 NFR 全部继承 U01-U07
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档（nfr-requirements.md + tech-stack-decisions.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增依赖
- [Answer] **零新增运行时依赖**：纯 SQLAlchemy core 聚合 + Pydantic 读模型，全部复用现有栈。

### Q2 — 聚合性能策略
- [Answer] MVP **实时聚合**（不预聚合 / 不物化视图）：summary 单条无 GROUP BY 聚合、cards 单条 GROUP BY style_id、详情按 style_id 限定。万级 promotion P95 ≤ 500ms；命中 promotion `(tenant_id, cooperation_date)` 相关索引。V1 评估物化视图。

### Q3 — 是否新增索引
- [Answer] 复用 U04 promotion 现有索引（tenant + cooperation_date / style_id / pr_id）；MVP 不新增索引（聚合命中既有索引即可）。若 Build & Test 发现卡片 GROUP BY style_id 慢，再评估 `idx(tenant_id, cooperation_date, style_id)`（记入文档，不强制）。

### Q4 — 只读事务 / 引擎
- [Answer] 只读，走 app 引擎 + RLS（依赖 Session 注入 tenant_id）；不用 bypass；无写/事务边界问题。

### Q5 — null 安全
- [Answer] 所有派生比率/CPL 用 `services/metric/common.safe_div`（分母 0 或 None → None）；SQL 层 SUM/COUNT 返回 NULL 用 COALESCE 归零（计数类），比率类在 Python 层 safe_div。

### Q6 — 监控指标
- [Answer] 不新增自定义 Prometheus 指标（HTTP 时延由 prometheus-fastapi-instrumentator 自动暴露，按 handler 分组）；structlog 记 report 查询 tenant_id + preset + 耗时。

### Q7 — TimeRange 边界
- [Answer] custom 跨度上限 366 天（防全表扫描 DoS）；解析基于 `get_today()`（Asia/Shanghai，FB8）；非法 preset/范围 → 422。

### Q8 — 测试策略
- [Answer] 聚合正确性（构造已知 promotion 数据集断言 summary/cards/detail 数值）+ TimeRange 解析（5 preset + custom 边界 + 跨度超限）+ null 安全（空数据集 rate=null）+ 多租户隔离（A 不见 B）+ safe_div 单元。

---

## 2. 执行步骤

- [x] 2.1 `U08/nfr-requirements/nfr-requirements.md`：性能 SLA（summary/cards/detail）+ 只读 RLS + null 安全 + TimeRange 边界 + 容量 + 测试 + 故事映射 + 一致性校验
- [x] 2.2 `U08/nfr-requirements/tech-stack-decisions.md`：零新增依赖确认 + safe_div 实现 + TimeRange resolve 实现 + 聚合 SQL 模式（FILTER / CASE 折算）+ 索引复用说明 + 无新指标说明
- [x] 2.3 诊断器无警告 + 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 需求文档。**
