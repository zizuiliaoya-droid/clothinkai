# U14 NFR 设计模式（NFR Design Patterns）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 模式：P-U14-01（工作进度+爆款约篇）、P-U14-02（店铺）、P-U14-03（投产+周环比）

---

## P-U14-01 — 工作进度 + 爆款约篇

### 工作进度（GROUP BY pr + FILTER）

```sql
SELECT
  p.pr_id AS pr_id,
  COALESCE(u.display_name, u.username, '未分配') AS pr_name,
  COUNT(*) AS quote_count,
  COUNT(*) FILTER (WHERE ({URGE}) = '档期内') AS in_schedule_count,
  COUNT(*) FILTER (WHERE ({URGE}) = '催发') AS urge_count,
  COUNT(*) FILTER (WHERE ({URGE}) = '重要催发') AS important_urge_count,
  COUNT(*) FILTER (WHERE ({URGE}) = '超时') AS overdue_count,
  COUNT(*) FILTER (WHERE p.publish_status='已发布') AS publish_count,
  COUNT(*) FILTER (WHERE p.publish_status='已发布' AND p.like_count IS NOT NULL)
    AS info_complete_count,
  COUNT(*) FILTER (WHERE p.publish_status='已取消') AS cancel_count,
  COUNT(*) FILTER (WHERE p.recall_status IN ('召回中','召回成功','召回失败')) AS recall_due_count,
  COUNT(*) FILTER (WHERE p.recall_status='召回成功') AS recall_success_count,
  COUNT(*) FILTER (WHERE p.like_count >= :hit_stat_threshold) AS hit_count,
  {LIKE_SUM} AS like_count,
  COALESCE(SUM(p.cost_snapshot), 0) AS cost
FROM promotion p
LEFT JOIN "user" u ON u.id = p.pr_id
WHERE p.tenant_id = :tenant_id AND p.is_active = true
  AND p.cooperation_date BETWEEN :from AND :to
GROUP BY p.pr_id, u.display_name, u.username
ORDER BY quote_count DESC
```
- service 层 safe_div：info_complete_rate / hit_rate / recall_complete_rate / month_complete_rate / overdue_rate / cpl。

### 爆款约篇（set_target ON CONFLICT + actual 子查询）

```python
async def set_target(self, payload, user):
    stmt = pg_insert(TargetPlanning).values(
        tenant_id=user.tenant_id, pr_id=payload.pr_id, style_id=payload.style_id,
        period_month=payload.period_month, min_target=payload.min_target,
    ).on_conflict_do_update(
        index_elements=["tenant_id", "pr_id", "style_id", "period_month"],
        set_={"min_target": payload.min_target, "updated_at": func.now()},
    ).returning(TargetPlanning)
    ...
```

```sql
-- list_with_actuals(month)
SELECT t.*, COALESCE(u.display_name,u.username) pr_name, s.style_code, s.style_name,
  COALESCE(act.actual, 0) AS actual_count
FROM target_planning t
JOIN style s ON s.id = t.style_id
LEFT JOIN "user" u ON u.id = t.pr_id
LEFT JOIN (SELECT pr_id, style_id, COUNT(*) actual FROM promotion
           WHERE tenant_id=:t AND is_active
             AND to_char(cooperation_date,'YYYY-MM') = :month
           GROUP BY pr_id, style_id) act
  ON act.pr_id=t.pr_id AND act.style_id=t.style_id
WHERE t.tenant_id=:t AND t.period_month=:month
```
- service：status = 达标 if actual>=min else 未达标；gap = actual - min。

---

## P-U14-02 — 店铺数据（聚合 + 手动 upsert）

```sql
-- get_dashboard
SELECT q.date,
  COALESCE(SUM(q.visitors),0) AS visitors,
  COALESCE(SUM(q.pay_amount),0) AS pay_amount,
  COALESCE(SUM(q.pay_orders),0) AS pay_orders,
  sd.ad_spend_total, sd.zhitongche_spend, sd.yinli_spend
FROM qianniu_daily q
LEFT JOIN store_daily sd ON sd.date = q.date AND sd.tenant_id = q.tenant_id
WHERE q.tenant_id = :t AND q.date BETWEEN :from AND :to
GROUP BY q.date, sd.ad_spend_total, sd.zhitongche_spend, sd.yinli_spend
ORDER BY q.date
```

```python
async def upsert_manual(self, tenant_id, day, payload, user):
    stmt = pg_insert(StoreDaily).values(
        tenant_id=tenant_id, date=day,
        ad_spend_total=payload.ad_spend_total,
        zhitongche_spend=payload.zhitongche_spend,
        yinli_spend=payload.yinli_spend, remark=payload.remark,
    ).on_conflict_do_update(
        index_elements=["tenant_id", "date"],
        set_={k: v for k, v in {...}.items() if v is not None} | {"updated_at": func.now()},
    )
    ...
```

---

## P-U14-03 — 投产跨表聚合 + 周环比 + safe_div

### aggregate_by_style（子查询预聚合防笛卡尔积）

```sql
SELECT
  s.id AS style_id, s.style_code, s.style_name,
  COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
  COALESCE(SUM((q.extra->>'refund_amount')::numeric), 0) AS refund_amount,
  COALESCE(SUM((q.extra->>'add_cart_count')::int), 0) AS add_cart_count,
  COALESCE(MAX(promo.promo_cost), 0) AS promo_cost,
  COALESCE(MAX(ad.ad_spend), 0) AS ad_spend
FROM style s
LEFT JOIN platform_product pp ON pp.style_id = s.id
LEFT JOIN qianniu_daily q ON q.platform_product_id = pp.id
     AND q.date BETWEEN :from AND :to
LEFT JOIN (SELECT pp2.style_id, SUM(a.cost) ad_spend FROM ad_daily a
           JOIN platform_product pp2 ON pp2.id = a.platform_product_id
           WHERE a.date BETWEEN :from AND :to GROUP BY pp2.style_id) ad
     ON ad.style_id = s.id
LEFT JOIN (SELECT style_id, SUM(quote_amount) promo_cost FROM promotion
           WHERE cooperation_date BETWEEN :from AND :to AND is_active
           GROUP BY style_id) promo ON promo.style_id = s.id
WHERE s.tenant_id = :tenant_id AND s.is_deleted = false
GROUP BY s.id, s.style_code, s.style_name
-- 注：promo/ad 已预聚合为 style 维度单行，用 MAX 取该单值（避免与 qianniu 多行相乘）
```

### get_report（周环比 + service safe_div）

```python
async def get_report(self, tenant_id, time_range, *, exclude_brushing=False):
    cur_from, cur_to = time_range
    span = (cur_to - cur_from)
    prev_to = cur_from - timedelta(days=1)
    prev_from = prev_to - span
    with report_query_duration_seconds.labels("production").time():
        cur_rows = await self._repo.aggregate_by_style(
            tenant_id, cur_from, cur_to, exclude_brushing=exclude_brushing)
        prev_rows = await self._repo.aggregate_by_style(
            tenant_id, prev_from, prev_to, exclude_brushing=exclude_brushing)
    return ProductionReport(
        items=[self._to_row(r) for r in cur_rows],
        previous=[self._to_row(r) for r in prev_rows],
    )

def _to_row(self, r) -> ProductionRow:
    confirmed = r["pay_amount"] - r["refund_amount"]
    total_spend = r["promo_cost"] + r["ad_spend"]
    return_rate = safe_div(r["refund_amount"], r["pay_amount"], quantize=_Q4)
    add_cart_cost = safe_div(total_spend, r["add_cart_count"], quantize=_Q4)
    net_roi = safe_div(confirmed, total_spend, quantize=_Q4)
    # 单件成交成本 = 加购成本 / 加购转化率 / (1-退货率)（链式 safe_div，缺转化率→null）
    unit_deal_cost = ...  # 缺加购转化率字段 → null（V1 基础口径）
    return ProductionRow(..., return_rate=return_rate, confirmed_amount=confirmed,
                         total_spend=total_spend, add_cart_cost=add_cart_cost,
                         net_roi=net_roi, unit_deal_cost=unit_deal_cost)
```

- **exclude_brushing**：V1 透传不改 SQL（注释 TODO U16 剔除 order_adjustment WHERE exclude_from_roi=true）。

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| 工作进度 FILTER 聚合 + service safe_div | ✅ P-U14-01 |
| 爆款约篇 ON CONFLICT + actual 子查询 + 达标/gap | ✅ P-U14-01 |
| 店铺 qianniu SUM + store_daily 左联 + 手动 upsert | ✅ P-U14-02 |
| 投产子查询预聚合防笛卡尔积 + extra COALESCE | ✅ P-U14-03 |
| 周环比等长上期两次聚合 | ✅ P-U14-03 |
| 5 公式 service safe_div 分母 0→null | ✅ P-U14-03 |
| exclude_brushing V1 占位 | ✅ |
| report_query_duration 埋点 | ✅ |
