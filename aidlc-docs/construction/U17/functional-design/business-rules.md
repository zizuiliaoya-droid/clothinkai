# U17 业务规则（套装 + BI 看板 + 报表导出）

> 单元：U17（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 错误码沿用 core/exceptions；导出复用 resolve_time_range

---

## 1. 套装/组合商品（EP02-S08）

- **BR-U17-01** 创建 bundle：bundle_code 租户内唯一（UNIQUE(tenant, bundle_code)）；重复 → 409。
- **BR-U17-02** bundle 必须含 ≥ 1 个 item；每个 item 含 sku_id + quantity（≥ 1）。
- **BR-U17-03** item.sku_id 校验：sku 存在且同租户（RLS + 显式校验）；不存在/跨租户 → 422。
- **BR-U17-04** 同一 bundle 内 sku 不重复（UNIQUE(tenant, bundle_id, sku_id)）；重复 → 合并或 422（V2 拒绝重复）。
- **BR-U17-05** quantity < 1 → 422（CHECK + schema 校验）。
- **BR-U17-06** get_with_items：返回 bundle + items（含 sku 快照 code/名称）。
- **BR-U17-07** 销量拆分：split_quantities(bundle_id, sold_qty) → 每 item 返回 (sku_id, item.quantity × sold_qty)；供报表按 bundle_item 拆分销量到各 sku（EP02-S08 第二条）。

---

## 2. BI 看板（EP09-S06）

- **BR-U17-20** get_dashboard(time_range) 复用已有 report service（WorkProgressService/ProductionService/StoreDailyService），聚合为图表就绪数据集（cards + charts）。
- **BR-U17-21** charts 含 line/bar/pie 组合（趋势/对比/占比）；后端仅产出 labels + series，渲染在前端。
- **BR-U17-22** 时间筛选复用 resolve_time_range（preset + custom，跨度 ≤ 366 天）。
- **BR-U17-23** 布局偏好：save_layout(user, layout) → user_preference upsert（pref_key="bi_layout"）；get_layout → get_or_default（无则默认布局）。
- **BR-U17-24** user_preference 按 user_id 隔离（本人读写）；UNIQUE(tenant, user_id, pref_key) upsert。
- **BR-U17-25** BI 看板只读权限复用 report.*:read 通配（运营已有）。

---

## 3. 报表导出 Excel（EP09-S08）

- **BR-U17-40** export(report_type, time_range, filters)：report_type ∈ {work-progress, production, store-daily}；非法 → 400。
- **BR-U17-41** 复用对应 report service 取数据 → 行序列化（表头含衍生字段，如发布率/退货率/净投产比）。
- **BR-U17-42** openpyxl write_only Workbook 流式写入 BytesIO → StreamingResponse；content-type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet；Content-Disposition attachment + 文件名（report_type + 日期范围）。
- **BR-U17-43** 时间筛选 + filters 透传对应 service（按当前筛选条件导出）。
- **BR-U17-44** 权限：require_permission("report.export", "read")；无权限 → 403（EP09-S08 第二条）。
- **BR-U17-45** 空数据导出仅表头（不报错）；Decimal/None 序列化为字符串/空。

---

## 4. 权限

- **BR-U17-60** product.bundle:read/write → merchandiser（跟单）+ admin。
- **BR-U17-61** report.export:read → pr_manager + operations + admin。
- **BR-U17-62** BI 看板 → report.*:read 通配（无新 scope）。
- **BR-U17-63** user_preference → 本人 user_id 隔离，无需额外 scope。
- migration 021 seed（admin 通配 + 角色显式）。

---

## 5. 错误码矩阵

| 场景 | 异常 | HTTP |
|---|---|---|
| bundle_code 重复 | DuplicateResourceError | 409 |
| bundle item sku 不存在/跨租户 | ValidationError | 422 |
| quantity < 1 | ValidationError | 422 |
| 同 bundle sku 重复 | ValidationError/IntegrityError | 422 |
| 导出 report_type 非法 | ValidationError | 400 |
| 导出无权限 | require_permission | 403 |
| 时间范围非法 | ReportInvalidTimeRangeError | 422 |
