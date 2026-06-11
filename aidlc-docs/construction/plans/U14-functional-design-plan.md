# U14 功能设计计划（Functional Design Plan）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表（EP09-S02~S05）
> 依赖：U05（settlement）、U13（qianniu_daily/ad_daily）
> 复用：U08 report 模块 + resolve_time_range + metric/common.safe_div

---

## 0. 澄清问题（[Answer] 预填）

### Q1：4 张报表落在哪个模块？
[Answer] 复用 `modules/report/`，追加 4 个 service（work_progress_service / target_planning_service / store_daily_service / production_service）+ 对应 schemas/repository/api 端点；metric 逻辑放 `services/metric/`（work_progress.py / store_daily.py / style_roi.py）。

### Q2：需要新建几张表？
[Answer] 2 张新表（migration 018）：
- `target_planning`（爆款约篇目标）：pr_id/style_id/period_month/min_target；UNIQUE(tenant, pr_id, style_id, period_month)
- `store_daily`（店铺日报手动输入字段）：date/ad_spend_total(全站推消耗)/zhitongche_spend(直通车消耗)/yinli_spend(引力魔方消耗)/remark；UNIQUE(tenant, date)
工作进度表 + 投产报表为纯读聚合（无新表）。

### Q3：工作进度表数据来源与时间维度？
[Answer] 纯读聚合 promotion（U04），按 `cooperation_date` 月度 + pr_id 维度。KPI：约篇件数/档期内/催发/重要催发/超时/已发布/信息完整度/已取消/应召回/召回成功/召回完成率/超时率/月度完成率/爆文数(≥爆文统计阈值 500)/爆文率/点赞数(折算)/成本/CPL。复用 URGE_STATUS_SQL_EXPR + like_sum_expr。

### Q4：爆款约篇目标如何计算达标？
[Answer] TargetPlanningService.set_target 写 target_planning；list_with_actuals(month) 联接 promotion 按月聚合实际约篇数（COUNT by pr_id+style_id+cooperation_date 月）→ 返回 min_target / actual / status(达标 actual>=min / 未达标) / gap(actual-min)。

### Q5：店铺数据看板如何聚合 + 手动输入？
[Answer] StoreDailyService.get_dashboard(time_range)：从 qianniu_daily 按 date SUM 聚合（visitors/pay_amount/pay_orders + extra JSONB 字段）+ 左联 store_daily 手动字段；按 time_range 过滤。upsert_manual(date, fields)：ON CONFLICT(tenant, date) upsert 手动 3 字段。

### Q6：投产报表核心公式 + 除零？
[Answer] ProductionService.get_report 按款式聚合 qianniu_daily(支付/退款/加购) + ad_daily(站内投放) + promotion(站外成本/点赞) + settlement。5 核心指标用 safe_div（分母 0→null）：退货退款率 / 待确认收货金额 / 加购成本 / 净投产比 / 推广单件成交成本。时间维度 qianniu_daily.date。

### Q7：周环比怎么实现？
[Answer] get_report 接受 time_range，内部再算"上一等长周期"（date_from - span ~ date_from-1），两期分别聚合，返回 current + previous + change(safe_div 环比)。

### Q8：exclude_brushing 占位？
[Answer] ProductionService.get_report(exclude_brushing: bool = False) 参数存在但 V1 不影响结果（order_adjustment 在 U16 落地）；style_roi metric 预留 exclude_brushing 形参默认 False + 注释 TODO U16。

### Q9：qianniu_daily 字段不足（24 列）怎么办？
[Answer] U13 qianniu_daily 仅 visitors/pay_amount/pay_orders 一等字段 + extra JSONB。U14 报表所需额外字段（成功退款金额/加购数/加购转化率等）从 extra JSONB 取（COALESCE((extra->>'refund_amount')::numeric, 0) 等）。基础口径用现有字段，缺失字段从 extra 提取，无则按 0/null。

### Q10：权限 scope？
[Answer] 读 scope 复用 report.*:read 通配（operations/pr_manager 已有）；新增 write scope：report.target:write（pr_manager 设目标）+ report.store_daily:write（operations 编辑手动字段）；migration 018 seed。

### Q11：precompute_report_cache 任务？
[Answer] V1 暂以实时聚合为主（数据量可控）；precompute_report_cache 作为可选 Celery 任务占位（report 队列），默认不强制启用，文档说明。Functional Design 不展开，留 NFR/Infra。

---

## 1. 步骤

- [x] 1.1 阅读 EP09-S02~S05 GWT + 开发文档指标契约（时间维度/计算公式/除零规则/阈值）+ 已有 report/metric 模块
- [x] 1.2 编写 domain-entities.md（TargetPlanning/StoreDaily 2 表 + 4 报表读模型 schemas + 聚合来源映射 + 时间维度表）
- [x] 1.3 编写 business-rules.md（BR-U14-01~ 工作进度 KPI 口径/爆款约篇达标/店铺聚合+手动输入/投产 5 公式+除零+周环比+exclude_brushing 占位/权限）
- [x] 1.4 编写 business-logic-model.md（4 UC + 时间筛选复用 + 跨表聚合 SQL 模式 + 跨单元契约 U04/U05/U13）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
