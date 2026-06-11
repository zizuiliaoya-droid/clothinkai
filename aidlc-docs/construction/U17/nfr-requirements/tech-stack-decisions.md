# U17 技术栈决策（套装 + BI 看板 + 报表导出）

> 零新依赖：复用 openpyxl==3.1.5（U06a）+ U14 report service + U02 product + U01 核心。

---

## 1. 依赖

**无新增第三方依赖。** 复用：
- `openpyxl==3.1.5`（write_only Workbook，U06a 已装）
- U14 report service（WorkProgressService/ProductionService/StoreDailyService + resolve_time_range）
- U02 product（Sku 模型）
- prometheus Counter / AuditService（U01）
- `io.BytesIO` / `fastapi.responses.StreamingResponse`（标准/已有）

---

## 2. 文件落点

### 新建（11）
| 文件 | 模块 | 内容 |
|---|---|---|
| `product/bundle_models.py` | product | BundleProduct + BundleItem ORM |
| `product/bundle_schemas.py` | product | BundleCreate / BundleItemIn / BundleResponse |
| `product/bundle_repository.py` | product | bundle CRUD + list_items |
| `product/bundle_service.py` | product | create / get_with_items / split_quantities |
| `product/bundle_api.py` | product | /api/bundles |
| `report/user_preference_models.py` | report | UserPreference ORM |
| `report/user_preference_service.py` | report | get_or_default / upsert |
| `report/bi_service.py` | report | get_dashboard（复用 report service） |
| `report/export_service.py` | report | export（openpyxl 流式） |
| `report/bi_api.py` | report | /api/reports/bi + /bi/layout |
| `report/export_api.py` | report | /api/reports/{type}/export |

### 横切修改（7）
| 文件 | 改动 |
|---|---|
| `product/permissions.py` | +product.bundle:read/write |
| `product/deps.py` | +BundleServiceDep |
| `report/advanced_permissions.py` | +report.export:read |
| `report/deps.py` | +BiServiceDep / ExportServiceDep / UserPreferenceServiceDep |
| `core/metrics.py` | +report_export_total{report_type,result} |
| `main.py` | 挂 bundle_router + bi_router + export_router |
| `tests/conftest.py` | 追加 bundle_models + user_preference_models import |
| `alembic/versions/021_*.py` | 3 表 + scope seed |

---

## 3. 导出 openpyxl 流式片段

```python
import io
from openpyxl import Workbook
from fastapi.responses import StreamingResponse

_HEADERS = {
    "work-progress": ["PR", "约篇量", "发布量", "发布率", "超时率", "爆文数", "点赞", "成本", "CPL"],
    "production": ["款号", "支付额", "退款额", "退货率", "净投产比", "加购成本", "总花费"],
    "store-daily": ["日期", "访客", "支付额", "支付订单", "广告花费"],
}

def _cell(v):
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(v)
    return v

async def export(self, report_type, time_range, filters) -> StreamingResponse:
    if report_type not in _HEADERS:
        raise ReportExportTypeInvalidError(...)        # 400
    rows = await self._fetch_rows(report_type, time_range, filters)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(report_type)
    ws.append(_HEADERS[report_type])
    for r in rows:
        ws.append([_cell(c) for c in r])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = f"{report_type}_{time_range[0]}_{time_range[1]}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
```

---

## 4. split_quantities（销量拆分）

```python
async def split_quantities(self, bundle_id, sold_qty: int) -> list[tuple[UUID, int]]:
    items = await self._repo.list_items(bundle_id)
    return [(it.sku_id, it.quantity * sold_qty) for it in items]
```

---

## 5. BI 数据集组装

```python
async def get_dashboard(self, tenant_id, time_range) -> dict:
    prod = await ProductionService(self._s).get_report(tenant_id, time_range)
    store = await StoreDailyService(self._s).get_dashboard(tenant_id, time_range)
    cards = [
        {"key": "style_count", "label": "在投款式", "value": len(prod.items)},
        {"key": "pay_amount", "label": "支付额", "value": str(sum(...))},
        ...
    ]
    charts = [
        {"type": "line", "title": "店铺支付额趋势", "labels": [str(r.date) for r in store],
         "series": [{"name": "支付额", "data": [float(r.pay_amount) for r in store]}]},
        {"type": "bar", "title": "款式净投产比", "labels": [...], "series": [...]},
        {"type": "pie", "title": "款式支付额占比", "labels": [...], "series": [...]},
    ]
    return {"cards": cards, "charts": charts}
```

---

## 6. 指标 + migration 021

```python
report_export_total = Counter(
    "report_export_total",
    "Total report exports (U17)",
    labelnames=("report_type", "result"),  # success/forbidden/invalid
)
```

```python
revision = "021_u17_bundle_bi_export"
down_revision = "020_u16_order_adjustment_balance"
# bundle_product（base + bundle_code/bundle_name/remark/is_active + UNIQUE(tenant,bundle_code)）
# bundle_item（base + bundle_id FK CASCADE + sku_id FK RESTRICT + quantity CHECK>=1
#   + UNIQUE(tenant,bundle_id,sku_id) + idx(tenant,bundle_id)）
# user_preference（base + user_id FK CASCADE + pref_key + pref_value JSONB
#   + UNIQUE(tenant,user_id,pref_key)）
# enable_rls 3 表；seed product.bundle:read/write（merchandiser）+ report.export:read（pr_manager/operations）
```
- revision id `"021_u17_bundle_bi_export"`（24 字符 ≤ 32）。

---

## 7. 测试落点

| 文件 | 重点 |
|---|---|
| `tests/unit/test_bundle_export.py` | split_quantities + 导出 _cell 序列化 |
| `tests/integration/test_bundle_bi_export.py` | bundle create + 跨租户 sku 422 + split + user_preference + 导出 xlsx 可解析 + RLS |
| `tests/api/test_bundle_export_api.py` | bundles + bi + export 401/403 + OpenAPI |

- 本地 Docker PG16:5560 + Redis7:6415 + Py3.12（U17 唯一端口）。
