# U14 业务逻辑模型（Business Logic Model）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 故事：EP09-S02~S05

---

## 1. 用例列表

| UC | 名称 | 故事 | 角色 |
|---|---|---|---|
| UC-1 | 工作进度表 | EP09-S02 | PR 主管 |
| UC-2 | 爆款约篇目标设置 + 达标跟踪 | EP09-S03 | PR 主管 |
| UC-3 | 店铺数据看板 + 手动输入 | EP09-S04 | 运营 |
| UC-4 | 投产报表（含周环比） | EP09-S05 | 运营 |

---

## 2. UC-1 工作进度表

```
PR 主管 → GET /api/reports/work-progress?month=2026-05
├─ require_permission(report.work_progress, read)（report.*:read 通配）
├─ 解析 month → [月初, 月末]
├─ WorkProgressRepository.aggregate_by_pr(tenant, date_from, date_to)：
│    GROUP BY pr_id；COUNT/FILTER(URGE_STATUS_SQL_EXPR)/like_sum_expr/
│    FILTER(like_count>=HIT_STAT_THRESHOLD)
├─ service safe_div 组装比率（信息完整度/爆文率/召回完成率/月度完成率/超时率/CPL）
└─ 返回 list[PrWorkProgress]
```

---

## 3. UC-2 爆款约篇

```
PR 主管 → POST /api/reports/targets {pr_id, style_id, period_month, min_target}
├─ require_permission(report.target, write)
├─ 校验 pr/style 存在
├─ ON CONFLICT(tenant,pr_id,style_id,period_month) upsert min_target
└─ 返回 TargetPlanning

→ GET /api/reports/targets?month=2026-05
├─ list target_planning(month) LEFT JOIN promotion 聚合实际约篇数
│    actual = COUNT(promotion WHERE pr_id+style_id+月)
├─ status = 达标(actual>=min) / 未达标；gap = actual - min
└─ 返回 list[TargetWithActual]
```

---

## 4. UC-3 店铺数据看板

```
运营 → GET /api/reports/store-daily?preset=last_30d
├─ resolve_time_range → [from, to]
├─ StoreDailyRepository.aggregate(tenant, from, to)：
│    qianniu_daily GROUP BY date SUM(visitors/pay_amount/pay_orders + extra)
│    LEFT JOIN store_daily(date) 手动字段
└─ 返回 list[StoreDailyRow]

运营 → PUT /api/reports/store-daily/{date} {ad_spend_total, zhitongche_spend, yinli_spend}
├─ require_permission(report.store_daily, write)
├─ ON CONFLICT(tenant,date) upsert 手动字段
└─ 返回 StoreDailyRow
```

---

## 5. UC-4 投产报表（周环比）

```
运营 → GET /api/reports/production?preset=last_7d&exclude_brushing=false
├─ resolve_time_range → current [from, to]
├─ previous = [from - span, from - 1]（等长上期）
├─ ProductionRepository.aggregate_by_style(tenant, period)（current + previous 各一次）：
│    JOIN qianniu_daily(支付/退款/加购 from extra) + ad_daily(cost)
│    + promotion(站外成本/点赞) ALL by style_id, date BETWEEN
│    exclude_brushing=false → 不剔除（V1）；true → 预留 U16
├─ service 逐 style safe_div 5 公式：
│    退货率 / 待确认收货 / 加购成本 / 净投产比 / 单件成交成本
└─ 返回 ProductionReport(items=current, previous=previous)
```

---

## 6. 跨表聚合 SQL 模式（投产报表）

```sql
-- 按 style_id 聚合（current 期；previous 期同 SQL 换日期参数）
SELECT
  s.id AS style_id, s.style_code, s.style_name,
  COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
  COALESCE(SUM((q.extra->>'refund_amount')::numeric), 0) AS refund_amount,
  COALESCE(SUM((q.extra->>'add_cart_count')::int), 0) AS add_cart_count,
  COALESCE(promo.promo_cost, 0) AS promo_cost,
  COALESCE(ad.ad_spend, 0) AS ad_spend
FROM style s
LEFT JOIN platform_product pp ON pp.style_id = s.id
LEFT JOIN qianniu_daily q ON q.platform_product_id = pp.id
     AND q.date BETWEEN :from AND :to
LEFT JOIN (SELECT pp2.style_id, SUM(a.cost) ad_spend FROM ad_daily a
           JOIN platform_product pp2 ON pp2.id=a.platform_product_id
           WHERE a.date BETWEEN :from AND :to GROUP BY pp2.style_id) ad
     ON ad.style_id = s.id
LEFT JOIN (SELECT style_id, SUM(quote_amount) promo_cost FROM promotion
           WHERE cooperation_date BETWEEN :from AND :to AND is_active
           GROUP BY style_id) promo ON promo.style_id = s.id
WHERE s.tenant_id = :tenant_id
GROUP BY s.id, s.style_code, s.style_name, promo.promo_cost, ad.ad_spend
```

> 比率指标在 service 层 safe_div 后处理（不在 SQL 内除，保证分母 0→null 语义统一）。

---

## 7. WorkProgress/Target/StoreDaily/Production Service 接口

```python
class WorkProgressService:
    async def get_for_month(self, tenant_id, month: str) -> list[PrWorkProgress]

class TargetPlanningService:
    async def set_target(self, payload: TargetCreate, user) -> TargetPlanning
    async def list_with_actuals(self, tenant_id, month: str) -> list[TargetWithActual]

class StoreDailyService:
    async def get_dashboard(self, tenant_id, time_range) -> list[StoreDailyRow]
    async def upsert_manual(self, tenant_id, day, payload, user) -> StoreDailyRow

class ProductionService:
    async def get_report(self, tenant_id, time_range, *, exclude_brushing=False) -> ProductionReport
```

---

## 8. API 端点概览

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET | /api/reports/work-progress | report.work_progress:read | 工作进度（按月） |
| POST | /api/reports/targets | report.target:write | 设约篇目标 |
| GET | /api/reports/targets | report.target:read | 约篇达标跟踪 |
| GET | /api/reports/store-daily | report.store_daily:read | 店铺看板 |
| PUT | /api/reports/store-daily/{date} | report.store_daily:write | 手动字段 |
| GET | /api/reports/production | report.production:read | 投产报表+周环比 |

---

## 9. 跨单元契约

| 方向 | 契约 |
|---|---|
| U14 → U04 | promotion 聚合（cooperation_date/quote_amount/like_count/publish_status/URGE_STATUS_SQL_EXPR/like_sum_expr） |
| U14 → U05 | settlement（付款/退款金额，按需） |
| U14 → U13 | qianniu_daily（支付/退款/加购 from extra）+ ad_daily（cost）+ platform_product 关联 |
| U14 → U08 | 复用 resolve_time_range + metric/common.safe_div |
| U16 → U14 | exclude_brushing 启用 + order_adjustment 剔除回归 |
| U17 → U14 | BI 看板 + 导出读 U14 报表 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 4 UC 覆盖 EP09-S02~S05 | ✅ |
| 跨表聚合 SQL + service safe_div 分离 | ✅ §6 |
| 周环比等长上期 | ✅ UC-4 |
| exclude_brushing 占位 | ✅ |
| 4 service 接口 + 6 API 端点 | ✅ §7 §8 |
| 跨单元契约 U04/U05/U13/U08/U16/U17 | ✅ §9 |
