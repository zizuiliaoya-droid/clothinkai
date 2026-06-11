# U17 功能设计计划（Functional Design Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2，收官单元）
> 依赖：U02（product/sku）、U14（report 各报表 service）
> 复用：modules/product（bundle）+ modules/report（bi/export）+ openpyxl（已有）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：套装/BI/导出落在哪些模块、几张表？
[Answer] 套装：modules/product 追加 bundle_models（BundleProduct + BundleItem）+ bundle_schemas/service/api。BI + 导出：modules/report 追加 bi_service + export_service + bi_api/export_api。migration 021：bundle_product + bundle_item + user_preference 3 表 + product.bundle/report.export scope seed。

### Q2：bundle 表结构？
[Answer] BundleProduct：bundle_code（UNIQUE tenant）/ bundle_name / remark / is_active。BundleItem：bundle_id(FK CASCADE) / sku_id(FK RESTRICT) / quantity(>=1)。一 bundle 多 item。

### Q3：EP02-S08 销量拆分口径？
[Answer] BundleService.split_quantities(bundle_id, sold_qty) → 返回 [(sku_id, qty=item.quantity × sold_qty)]，供报表把套装销量按 bundle_item 数量拆分到各 sku。V2 提供拆分 helper + 单测验证；投产报表实际接入为薄覆盖（文档说明，qianniu_daily 按 platform_product/style 聚合，bundle 拆分作为口径扩展点）。

### Q4：EP09-S06 BI 看板后端职责？
[Answer] BI 主要为前端图表组合（卡片 + 折线/柱状/饼图）。后端 BiService.get_dashboard(time_range) 复用已有 report service（WorkProgressService/ProductionService/StoreDailyService）聚合为图表就绪数据集（series/labels）。布局偏好存 user_preference（JSONB），UserPreferenceService get/save（按 user + key）。

### Q5：user_preference 表？
[Answer] user_preference：user_id(FK CASCADE) / pref_key(String) / pref_value(JSONB) ；UNIQUE(tenant, user_id, pref_key)。BI 布局 key="bi_layout"。get_or_default + upsert。

### Q6：EP09-S08 报表导出实现？
[Answer] ReportExportService.export(report_type, time_range, filters) → openpyxl 内存生成 xlsx → StreamingResponse（content-type application/vnd.openxmlformats）+ Content-Disposition。report_type ∈ {work-progress / production / store-daily}，复用对应 service 取数据 → 行序列化（含衍生字段表头）。无导出权限 403（require_permission report.export）。

### Q7：导出权限与时间筛选？
[Answer] report.export:read scope（PR 主管 + 运营 + admin）。时间筛选复用 resolve_time_range（preset + custom）；filters 透传对应 service。

### Q8：权限 scope？
[Answer] product.bundle:read/write（跟单 merchandiser + admin）；report.export:read（pr_manager + operations + admin）；BI 看板复用 report.*:read 通配；user_preference 本人读写（无需额外 scope，按 user_id 隔离）。migration 021 seed。

### Q9：导出库与流式？
[Answer] 复用 openpyxl==3.1.5（已装，U06a 用）。write_only Workbook 流式写入 BytesIO，避免大报表内存峰值；StreamingResponse 返回。

### Q10：错误码？
[Answer] bundle_code 重复 409 / bundle_item sku 不存在或跨租户 422 / quantity < 1 422 / 导出 report_type 非法 400 / 导出无权限 403 / 时间范围非法 422（复用 resolve_time_range）。

---

## 1. 步骤

- [x] 1.1 阅读 EP02-S08/EP09-S06/EP09-S08 GWT + 开发文档（套装/BI/导出）+ 已有 product/report service + openpyxl 用法
- [x] 1.2 编写 domain-entities.md（BundleProduct/BundleItem/UserPreference 3 表 + 销量拆分口径 + BI 数据集结构 + 导出 report_type 映射 + ER）
- [x] 1.3 编写 business-rules.md（BR-U17-01~ bundle 唯一/item 校验/销量拆分/BI 聚合复用+布局偏好/导出 report_type+权限+流式/错误码）
- [x] 1.4 编写 business-logic-model.md（3 UC：套装创建+销量拆分 / BI 看板聚合+布局 / 报表导出 Excel + 跨单元契约 U02/U14）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
