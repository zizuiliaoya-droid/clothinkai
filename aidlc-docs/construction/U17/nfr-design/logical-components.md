# U17 逻辑组件（套装 + BI 看板 + 报表导出）

> 单元：U17（EP02-S08、EP09-S06、EP09-S08）（V2 收官单元）
> product 新建 5 + report 新建 6 + 横切 7；无循环依赖。

---

## 1. 新建组件（11）

### modules/product（5，套装）
| 组件 | 类型 | 职责 |
|---|---|---|
| `bundle_models.py` | ORM | BundleProduct + BundleItem（TenantScopedModel + RLS） |
| `bundle_schemas.py` | Pydantic | BundleCreate / BundleItemIn / BundleResponse / BundleItemResponse |
| `bundle_repository.py` | Repository | add/get/list_items/exists_code/sku_exists/add_item |
| `bundle_service.py` | Service | create / get_with_items / split_quantities |
| `bundle_api.py` | Router | /api/bundles（GET/POST + GET/{id}） |

### modules/report（6，BI + 导出）
| 组件 | 类型 | 职责 |
|---|---|---|
| `user_preference_models.py` | ORM | UserPreference（TenantScopedModel + RLS） |
| `user_preference_service.py` | Service | get_or_default / upsert（含内联 repo 查询） |
| `bi_service.py` | Service | get_dashboard（复用 ProductionService/StoreDailyService 聚合 cards+charts） |
| `export_service.py` | Service | export（openpyxl write_only 流式）+ _fetch_rows + _cell |
| `bi_api.py` | Router | /api/reports/bi + /api/reports/bi/layout |
| `export_api.py` | Router | /api/reports/{report_type}/export |

---

## 2. 横切修改（7）

| 文件 | 改动 |
|---|---|
| `product/permissions.py` | +product.bundle:read/write |
| `product/deps.py` | +BundleServiceDep |
| `report/advanced_permissions.py` | +report.export:read 常量 |
| `report/deps.py` | +BiServiceDep / ExportServiceDep / UserPreferenceServiceDep |
| `report/exceptions.py` | +ReportExportTypeInvalidError（400） |
| `core/metrics.py` | +report_export_total{report_type,result}（+ __all__） |
| `main.py` | 挂 bundle_router + bi_router + export_router |
| `tests/conftest.py` | 追加 bundle_models + user_preference_models import |
| `alembic/versions/021_*.py` | 3 表 + scope seed |

> exceptions/conftest/migration 与上表合并计 7 项主改动。

---

## 3. 依赖图（无循环）

```
bundle_api → BundleService → BundleRepository → bundle_models / sku(U02)
bi_api → BiService → ProductionService(U14) / StoreDailyService(U14)
       → UserPreferenceService → user_preference_models
export_api → ReportExportService → ProductionService/StoreDailyService/WorkProgressService(U14)
                                  → openpyxl(U06a) → StreamingResponse
```

依赖层级：U17 → U02（product/sku）+ U14（report service）→ U13/U05 → U01。无环（U02/U14 仅被读，不反向依赖 U17）。

---

## 4. migration 021 DDL 概要

```
revision = "021_u17_bundle_bi_export"
down_revision = "020_u16_order_adjustment_balance"

bundle_product（base + ）:
  bundle_code String(64) NOT NULL / bundle_name String(255) NOT NULL
  remark Text NULL / is_active Boolean NOT NULL DEFAULT true
  UNIQUE(tenant_id, bundle_code)  [uq_bundle_product_code]

bundle_item（base + ）:
  bundle_id UUID FK bundle_product CASCADE NOT NULL
  sku_id UUID FK sku RESTRICT NOT NULL
  quantity Integer NOT NULL CHECK >= 1
  UNIQUE(tenant_id, bundle_id, sku_id)  [uq_bundle_item_sku]
  INDEX(tenant_id, bundle_id)  [idx_bundle_item_bundle]

user_preference（base + ）:
  user_id UUID FK user CASCADE NOT NULL
  pref_key String(64) NOT NULL
  pref_value JSONB NOT NULL DEFAULT '{}'
  UNIQUE(tenant_id, user_id, pref_key)  [uq_user_preference]

enable_rls 3 表
seed: product.bundle:read/write（merchandiser）+ report.export:read（pr_manager/operations）
      （admin 通配 "*" 已覆盖）
```

down：drop 3 表 + 删 4 scope。

---

## 5. 启动序列影响

- `main` 挂 bundle_router（/api/bundles）+ bi_router + export_router（/api/reports/...）。
- 无新 Celery 任务 / Beat / 事件（U17 同步只读 + 写 bundle/偏好）。
- 无新依赖（openpyxl 已装）。

---

## 6. 测试组件映射（3 文件）

| 测试文件 | 目标组件 | 用例要点 |
|---|---|---|
| `tests/unit/test_bundle_export.py` | BundleService.split_quantities + ExportService._cell | A×1+B×2 卖 3 → [(A,3),(B,6)] / _cell（Decimal→str、None→""、int 透传） |
| `tests/integration/test_bundle_bi_export.py` | BundleService + UserPreferenceService + BiService + ExportService | create + 跨租户 sku 422 + 同 sku 重复 422 + split + 偏好 upsert/get_or_default + 导出 xlsx openpyxl 可解析（表头 + 行）+ RLS |
| `tests/api/test_bundle_export_api.py` | bundle_api + bi_api + export_api | /api/bundles + /api/reports/bi + /api/reports/production/export 401 + OpenAPI 路径 |

- 复用 conftest fixtures：session/tenant_a/factory/follower_role(merchandiser)/admin_role/product_factory；ROI/store 数据复用 U13/U14 模式。

---

## 7. 一致性校验

- 与 nfr-design-patterns P-U17-01~03 伪代码组件一致。
- 与 functional-design domain-entities 组件清单（11 新建 + 7 横切）一致。
- 复用 U14 report service、U02 product、openpyxl（U06a）、U01 metrics/audit/权限，无重复实现。
- 依赖图无循环（拓扑：U01 → U02/U05/U13 → U14 → U17）。
