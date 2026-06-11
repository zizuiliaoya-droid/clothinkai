# U14 业务规则（Business Rules）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 故事：EP09-S02~S05

---

## 1. 工作进度表（EP09-S02）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U14-01 | 按月（period_month）+ pr_id 维度聚合 promotion，时间字段 cooperation_date | 指标契约 |
| BR-U14-02 | 约篇件数 = COUNT(promotion)；已发布 = COUNT(publish_status='已发布') | 契约 |
| BR-U14-03 | 档期内/催发/重要催发/超时 = 复用 URGE_STATUS_SQL_EXPR FILTER 计数 | U04 |
| BR-U14-04 | 信息完整度 = 已填 like_count 的已发布数 / 已发布数（safe_div） | 契约 |
| BR-U14-05 | 爆文数 = COUNT(like_count >= HIT_STAT_THRESHOLD 默认 500)（与标记阈值 1000 不同）；爆文率 = 爆文数/已发布 | §8 阈值 |
| BR-U14-06 | 点赞数 = like_sum_expr 折算求和（抖音/快手 ×0.1）；CPL = 成本/有效点赞（safe_div） | U04 |
| BR-U14-07 | 召回完成率 = 召回成功数/应召回数；月度完成率 = 已发布/约篇；超时率 = 超时/约篇（均 safe_div） | 契约 |

## 2. 爆款约篇数量（EP09-S03）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U14-10 | set_target 写 target_planning，UNIQUE(tenant,pr_id,style_id,period_month) 冲突 → upsert 更新 min_target | — |
| BR-U14-11 | min_target 必须 ≥ 0 | CHECK |
| BR-U14-12 | list_with_actuals(month)：联接 promotion 按月聚合实际约篇数（pr_id+style_id+cooperation_date 月） | EP09-S03 |
| BR-U14-13 | status = 达标(actual≥min) / 未达标；gap = actual - min（正超额负缺口） | EP09-S03 GWT |
| BR-U14-14 | 设目标 = pr_manager（report.target:write）；查看 = report.*:read | 权限 |

## 3. 店铺数据看板（EP09-S04）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U14-20 | get_dashboard：qianniu_daily 按 date SUM 聚合（visitors/pay_amount/pay_orders + extra 字段）+ 左联 store_daily 手动字段 | EP09-S04 |
| BR-U14-21 | 时间筛选复用 resolve_time_range（last_7d/30d/this_month/last_month/custom） | U08 |
| BR-U14-22 | upsert_manual(date, fields)：ON CONFLICT(tenant,date) 更新 ad_spend_total/zhitongche_spend/yinli_spend/remark | EP09-S04 |
| BR-U14-23 | 手动编辑 = operations（report.store_daily:write） | 权限 |

## 4. 投产报表（EP09-S05）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U14-30 | 按款式聚合 qianniu_daily + ad_daily + promotion + settlement，时间字段 qianniu_daily.date | 契约 |
| BR-U14-31 | 退货退款率 = 成功退款金额 / 支付金额（safe_div） | 公式 |
| BR-U14-32 | 待确认收货金额 = 支付金额 - 成功退款金额 | 公式 |
| BR-U14-33 | 推广总花费 = 站外推广成本(promotion) + 站内投放(ad_daily.cost) | 公式 |
| BR-U14-34 | 加购成本 = 推广总花费 / 总加购数（safe_div） | 公式 |
| BR-U14-35 | 净投产比 = 待确认收货金额 / 推广总花费（safe_div） | 公式 |
| BR-U14-36 | 推广单件成交成本 = 加购成本 / 加购转化率 / (1-退货率)（链式 safe_div） | 公式 |
| BR-U14-37 | 任意分母 0/None → null（前端 "—"）；缺失 extra 字段按 0/null | §除零 |
| BR-U14-38 | 周环比：current 期 + previous 等长期（date_from-span ~ date_from-1）分别聚合 | EP09-S05 GWT |
| BR-U14-39 | exclude_brushing 参数存在但 V1 默认 False 不影响结果（order_adjustment U16 落地） | 职责边界 |
| BR-U14-40 | qianniu_daily 缺失列（退款金额/加购数/加购转化率）从 extra JSONB 取，无则 0/null | §9 |

## 5. 通用

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U14-50 | 全部报表纯读，无写业务表（target_planning/store_daily 为配置写，非事件） | 架构 |
| BR-U14-51 | 全部聚合显式 WHERE tenant_id（RLS 之外防御层）+ time_range 过滤 | U08 模式 |
| BR-U14-52 | 时间跨度 ≤ 366 天（resolve_time_range 校验） | U08 |

---

## 6. 错误码矩阵

| 场景 | HTTP | code |
|---|---|---|
| 非法时间 preset/range | 422 | REPORT_INVALID_TIME_RANGE |
| target 款式/PR 不存在 | 422 | INVALID_REFERENCE |
| store_daily date 非法 | 422 | VALIDATION_ERROR |
| 权限不足 | 403 | PERMISSION_DENIED |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| EP09-S02~S05 GWT 全覆盖 | ✅ |
| 指标契约公式 + 除零 null | ✅ §1 §4 |
| 爆文统计阈值 500（≠标记 1000） | ✅ BR-U14-05 |
| 周环比 + exclude_brushing 占位 | ✅ BR-U14-38/39 |
| 时间筛选复用 U08 | ✅ |
