# U17 NFR 设计计划（NFR Design Plan）

> 单元：U17 — 套装 + BI 看板 + 报表导出（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 产出：nfr-design-patterns.md（伪代码模式）+ logical-components.md（组件清单 + 依赖图）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：套装创建 + 销量拆分模式？
[Answer] P-U17-01：BundleService.create → bundle_code UNIQUE（IntegrityError→409）+ items 校验（sku 同租户存在/quantity≥1/同 bundle 不重复）+ 落 bundle + N item + 审计 + commit；split_quantities(bundle_id, sold_qty) 纯函数返回 [(sku_id, quantity×sold_qty)]。完整伪代码 + sku 跨租户校验。

### Q2：BI 看板聚合 + 布局模式？
[Answer] P-U17-02：BiService.get_dashboard(tenant, time_range) 串行调 ProductionService/StoreDailyService（+ 可选 WorkProgressService 按月）→ 组装 cards + charts(line/bar/pie)；UserPreferenceService get_or_default/upsert（pref_key=bi_layout，ON CONFLICT(tenant,user,key)）。完整伪代码 + 默认布局常量。

### Q3：报表导出模式？
[Answer] P-U17-03：ReportExportService.export(report_type, time_range, filters) → report_type 映射校验（非法 400）+ _fetch_rows 复用对应 service + openpyxl write_only Workbook → BytesIO → StreamingResponse（xlsx media_type + Content-Disposition）+ report_export_total{type,result}；_cell 序列化 Decimal/None。完整伪代码 + 表头映射。

### Q4：权限注入与 403？
[Answer] P-U17-03 续：export_api require_permission("report.export","read")（无权限 403 由 deps 抛，service 不重复校验）；bundle_api require_permission("product.bundle",...)；bi_api require_permission("report.*","read") 通配；user_preference 本人（CurrentActiveUser.id）。

### Q5：logical-components 组件与依赖？
[Answer] product 新建 5（bundle_models/schemas/repository/service/api）+ report 新建 6（user_preference_models/service + bi_service + export_service + bi_api + export_api）+ 横切 7；依赖图：bundle_api→BundleService→repo→sku(U02)；bi_service/export_service→ProductionService/StoreDailyService(U14)；user_preference_service→repo。无循环（U17→U02/U14→U13/U05→U01）。

### Q6：repository 落点？
[Answer] product/bundle_repository.py（BundleRepository：add/get/get_with_items/list_items/exists_code/sku_exists）+ report/user_preference_service 内联 repo（或 user_preference_models + service 直接用 session）。导出 _fetch_rows 调 report service 不新建 repo。

### Q7：测试设计映射？
[Answer] logical-components 末尾列 3 测试文件 → 组件/规则映射：unit(split_quantities/_cell)+integration(create/跨租户 sku/split/偏好/导出可解析/RLS)+api(401/403/OpenAPI)。

---

## 1. 步骤

- [x] 1.1 阅读 U17 functional-design + nfr-requirements + U14 ProductionService/StoreDailyService 接口 + openpyxl write_only
- [x] 1.2 编写 nfr-design-patterns.md（P-U17-01 bundle create+split_quantities / P-U17-02 BI get_dashboard+布局 upsert / P-U17-03 export openpyxl 流式+report_type 映射+403 完整伪代码）
- [x] 1.3 编写 logical-components.md（11 新建 + 7 横切 + repository + 依赖图无循环 + migration 021 DDL 概要 + 3 测试文件映射）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。**
