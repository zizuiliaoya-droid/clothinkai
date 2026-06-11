# U14 NFR 设计计划（NFR Design Plan）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 模式：P-U14-01（工作进度+爆款约篇聚合）、P-U14-02（店铺聚合+手动 upsert）、P-U14-03（投产跨表聚合+周环比+safe_div）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：工作进度月度聚合 SQL 结构？
[Answer] GROUP BY pr_id 单 SQL：COUNT + FILTER(URGE_STATUS_SQL_EXPR='档期内'/'催发'/'重要催发'/'超时') + FILTER(publish_status='已发布') + FILTER(like_count>=HIT_STAT_THRESHOLD) + like_sum_expr 折算 + SUM(cost_snapshot)。比率（信息完整度/爆文率/召回完成率/月度完成率/超时率/CPL）service 层 safe_div 后处理。

### Q2：爆款约篇达标聚合？
[Answer] target_planning LEFT JOIN promotion 子查询（按 pr_id+style_id+月聚合 actual_count）；service 算 status/gap。set_target 用 pg_insert ON CONFLICT(tenant,pr_id,style_id,period_month) DO UPDATE min_target。

### Q3：店铺聚合 + 手动 upsert？
[Answer] get_dashboard：qianniu_daily GROUP BY date SUM + LEFT JOIN store_daily(date) 手动字段；upsert_manual：pg_insert store_daily ON CONFLICT(tenant,date) DO UPDATE 手动 3 字段 + remark。

### Q4：投产跨表聚合避免笛卡尔积？
[Answer] 主查询 FROM style LEFT JOIN qianniu_daily（经 platform_product）；ad_daily/promotion 各自子查询预聚合 GROUP BY style_id 后 LEFT JOIN（防多表 JOIN 行数膨胀）。qianniu 退款/加购字段从 extra JSONB COALESCE 提取。

### Q5：周环比实现？
[Answer] get_report 内部：current=[from,to]；span=(to-from)；previous=[from-span-1, from-1]；两次调用同一 aggregate_by_style（换日期参数）；返回 items=current + previous=previous。环比变化由前端或服务端汇总按需。

### Q6：5 公式 safe_div 在哪层？
[Answer] repository 只返回原始 SUM 列（pay_amount/refund_amount/add_cart_count/promo_cost/ad_spend）；service 逐行 safe_div 算 退货率/待确认收货/加购成本/净投产比/单件成交成本（分母 0→null）。

### Q7：exclude_brushing 占位落地？
[Answer] ProductionService.get_report(exclude_brushing=False) 透传到 aggregate_by_style；V1 该参数不改 SQL（注释 TODO U16：剔除 order_adjustment WHERE exclude_from_roi）；style_roi.net_roi 形参占位。

### Q8：指标埋点 + 时间筛选复用？
[Answer] report_query_duration_seconds.labels(report_type).time() 包裹每个 service 聚合；时间筛选全复用 resolve_time_range（domain）。

---

## 1. 步骤

- [x] 1.1 编写 nfr-design-patterns.md（P-U14-01 工作进度 GROUP BY pr+FILTER 聚合+爆款约篇 ON CONFLICT+actual 子查询+达标 / P-U14-02 店铺 qianniu SUM+store_daily 左联+手动 upsert / P-U14-03 投产 style 主查询+ad/promo 子查询预聚合+extra COALESCE+周环比等长上期+service safe_div 5 公式+exclude_brushing 占位 完整伪代码 + 一致性校验）
- [x] 1.2 编写 logical-components.md（modules/report 追加 9 文件 + services/metric 3 子模块 + 横切 4 改动(metrics/main/celery_app/tasks) + migration 018 2 表 + 依赖图无循环 + 3 测试文件）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
