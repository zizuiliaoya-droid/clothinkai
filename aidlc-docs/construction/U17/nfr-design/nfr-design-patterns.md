# U17 NFR 设计模式（套装 + BI 看板 + 报表导出）

> 3 个设计模式伪代码：套装创建 + 销量拆分 / BI 看板聚合 + 布局 / 报表导出 openpyxl 流式。
> 复用 U14 ProductionService/StoreDailyService + openpyxl write_only（U06a）。

---

## P-U17-01：套装创建 + 销量拆分（BundleService）

```python
class BundleService:
    def __init__(self, session):
        self._s = session
        self._repo = BundleRepository(session)

    async def create(self, payload: BundleCreate, user) -> BundleProduct:
        if not payload.items:
            raise ValidationError("套装至少含 1 个组合项")        # BR-U17-02
        seen = set()
        for it in payload.items:
            if it.quantity < 1:
                raise ValidationError("quantity 须 >= 1")          # BR-U17-05
            if it.sku_id in seen:
                raise ValidationError("同一套装 sku 不可重复")      # BR-U17-04
            seen.add(it.sku_id)
            if not await self._repo.sku_exists(it.sku_id, user.tenant_id):
                raise ValidationError("sku 不存在或跨租户")         # BR-U17-03（422）
        bundle = BundleProduct(
            tenant_id=user.tenant_id, bundle_code=payload.bundle_code,
            bundle_name=payload.bundle_name, remark=payload.remark, is_active=True,
        )
        self._repo.add(bundle)
        try:
            await self._s.flush()                                  # UNIQUE(tenant,code) → 409
        except IntegrityError as exc:
            raise DuplicateResourceError("bundle_code 已存在") from exc
        for it in payload.items:
            self._repo.add_item(BundleItem(
                tenant_id=user.tenant_id, bundle_id=bundle.id,
                sku_id=it.sku_id, quantity=it.quantity))
        await self._s.flush()
        await AuditService(self._s).log("product.bundle.create",
                                        resource="bundle_product",
                                        resource_id=bundle.id, user_id=user.id)
        await self._s.commit()
        return bundle

    async def get_with_items(self, bundle_id) -> tuple:
        bundle = await self._repo.get(bundle_id)
        if bundle is None:
            raise ResourceNotFoundError("bundle 不存在")
        items = await self._repo.list_items(bundle_id)
        return bundle, items

    async def split_quantities(self, bundle_id, sold_qty: int) -> list[tuple]:
        """EP02-S08：销量按 item 数量拆分到各 sku。纯函数式。"""
        items = await self._repo.list_items(bundle_id)
        return [(it.sku_id, it.quantity * sold_qty) for it in items]
```
> sku 跨租户校验 + UNIQUE 双保险（BR-U17-01/03/04/05）。split_quantities 供投产报表口径扩展。

---

## P-U17-02：BI 看板聚合 + 布局（BiService + UserPreferenceService）

```python
_DEFAULT_BI_LAYOUT = {"cards": ["style_count", "pay_amount", "avg_roi"],
                      "charts": ["store_trend", "style_roi_bar", "style_pay_pie"]}

class BiService:
    async def get_dashboard(self, tenant_id, time_range) -> dict:
        prod = await ProductionService(self._s).get_report(tenant_id, time_range)
        store = await StoreDailyService(self._s).get_dashboard(tenant_id, time_range)
        pay_total = sum((r.pay_amount for r in prod.items), Decimal("0"))
        cards = [
            {"key": "style_count", "label": "在投款式", "value": len(prod.items)},
            {"key": "pay_amount", "label": "支付额", "value": str(pay_total)},
            {"key": "store_days", "label": "店铺天数", "value": len(store)},
        ]
        charts = [
            {"type": "line", "title": "店铺支付额趋势",
             "labels": [str(r.date) for r in store],
             "series": [{"name": "支付额", "data": [float(r.pay_amount) for r in store]}]},
            {"type": "bar", "title": "款式净投产比",
             "labels": [r.style_code for r in prod.items[:10]],
             "series": [{"name": "净投产比",
                         "data": [float(r.net_roi or 0) for r in prod.items[:10]]}]},
            {"type": "pie", "title": "款式支付额占比",
             "labels": [r.style_code for r in prod.items[:10]],
             "series": [{"name": "支付额",
                         "data": [float(r.pay_amount) for r in prod.items[:10]]}]},
        ]
        return {"cards": cards, "charts": charts}


class UserPreferenceService:
    async def get_or_default(self, user_id, key, default) -> dict:
        row = await self._get(user_id, key)
        return row.pref_value if row is not None else default

    async def upsert(self, user, key, value) -> None:
        stmt = (pg_insert(UserPreference)
                .values(tenant_id=user.tenant_id, user_id=user.id,
                        pref_key=key, pref_value=value)
                .on_conflict_do_update(
                    index_elements=["tenant_id", "user_id", "pref_key"],
                    set_={"pref_value": value, "updated_at": func.now()}))
        await self._s.execute(stmt)
        await self._s.commit()
```
> get_layout = get_or_default(user.id, "bi_layout", _DEFAULT_BI_LAYOUT)；save_layout = upsert（BR-U17-23/24）。

---

## P-U17-03：报表导出 openpyxl 流式（ReportExportService）

```python
import io
from openpyxl import Workbook
from fastapi.responses import StreamingResponse

_HEADERS = {
    "work-progress": ["PR", "约篇量", "发布量", "发布率", "超时率", "爆文数", "点赞", "成本", "CPL"],
    "production": ["款号", "款名", "支付额", "退款额", "退货率", "净投产比", "加购成本", "总花费"],
    "store-daily": ["日期", "访客", "支付额", "支付订单", "广告花费"],
}

def _cell(v):
    if v is None:
        return ""
    return str(v) if isinstance(v, Decimal) else v

class ReportExportService:
    async def export(self, report_type, time_range, filters) -> StreamingResponse:
        if report_type not in _HEADERS:
            report_export_total.labels(report_type=report_type, result="invalid").inc()
            raise ReportExportTypeInvalidError(f"不支持的报表类型: {report_type}")  # 400
        rows = await self._fetch_rows(report_type, time_range, filters)
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(report_type)
        ws.append(_HEADERS[report_type])
        for r in rows:
            ws.append([_cell(c) for c in r])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        report_export_total.labels(report_type=report_type, result="success").inc()
        fname = f"{report_type}_{time_range[0]}_{time_range[1]}.xlsx"
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})

    async def _fetch_rows(self, report_type, time_range, filters) -> list[list]:
        if report_type == "production":
            rep = await ProductionService(self._s).get_report(self._tid, time_range)
            return [[r.style_code, r.style_name, r.pay_amount, r.refund_amount,
                     r.return_rate, r.net_roi, r.add_cart_cost, r.total_spend]
                    for r in rep.items]
        if report_type == "store-daily":
            rows = await StoreDailyService(self._s).get_dashboard(self._tid, time_range)
            return [[r.date, r.visitors, r.pay_amount, r.pay_orders, r.ad_spend_total]
                    for r in rows]
        if report_type == "work-progress":
            month = f"{time_range[0]:%Y-%m}"
            rows = await WorkProgressService(self._s).get_for_month(self._tid, month)
            return [[r.pr_name, r.quote_count, r.publish_count, r.month_complete_rate,
                     r.overdue_rate, r.hit_count, r.like_count, r.cost, r.cpl]
                    for r in rows]
        return []
```
> 权限在 export_api 层 require_permission("report.export","read")（403 由 deps，service 不重复，BR-U17-44）。
> 空数据仅表头（BR-U17-45）。openpyxl write_only 流式内存安全。

---

## 故事 / NFR 映射

| 模式 | 故事 | 规则 |
|---|---|---|
| P-U17-01 | EP02-S08 | BR-U17-01~07 |
| P-U17-02 | EP09-S06 | BR-U17-20~25 |
| P-U17-03 | EP09-S08 | BR-U17-40~45 |
