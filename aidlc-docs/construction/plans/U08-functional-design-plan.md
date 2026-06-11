# U08 功能设计计划（Functional Design Plan）

> 单元：U08 — 发文进度看板（MVP 最后一个单元）
> 覆盖故事：EP09-S01（发文进度三层看板）+ EP09-S07（时间筛选组件）
> 依赖：U04（promotion + urge_calculator + metrics_calculator）+ U02（style 图片/品名/款号/成本）；复用 U01（RLS / 权限 / 多租户）
> 节奏：Functional Design 阶段 = 本计划 + 3 份功能设计文档（同一轮生成）

---

## 1. 单元上下文

### 1.1 范围（仅 MVP 两故事）
- **EP09-S01**：三层看板 = 全局汇总（Layer 1）+ 商品卡片（Layer 2）+ 详情抽屉（Layer 3：PR 维度明细 + 半月周期趋势）。
- **EP09-S07**：统一时间筛选（近7天/近30天/本月/上月/自定义），按 `cooperation_date` 聚合。

### 1.2 不在本单元（划归 V1/V2）
- EP09-S02 工作进度表 / S03 爆款约篇 / S04 店铺看板 / S05 投产报表（V1）；S06 BI / S08 导出（V2）。
- 本单元只建 `modules/report` 的 PublishProgressService + `services/metric/publish_progress.py`（+ common.py）。

### 1.3 关键特征
- **纯读聚合层**：无新表、无 migration、无写操作、不触发事件。聚合 promotion（U04）+ style（U02）。
- 分母为 0 → 返回 `null`（前端展示"—"，BR）。
- 统一日期入口 `get_today()`（复用 U04，Asia/Shanghai，FB8）。

---

## 2. 澄清问题（已预填 [Answer]）

### Q1 — 模块落点
- [Answer] 新建 `modules/report/`（schemas / repository / service(PublishProgressService) / deps / api）+ `services/metric/publish_progress.py` + `services/metric/common.py`（safe_div）。无新表/migration。

### Q2 — TimeRange 解析（EP09-S07）
- [Answer] 枚举 `last_7d / last_30d / this_month / last_month / custom`；解析为 `[date_from, date_to]`（含端点，Asia/Shanghai 本地日期，基于 `get_today()`）。custom 必须带 date_from+date_to。聚合字段 = `cooperation_date`（BR：三层都按 cooperation_date）。

### Q3 — Layer 1 全局汇总指标
- [Answer] 9 指标：约篇量（COUNT 未删除）/约篇金额（SUM quote_amount）/合作金额（SUM quote_amount WHERE 已发布）/发布量（COUNT 已发布）/发布率（发布量/约篇量）/超时量（COUNT urge_status=超时）/超时率（超时量/约篇量）/点赞量（SUM 折算后点赞）/点赞成本（合作金额/点赞量）/取消量（COUNT 已取消）。分母 0 → null。

### Q4 — 点赞折算
- [Answer] 复用 U04 `PLATFORM_LIKE_COEFFICIENT`（抖音/快手 ÷10，其余 ×1）；SQL 内 `CASE WHEN platform IN ('抖音','快手') THEN like_count*0.1 ELSE like_count` 聚合（与 metrics_calculator 一致，系数来源 legacy_settings 常量注入）。

### Q5 — Layer 2 商品卡片
- [Answer] 按 `style_id` GROUP BY，每卡片：style 图片(main_image_id)/品名(style_name)/颜色(聚合 sku color 或留空 MVP)/款号(style_code)/成本(style 维度成本快照或 SUM cost_snapshot)/约篇量&金额/发布量&合作金额/取消量/点赞量/超时量/点赞成本 + 发布率&超时率（前端进度条）。分页（page/page_size，按约篇量 desc）。

### Q6 — Layer 3 详情抽屉
- [Answer] 两接口：`get_detail_by_pr(style_id, time_range)`（按 pr_id GROUP BY，返回每 PR 的约篇/发布/超时/点赞）；`get_detail_by_time(style_id, time_range)`（半月周期 bucket：每月 1-15 / 16-末，返回时间序列点用于折线图）。

### Q7 — 颜色阈值（绿/黄/红）
- [Answer] 后端返回原始指标值 + 可选 `*_level`（green/yellow/red）由阈值判定（发布率高=绿、超时率高=红）；MVP 阈值硬编码（发布率≥0.8 绿/≥0.5 黄/否则红；超时率≤0.1 绿/≤0.3 黄/否则红），前端也可自行着色。返回值为主，level 为辅。

### Q8 — 权限
- [Answer] `report.publish_progress:read`（pr 已有；pr_manager/operations 含 report.*:read 通配覆盖）；多租户 RLS 自动隔离。只读，无写权限。

### Q9 — 性能
- [Answer] 单租户万级 promotion；summary + cards 用单条 GROUP BY SQL（命中 promotion 现有索引）；P95 ≤ 500ms。不预聚合（MVP 实时算，V1 评估物化）。

---

## 3. 执行步骤

- [x] 3.1 `U08/functional-design/domain-entities.md`：无新实体（读模型）+ TimeRange/ProgressSummary/StyleCard/PrDetail/TimeSeriesPoint 读模型定义 + 聚合来源（promotion/style 字段映射）
- [x] 3.2 `U08/functional-design/business-rules.md`：BR-U08-NN（TimeRange 解析 / 9 汇总指标口径 / 折算 / 卡片聚合 / 详情双维度 / 分母 0→null / 阈值着色 / 权限多租户）+ 错误码
- [x] 3.3 `U08/functional-design/business-logic-model.md`：4 UC（summary / cards / detail_by_pr / detail_by_time）+ TimeRange 解析流程 + 与 U04/U02 契约
- [x] 3.4 诊断器无警告 + 故事 EP09-S01/S07 100% 覆盖

---

## 4. 故事追溯矩阵

| 故事 | 设计落点 |
|---|---|
| EP09-S01 三层看板 | PublishProgressService.get_summary/get_cards/get_detail_by_pr/get_detail_by_time |
| EP09-S07 时间筛选 | TimeRange 枚举 + resolve_range + 全接口透传 |

---

**等待用户回复"继续"；本轮直接生成 3 份功能设计文档。**
