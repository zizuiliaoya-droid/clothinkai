# U17 业务逻辑模型（套装 + BI 看板 + 报表导出）

> 单元：U17（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 3 个核心用例 + 跨单元契约（U02 product/sku / U14 report service）

---

## UC-1：套装创建 + 销量拆分（EP02-S08）

```
[跟单创建套装]
  POST /api/bundles  (require product.bundle:write)
    payload: bundle_code / bundle_name / items[{sku_id, quantity}]
    BundleService.create：
      1. bundle_code 唯一校验（IntegrityError → 409 BR-U17-01）
      2. items 非空 + 每 item sku 存在同租户（不存在/跨租户 422 BR-U17-03）
      3. quantity ≥ 1（422 BR-U17-05）；同 bundle sku 不重复（UNIQUE BR-U17-04）
      4. 落 bundle_product + bundle_item × N + 审计 + commit
    → BundleResponse（含 items）

[销量拆分（供报表）]
  BundleService.split_quantities(bundle_id, sold_qty):
    items = repo.list_items(bundle_id)
    return [(it.sku_id, it.quantity * sold_qty) for it in items]   # BR-U17-07
  # 例：bundle = A×1 + B×2，卖 3 件 → [(A,3),(B,6)]
```

**关键点**：拆分 helper 纯函数式，供投产报表把套装销量按 item 数量拆分到各 sku（V2 口径扩展点）。

---

## UC-2：BI 看板聚合 + 布局（EP09-S06）

```
[运营查看 BI 看板]
  GET /api/reports/bi?preset=last_30d  (require report.*:read)
    BiService.get_dashboard(time_range)：
      tr = resolve_time_range(preset, from, to)        # 复用 BR-U17-22
      wp = WorkProgressService.get_for_month(...)       # PR 维度
      prod = ProductionService.get_report(tr)           # 款式 ROI
      store = StoreDailyService.get_dashboard(tr)        # 店铺日趋势
      → 组装 cards（约篇量/发布量/支付额/净投产比）+ charts（line/bar/pie）
    → {cards, charts}

[保存布局]
  PUT /api/reports/bi/layout  (本人)
    UserPreferenceService.upsert(user, "bi_layout", layout_json)  # BR-U17-23/24
  GET /api/reports/bi/layout
    → get_or_default(user, "bi_layout", DEFAULT_LAYOUT)
```

---

## UC-3：报表导出 Excel（EP09-S08）

```
[导出报表]
  GET /api/reports/{report_type}/export?preset=last_30d  (require report.export:read)
    if report_type not in {work-progress, production, store-daily}: 400  # BR-U17-40
    ReportExportService.export(report_type, time_range, filters)：
      tr = resolve_time_range(...)
      rows = 对应 service 取数据（work-progress/production/store-daily）
      wb = openpyxl write_only Workbook
      ws.append(表头含衍生字段)                          # BR-U17-41
      for row in rows: ws.append(序列化值)              # Decimal/None → str/空
      wb.save(BytesIO) → StreamingResponse(
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{report_type}_{范围}.xlsx"'})
    无权限 → 403（require_permission BR-U17-44）
```

---

## 4. 跨单元契约

| 来源单元 | 契约 | U17 用法 |
|---|---|---|
| U02 product | Sku 模型 + product 模块落点 | bundle_item.sku_id FK + bundle 落 modules/product |
| U14 report | WorkProgressService / ProductionService / StoreDailyService + resolve_time_range | BI 聚合 + 导出取数复用 |
| U01 core | TenantScopedModel / AuditService / require_permission | RLS + 审计 + 权限 |
| openpyxl（U06a 已装） | write_only Workbook | 导出流式生成 |

---

## 5. 故事覆盖

| 故事 | 覆盖 |
|---|---|
| EP02-S08 套装/组合商品 | UC-1（bundle 创建 + item 校验 + split_quantities 销量拆分） |
| EP09-S06 BI 看板 | UC-2（图表数据集复用 report service + 布局 user_preference） |
| EP09-S08 报表导出 Excel | UC-3（openpyxl 流式导出 + report_type 映射 + 403） |

---

## 6. 一致性

- 数据模型与开发文档 bundle_product/bundle_item 字段表一致。
- BI/导出复用 U14 report service，不重复实现聚合。
- 复用 openpyxl（U06a 既有依赖），无新增依赖。
- 依赖无循环（U17 → U02 / U14 → U13/U05 → U01）。
