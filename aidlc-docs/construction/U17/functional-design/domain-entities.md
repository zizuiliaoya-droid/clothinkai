# U17 领域实体（套装 + BI 看板 + 报表导出）

> 单元：U17（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> 模块归属：套装 → modules/product；BI + 导出 → modules/report
> 依赖：U02（product/sku）、U14（report 各报表 service）

---

## 1. 实体总览

| 实体 | 表名 | 用途 | 关键约束 |
|---|---|---|---|
| BundleProduct | `bundle_product` | 套装/组合商品 | UNIQUE(tenant_id, bundle_code) |
| BundleItem | `bundle_item` | 套装组合明细（sku × 数量） | FK bundle CASCADE / sku RESTRICT |
| UserPreference | `user_preference` | 用户偏好（BI 布局等） | UNIQUE(tenant_id, user_id, pref_key) |

3 表均 TenantScopedModel + RLS。

---

## 2. BundleProduct（套装/组合商品）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| bundle_code | String(64) | NOT NULL | 套装编码 |
| bundle_name | String(255) | NOT NULL | 套装名称 |
| remark | Text | NULL | 备注 |
| is_active | Boolean | NOT NULL DEFAULT true | 启用 |

索引：`uq_bundle_product_code` UNIQUE(tenant_id, bundle_code)。

---

## 3. BundleItem（组合明细）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| bundle_id | UUID FK bundle_product CASCADE | NOT NULL | 所属套装 |
| sku_id | UUID FK sku RESTRICT | NOT NULL | 组合 SKU |
| quantity | Integer | NOT NULL, CHECK ≥ 1 | 数量 |

索引：`idx_bundle_item_bundle` (tenant_id, bundle_id) + `uq_bundle_item_sku` UNIQUE(tenant_id, bundle_id, sku_id)（同 sku 不重复）。

### 销量拆分口径（EP02-S08 第二条）
- `BundleService.split_quantities(bundle_id, sold_qty)` → `[(sku_id, item.quantity × sold_qty)]`。
- 供投产报表把套装销量按 bundle_item 数量拆分到各 sku。
- V2 提供拆分 helper + 单测验证；投产报表实际接入为口径扩展点（qianniu_daily 按 platform_product/style 聚合，bundle 拆分作薄覆盖，文档标注）。

---

## 4. UserPreference（用户偏好）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| user_id | UUID FK user CASCADE | NOT NULL | 所属用户 |
| pref_key | String(64) | NOT NULL | 偏好键（如 bi_layout） |
| pref_value | JSONB | NOT NULL DEFAULT '{}' | 偏好值 |

索引：`uq_user_preference` UNIQUE(tenant_id, user_id, pref_key)。

- BI 布局（EP09-S06）：pref_key="bi_layout"，pref_value=布局 JSON。
- get_or_default(user_id, key, default) + upsert(user_id, key, value)。

---

## 5. BI 看板数据集（EP09-S06）

`BiService.get_dashboard(time_range)` 返回（复用已有 report service）：
```
{
  "cards": [ {key, label, value} ... ],          # 卡片：约篇量/发布量/支付额/净投产比 等
  "charts": [
    {type: "line",  title, labels: [...], series: [{name, data}]},   # 趋势折线
    {type: "bar",   title, labels: [...], series: [...]},            # 款式对比柱状
    {type: "pie",   title, labels: [...], series: [...]}             # 占比饼图
  ]
}
```
- 数据来源：WorkProgressService（PR 维度）/ ProductionService（款式 ROI）/ StoreDailyService（店铺日趋势）。
- 后端仅产出图表就绪数据集；图表渲染在前端。

---

## 6. 报表导出 report_type 映射（EP09-S08）

| report_type | 数据来源 service | 表头（含衍生字段） |
|---|---|---|
| work-progress | WorkProgressService.get_for_month | PR/约篇量/发布量/发布率/超时率/爆文数/点赞/成本/CPL |
| production | ProductionService.get_report | 款号/支付额/退款额/退货率/净投产比/加购成本/总花费 |
| store-daily | StoreDailyService.get_dashboard | 日期/访客/支付额/支付订单/广告花费 |

- `ReportExportService.export(report_type, time_range, filters)` → openpyxl write_only Workbook → BytesIO → StreamingResponse。
- 非法 report_type → 400；无 report.export 权限 → 403。

---

## 7. 组件清单（新建 / 修改）

### 新建
| 文件 | 模块 | 职责 |
|---|---|---|
| `product/bundle_models.py` | product | BundleProduct + BundleItem ORM |
| `product/bundle_schemas.py` | product | BundleCreate / BundleItemIn / BundleResponse |
| `product/bundle_repository.py` | product | bundle CRUD + items |
| `product/bundle_service.py` | product | create / get_with_items / split_quantities |
| `product/bundle_api.py` | product | /api/bundles |
| `report/bi_service.py` | report | get_dashboard（复用 report service 聚合） |
| `report/export_service.py` | report | export（openpyxl 流式） |
| `report/user_preference_models.py` | report | UserPreference ORM |
| `report/user_preference_service.py` | report | get_or_default / upsert |
| `report/bi_api.py` | report | /api/reports/bi + /api/reports/bi/layout |
| `report/export_api.py` | report | /api/reports/{type}/export |

### 修改（横切）
| 文件 | 改动 |
|---|---|
| `product/permissions.py` | +product.bundle:read/write |
| `product/deps.py` | +BundleServiceDep |
| `report/advanced_permissions.py` | +report.export:read |
| `report/deps.py` | +BiServiceDep / ExportServiceDep / UserPreferenceServiceDep |
| `main.py` | 挂 bundle_router + bi_router + export_router |
| `tests/conftest.py` | 追加 bundle_models + user_preference_models import |
| `alembic/versions/021_*.py` | 3 表 + scope seed |

---

## 8. ER 关系

```
bundle_product 1──* bundle_item ──* sku(U02)
user(U01) 1──* user_preference
report service(U14) ──reuse──> BiService / ExportService
BundleService.split_quantities ──口径扩展──> 投产报表（薄覆盖）
```

---

## 9. 演化说明
- 套装销量拆分 V2 提供 helper；投产报表深度接入（按 bundle 销量回填各 sku）后续可增强。
- BI 看板后端产出数据集，图表交互/布局编辑在前端；布局持久化在 user_preference。
- 报表导出 V2 支持 3 类报表；新增报表类型按 report_type 映射扩展。
