# U08 业务规则（发文进度看板）

> 单元：U08 — 发文进度看板（EP09-S01 + S07）
> 编号：BR-U08-NN

---

## 1. 时间筛选（EP09-S07）

- **BR-U08-01**：TimeRange preset ∈ {last_7d, last_30d, this_month, last_month, custom}；非法 preset → 422。
- **BR-U08-02**：解析基于 `get_today()`（Asia/Shanghai，FB8），含端点 `[date_from, date_to]`：
  - last_7d=[today-6, today]；last_30d=[today-29, today]；this_month=[月1日, today]；last_month=[上月1日, 上月末]。
- **BR-U08-03**：custom 必须同时提供 date_from + date_to，且 date_from ≤ date_to，否则 422；范围跨度上限 366 天（防全表扫描）。
- **BR-U08-04**：三层数据统一按 `cooperation_date ∈ [date_from, date_to]` 过滤聚合。
- **BR-U08-05**：API 在响应/URL 透传 time 参数（前端可分享）。

## 2. 全局汇总（Layer 1，EP09-S01）

- **BR-U08-10**：仅统计 `is_active=true`（未软停用）的 promotion。
- **BR-U08-11**：约篇量=COUNT；约篇金额=SUM(quote_amount)；合作金额=SUM(quote_amount WHERE publish_status='已发布')。
- **BR-U08-12**：发布量=COUNT(publish_status='已发布')；取消量=COUNT(publish_status='已取消')；超时量=COUNT(urge_status='超时')。
- **BR-U08-13**：发布率=发布量/约篇量；超时率=超时量/约篇量；分母为 0 → null（前端"—"）。
- **BR-U08-14**：点赞量=SUM(折算后点赞)；点赞成本(CPL)=合作金额/点赞量；点赞量为 0 → CPL=null。

## 3. 点赞折算

- **BR-U08-20**：折算系数复用 U04 `PLATFORM_LIKE_COEFFICIENT`（抖音/快手 ×0.1，其余 ×1.0）；SQL `CASE WHEN platform IN (<系数<1 平台>) THEN like_count*<系数> ELSE like_count`，`like_count` 为 NULL 视作 0 不计入。
- **BR-U08-21**：折算口径与 `metrics_calculator.calculate_effective_like_count` 一致（聚合层用 SQL 等价表达）。

## 4. 商品卡片（Layer 2）

- **BR-U08-30**：按 style_id GROUP BY + JOIN style 取 style_code/style_name/main_image_id；每卡片含约篇/发布/取消/超时/点赞/合作金额/成本(SUM cost_snapshot)/CPL。
- **BR-U08-31**：分页 page(≥1)/page_size(1..100)；默认按约篇量 desc。
- **BR-U08-32**：发布率/超时率 level 着色（绿/黄/红）：发布率≥0.8 绿 / ≥0.5 黄 / 否则红；超时率≤0.1 绿 / ≤0.3 黄 / 否则红；分母 0 时 level=null。

## 5. 详情抽屉（Layer 3）

- **BR-U08-40**：get_detail_by_pr(style_id, time_range)：限定 style_id + TimeRange，按 pr_id GROUP BY，JOIN user 取 display_name（无则 username）；pr_id 为 NULL 归入"未分配"。
- **BR-U08-41**：get_detail_by_time(style_id, time_range)：半月 bucket（每月 1-15 / 16-末），返回时间序列点（约篇/发布/点赞），按时间升序。

## 6. 权限与多租户

- **BR-U08-50**：所有接口 `report.publish_progress:read`（pr 直含；pr_manager/operations 通过 report.*:read 通配覆盖）。
- **BR-U08-51**：RLS + ORM 钩子按 tenant_id 隔离；只读，无写/删除端点。
- **BR-U08-52**：聚合 SQL 不跨租户（依赖 Session 注入 tenant_id；report 不使用 bypass）。

## 7. 错误处理

- **BR-U08-60**：分母为 0 的派生指标一律 null（不抛异常）；前端展示"—"。
- **BR-U08-61**：无数据时返回零值汇总（count=0，金额=0，rate=null），不报错。

---

## 8. 错误码矩阵

| 场景 | HTTP | 错误码 |
|---|---|---|
| 非法 preset | 422 | REPORT_INVALID_TIME_PRESET |
| custom 缺 date / from>to / 跨度超限 | 422 | REPORT_INVALID_TIME_RANGE |
| style_id 不存在（详情） | 404 | REPORT_STYLE_NOT_FOUND |

---

## 9. 性能

- summary + cards 各一条 GROUP BY SQL（命中 promotion (tenant, cooperation_date) 相关索引）；万级 P95 ≤ 500ms。
- 详情按 style_id 限定，数据量小。
- MVP 实时聚合，不预聚合（V1 评估物化视图）。
