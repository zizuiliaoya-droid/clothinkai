# U14 NFR 需求计划（NFR Requirements Plan）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 增量式：复用 U01/U08 报表基线 + U13 采集数据；纯读聚合为主 + 2 配置表

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否引入新依赖？
[Answer] 零新增依赖。聚合用 SQLAlchemy text() 原生 SQL（同 U08）；safe_div 复用 U08；时间筛选复用 resolve_time_range。

### Q2：报表性能 SLA？
[Answer] 工作进度（月度 GROUP BY pr）P95 ≤ 500ms；爆款约篇 P95 ≤ 300ms；店铺数据（qianniu_daily 按 date 聚合）P95 ≤ 500ms；投产报表（4 表跨表聚合 + 周环比 2 期）P95 ≤ 800ms（最重）。复用 U13 idx(tenant,date) + U04 promotion 索引。

### Q3：容量假设？
[Answer] 单租户款式 ≤ 5 万、qianniu_daily/ad_daily 每日数百~数千行 × 365 天；投产报表 time_range ≤ 366 天（resolve_time_range 限制）；GROUP BY style 结果 ≤ 数千行（分页）。

### Q4：投产报表跨表聚合性能优化？
[Answer] 子查询预聚合（ad_daily/promotion 各自 GROUP BY style_id 后 JOIN）避免笛卡尔积；qianniu_daily 经 platform_product → style_id 关联。复用 U13 idx(tenant,date)+platform_product idx(tenant,style)。precompute_report_cache Celery 任务占位（report 队列，V1 不强制启用）。

### Q5：安全 / 权限？
[Answer] 全部只读聚合 + RLS 隔离；显式 WHERE tenant_id 防御层（bypass 测试）。读 scope report.*:read 通配；写 scope report.target:write/report.store_daily:write seed。字段级权限：成本/金额对 operations 可见（report 角色基线，无额外字段屏蔽——report 是聚合层不暴露单条敏感字段）。

### Q6：除零 / 空值语义？
[Answer] 全部比率经 safe_div（分母 0/None→null，前端 "—"）；金额空按 0 汇总；qianniu_daily 缺失 extra 字段 COALESCE 0/null。与 U08/U04 一致。

### Q7：可观测性？
[Answer] 追加 1 指标 `report_query_duration_seconds{report_type}`（工作进度/爆款约篇/店铺/投产 4 类聚合耗时直方图），监控慢查询。

### Q8：多租户隔离测试？
[Answer] 测试矩阵必含：A 租户报表不含 B 数据（RLS + 显式 tenant）；target_planning/store_daily UNIQUE 跨租户独立；投产跨表聚合不跨租户串数据。

### Q9：migration 编号？
[Answer] migration 018（接 017）：target_planning + store_daily 2 表 + RLS + UNIQUE + idx + report.target:read/write + report.store_daily:read/write + report.work_progress:read + report.production:read scope seed。downgrade DROP 2 表 + DELETE scope。

### Q10：测试覆盖？
[Answer] service ≥ 80%、metric/domain ≥ 85%、api ≥ 60%；整体 ≥ 70%。重点：投产 5 公式 safe_div 边界（分母 0→null）、周环比上期计算、爆款约篇达标/缺口、店铺手动 upsert。

---

## 1. 步骤

- [x] 1.1 编写 nfr-requirements.md（零依赖 + 4 报表 SLA + 投产跨表聚合优化(子查询预聚合) + 除零 safe_div + 1 指标 + 多租户隔离 + migration 018 + precompute 占位 + 测试矩阵）
- [x] 1.2 编写 tech-stack-decisions.md（零依赖复用 text() SQL/safe_div/resolve_time_range + modules/report 落点 4 service + services/metric 3 子模块 + 2 新表 + report_query_duration 指标 + migration 018 片段 + 测试落点）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
