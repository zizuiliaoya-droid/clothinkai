# U08 API 端点（发文进度看板）

> 单元：U08 — 发文进度看板
> 全部 GET + 只读 + `report.publish_progress:read`

---

## 1. 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/reports/publish-progress/summary | Layer 1 全局汇总（9 指标 + 着色 level） |
| GET | /api/reports/publish-progress/cards | Layer 2 商品卡片（GROUP BY style，分页） |
| GET | /api/reports/publish-progress/styles/{style_id}/by-pr | Layer 3 Tab1 PR 维度明细 |
| GET | /api/reports/publish-progress/styles/{style_id}/by-time | Layer 3 Tab2 半月周期趋势 |

---

## 2. 公共查询参数（时间筛选 EP09-S07）

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| preset | str | last_30d | last_7d / last_30d / this_month / last_month / custom |
| date_from | date? | — | custom 必填（含端点） |
| date_to | date? | — | custom 必填（含端点；跨度 ≤ 366 天） |

cards 额外：`page`（≥1，默认 1）+ `page_size`（1..100，默认 20）。

---

## 3. 响应模型

### summary → ProgressSummary
```json
{
  "quote_count": 4, "quote_amount": "3800.00",
  "cooperation_amount": "3000.00",
  "publish_count": 2, "publish_rate": "0.5000", "publish_rate_level": "yellow",
  "overdue_count": 1, "overdue_rate": "0.2500", "overdue_rate_level": "yellow",
  "like_count": 510, "cpl": "5.8824", "cancel_count": 1
}
```
> 分母为 0 → rate / cpl = null，level = null（前端"—"）。

### cards → StyleCardPage
`{ items: [StyleCard], total, page, page_size }`；StyleCard 含 style_code/style_name/main_image_key/cost/各计数+金额/cpl/publish_rate/overdue_rate。

### by-pr → list[PrDetail]
`{ pr_id, pr_name, quote_count, publish_count, overdue_count, like_count, publish_rate }`（pr_id 为 null 归"未分配"）。

### by-time → list[TimeSeriesPoint]
`{ period_label（如 "2026-06 上半月"）, quote_count, publish_count, like_count }`（按时间升序）。

---

## 4. 错误码

| 场景 | HTTP | 错误码 |
|---|---|---|
| 非法 preset | 422 | REPORT_INVALID_TIME_PRESET |
| custom 缺 date / from>to / 跨度超限 | 422 | REPORT_INVALID_TIME_RANGE |
| style_id 不存在（详情） | 404 | REPORT_STYLE_NOT_FOUND |
| 无权限 | 403 | PERMISSION_DENIED |
| 未登录 | 401 | TOKEN_INVALID |

---

## 5. 权限

`report.publish_progress:read` —— pr 直含；pr_manager / operations 通过 report.*:read 通配覆盖（U04/U07 已 seed，U08 不改 default_roles）。
