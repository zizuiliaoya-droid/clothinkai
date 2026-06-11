# U08 业务逻辑模型（发文进度看板）

> 单元：U08 — 发文进度看板（EP09-S01 + S07）
> 4 个用例 + TimeRange 解析 + 跨单元契约

---

## TimeRange 解析（EP09-S07，所有 UC 前置）

```
resolve_time_range(preset, date_from?, date_to?) -> TimeRange
  today = get_today()                          # Asia/Shanghai（FB8）
  match preset:
    last_7d     -> [today-6, today]
    last_30d    -> [today-29, today]
    this_month  -> [today.replace(day=1), today]
    last_month  -> 上月1日 .. 上月末
    custom      -> 需 date_from+date_to；from<=to；跨度<=366 else 422
    其他        -> 422 REPORT_INVALID_TIME_PRESET
```

---

## UC-1 全局汇总（Layer 1）

```
[PR/PR主管/运营] GET /api/reports/publish-progress/summary?preset=&date_from=&date_to=
  → @require_permission("report.publish_progress:read")
  → tr = resolve_time_range(...)
  → PublishProgressService.get_summary(tr)
        └── repo.aggregate_summary(tr, today)   # 单条 GROUP（无 GROUP BY，全范围聚合）
              SELECT
                COUNT(*) FILTER (WHERE is_active)                      AS quote_count,
                SUM(quote_amount)                                      AS quote_amount,
                SUM(quote_amount) FILTER (publish_status='已发布')      AS cooperation_amount,
                COUNT(*) FILTER (publish_status='已发布')               AS publish_count,
                COUNT(*) FILTER (publish_status='已取消')               AS cancel_count,
                COUNT(*) FILTER ((URGE_EXPR)='超时')                    AS overdue_count,
                SUM(CASE WHEN platform IN ('抖音','快手')
                         THEN like_count*0.1 ELSE like_count END)      AS like_count
              FROM promotion
              WHERE tenant_id=:tid AND is_active
                AND cooperation_date BETWEEN :from AND :to
        └── publish_rate = safe_div(publish_count, quote_count)        # null 安全
        └── overdue_rate = safe_div(overdue_count, quote_count)
        └── cpl          = safe_div(cooperation_amount, like_count)
        └── *_level（绿/黄/红，BR-U08-32）
  → 200 ProgressSummary
```

---

## UC-2 商品卡片（Layer 2）

```
[*] GET /api/reports/publish-progress/cards?preset=&...&page=&page_size=
  → tr = resolve_time_range(...)
  → PublishProgressService.get_cards(tr, page, page_size)
        └── repo.aggregate_cards(tr, today, page, page_size)
              SELECT style_id, s.style_code, s.style_name, s.main_image_id,
                     COUNT(*) AS quote_count, SUM(quote_amount) AS quote_amount,
                     SUM(cost_snapshot) AS cost,
                     COUNT(*) FILTER(已发布) AS publish_count,
                     SUM(quote_amount) FILTER(已发布) AS cooperation_amount,
                     COUNT(*) FILTER(已取消) AS cancel_count,
                     COUNT(*) FILTER(超时) AS overdue_count,
                     SUM(折算点赞) AS like_count
              FROM promotion p JOIN style s ON s.id=p.style_id
              WHERE p.tenant_id=:tid AND p.is_active
                AND p.cooperation_date BETWEEN :from AND :to
              GROUP BY style_id, s.style_code, s.style_name, s.main_image_id
              ORDER BY quote_count DESC LIMIT :ps OFFSET :off
        └── 每卡 cpl/publish_rate/overdue_rate = safe_div(...)
  → 200 Page[StyleCard]（含 total）
```

---

## UC-3 详情：PR 维度明细（Layer 3 Tab1）

```
[*] GET /api/reports/publish-progress/styles/{style_id}/by-pr?preset=&...
  → tr = resolve_time_range(...)
  → PublishProgressService.get_detail_by_pr(style_id, tr)
        └── repo.aggregate_by_pr(style_id, tr, today)
              SELECT p.pr_id, COALESCE(u.display_name, u.username, '未分配') AS pr_name,
                     COUNT(*) AS quote_count, COUNT(*) FILTER(已发布) AS publish_count,
                     COUNT(*) FILTER(超时) AS overdue_count, SUM(折算点赞) AS like_count
              FROM promotion p LEFT JOIN "user" u ON u.id=p.pr_id
              WHERE p.tenant_id=:tid AND p.style_id=:sid AND p.is_active
                AND p.cooperation_date BETWEEN :from AND :to
              GROUP BY p.pr_id, u.display_name, u.username
        └── publish_rate = safe_div(...)
  → 200 list[PrDetail]
```

---

## UC-4 详情：半月周期趋势（Layer 3 Tab2）

```
[*] GET /api/reports/publish-progress/styles/{style_id}/by-time?preset=&...
  → tr = resolve_time_range(...)
  → PublishProgressService.get_detail_by_time(style_id, tr)
        └── repo.aggregate_by_half_month(style_id, tr, today)
              # bucket = year-month + (day<=15 ? '上半月' : '下半月')
              GROUP BY bucket ORDER BY period_start ASC
        └── 返回 list[TimeSeriesPoint]（period_label/start/end + quote/publish/like）
  → 200 list[TimeSeriesPoint]（折线图）
```

---

## 跨单元契约

| 契约 | 提供方 | 使用 |
|---|---|---|
| `URGE_STATUS_SQL_EXPR` / `get_today` | U04 urge_calculator | 超时量 + 日期 |
| `PLATFORM_LIKE_COEFFICIENT` | U04 legacy_settings | 折算系数（SQL CASE 注入） |
| `safe_div` | U08 新建 `services/metric/common.py` | 全派生指标 null 安全（V1 报表复用） |
| promotion + style 表 | U04 + U02 | 聚合来源 |
| RLS + 多租户 | U01 | 隔离 |

---

## 故事覆盖校验

| 故事 | UC | 状态 |
|---|---|---|
| EP09-S01 Layer 1 全局汇总 | UC-1 | ✅ |
| EP09-S01 Layer 2 商品卡片 | UC-2 | ✅ |
| EP09-S01 Layer 3 PR 明细 | UC-3 | ✅ |
| EP09-S01 Layer 3 半月趋势 | UC-4 | ✅ |
| EP09-S01 分母 0→"—" | safe_div 全程 | ✅ |
| EP09-S07 时间筛选 5 选项 | resolve_time_range | ✅ |
