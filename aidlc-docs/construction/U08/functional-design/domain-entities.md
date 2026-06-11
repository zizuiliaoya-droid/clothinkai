# U08 领域实体（发文进度看板）

> 单元：U08 — 发文进度看板（EP09-S01 + S07）
> 依赖：U04（promotion + urge_calculator + metrics_calculator）+ U02（style）
> 特征：**纯读聚合层，无新 ORM 实体 / 无新表 / 无 migration**

---

## 1. 实体总览

U08 不引入持久化实体；只定义**读模型（Pydantic Schema）** + **聚合来源映射**。所有数据实时聚合自
`promotion`（U04）+ `style`（U02），按 `cooperation_date` 落在 TimeRange 内。

| 读模型 | 用途 | 来源 |
|---|---|---|
| TimeRange | 时间筛选解析结果 | EP09-S07 |
| ProgressSummary | Layer 1 全局汇总（9 指标） | promotion GROUP（全租户范围） |
| StyleCard | Layer 2 商品卡片 | promotion GROUP BY style_id + JOIN style |
| PrDetail | Layer 3 Tab1 PR 维度明细 | promotion GROUP BY pr_id（限某 style） |
| TimeSeriesPoint | Layer 3 Tab2 半月周期趋势 | promotion GROUP BY 半月 bucket（限某 style） |

---

## 2. TimeRange（EP09-S07）

| 字段 | 类型 | 说明 |
|---|---|---|
| preset | enum | last_7d / last_30d / this_month / last_month / custom |
| date_from | date | 解析后起始（含） |
| date_to | date | 解析后结束（含） |

解析规则（Asia/Shanghai，基于 `get_today()`，FB8）：
- last_7d：[today-6, today]
- last_30d：[today-29, today]
- this_month：[当月 1 日, today]
- last_month：[上月 1 日, 上月末]
- custom：必须显式 date_from + date_to（date_from ≤ date_to）

聚合字段固定 `cooperation_date`（三层一致）。

---

## 3. ProgressSummary（Layer 1，9 指标）

| 字段 | 口径 | 分母 0 |
|---|---|---|
| quote_count（约篇量） | COUNT(promotion，未删除 is_active=true) | — |
| quote_amount（约篇金额） | SUM(quote_amount) | — |
| cooperation_amount（合作金额） | SUM(quote_amount) WHERE publish_status='已发布' | — |
| publish_count（发布量） | COUNT(publish_status='已发布') | — |
| publish_rate（发布率） | publish_count / quote_count | null |
| overdue_count（超时量） | COUNT(urge_status='超时') | — |
| overdue_rate（超时率） | overdue_count / quote_count | null |
| like_count（点赞量） | SUM(折算后点赞) | — |
| cpl（点赞成本） | cooperation_amount / like_count | null |
| cancel_count（取消量） | COUNT(publish_status='已取消') | — |

各 rate 字段可附 `*_level`（green/yellow/red，BR-U08-30 阈值）。

---

## 4. StyleCard（Layer 2）

| 字段 | 来源 |
|---|---|
| style_id / style_code / style_name | style |
| main_image_id | style.main_image_id（前端取图） |
| cost（成本） | SUM(promotion.cost_snapshot)（该 style 范围内） |
| quote_count / quote_amount | 该 style 聚合 |
| publish_count / cooperation_amount | 该 style 聚合 |
| cancel_count / overdue_count / like_count | 该 style 聚合 |
| cpl | cooperation_amount / like_count（null 安全） |
| publish_rate / overdue_rate | 进度条（前端） |

分页：page / page_size；排序约篇量 desc。

---

## 5. PrDetail（Layer 3 Tab1）

按 `pr_id` GROUP BY（限定某 style_id + TimeRange）：

| 字段 | 来源 |
|---|---|
| pr_id / pr_name | promotion.pr_id JOIN user.display_name/username |
| quote_count / publish_count / overdue_count / like_count | 聚合 |
| publish_rate | publish_count / quote_count（null 安全） |

---

## 6. TimeSeriesPoint（Layer 3 Tab2）

半月周期 bucket（每月 1-15 日 / 16-月末），限定某 style_id：

| 字段 | 来源 |
|---|---|
| period_label | 如 "2026-05 上半月" / "2026-05 下半月" |
| period_start / period_end | bucket 边界 |
| quote_count / publish_count / like_count | 该 bucket 聚合 |

用于折线图。

---

## 7. 聚合来源字段映射

| 看板字段 | promotion 列 | 备注 |
|---|---|---|
| 时间过滤 | cooperation_date | TimeRange 内 |
| 约篇量/金额 | id / quote_amount | is_active=true |
| 发布量/合作金额 | publish_status='已发布' | |
| 取消量 | publish_status='已取消' | |
| 超时量 | URGE_STATUS_SQL_EXPR='超时' | 复用 U04，:today 参数 |
| 点赞量 | like_count + platform 折算 | CASE 抖音/快手 ×0.1 |
| 卡片 style 信息 | JOIN style ON style_id | style_code/name/main_image_id |
| PR 明细 | JOIN user ON pr_id | display_name |

---

## 8. 复用与契约

| 复用 | 来源 | 用途 |
|---|---|---|
| `URGE_STATUS_SQL_EXPR` / `get_today` | U04 urge_calculator | 超时量 + 统一日期 |
| `PLATFORM_LIKE_COEFFICIENT` | U04 legacy_settings | 点赞折算系数 |
| promotion 索引（tenant + cooperation_date 等） | U04 | 聚合性能 |
| style 表 | U02 | 卡片信息 |
| RLS + 多租户 Session | U01 | 隔离 |

### 演化（V1）
- WorkProgressService（EP09-S02）/ TargetPlanningService（EP09-S03）/ ProductionService（EP09-S05）复用 `services/metric/common.safe_div` + TimeRange。
