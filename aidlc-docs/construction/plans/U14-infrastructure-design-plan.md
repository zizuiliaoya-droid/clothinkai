# U14 基础设施设计计划（Infrastructure Design Plan）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 低基础设施增量：migration 018（2 表）+ report Celery 队列（precompute 占位）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务？
[Answer] 否。报表聚合在既有 backend 同步查询；precompute_report_cache 占位任务（report 队列）由既有 celery-worker/celery-beat 承载（V1 不强制启用）。

### Q2：新数据库表？
[Answer] migration 018：target_planning + store_daily 2 表 + RLS + UNIQUE + idx + 6 scope seed。无回填。

### Q3：新 Celery 队列？
[Answer] report 队列（shared-infrastructure 已预留）；V1 报表以实时聚合为主，precompute_report_cache 占位（celery-worker 订阅 report 队列；Beat 调度默认注释，需要时启用）。

### Q4：新环境变量 / R2 / Redis？
[Answer] 无。

### Q5：性能 / 索引？
[Answer] 复用既有索引：qianniu_daily/ad_daily idx(tenant,date)、platform_product idx(tenant,style)、promotion idx(cooperation_date)；target_planning idx(tenant,period_month)、store_daily UNIQUE(tenant,date) 即查询路径。投产报表跨表用子查询预聚合（应用层优化，非基础设施）。

### Q6：部署与回滚？
[Answer] 部署 = 代码 + migration 018 + celery-worker -Q 追加 report（precompute 启用时）；回滚 downgrade 017（DROP 2 表 + DELETE scope）+ 代码回退。无外部依赖。

### Q7：本地 Docker 验证端口？
[Answer] U14 Build & Test 用 PG16:5557 + Redis7:6412（接 U13 的 5556/6411）。

### Q8：监控？
[Answer] 复用 Prometheus + report_query_duration_seconds（NFR Design 定义）监控报表慢查询；无额外配置。

---

## 1. 步骤

- [x] 1.1 编写 infrastructure-design.md（无新服务；migration 018 2 表 DDL+RLS+6 scope seed；report 队列+precompute 占位；复用索引；零新依赖/环境变量/R2/Redis；本地 Docker 5557/6412）
- [x] 1.2 编写 deployment-architecture.md（拓扑无变更；部署 checklist+migration 018+report 队列可选+验证步骤(2 表/scope/4 报表/周环比/除零/手动 upsert/多租户)+回滚）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
**注：infrastructure-design.md 的 spec-format 假阳性（Missing Overview/Architecture）IGNORE。**
