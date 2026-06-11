# U06b StyleSkuImportAdapter 规格（Adapter Spec）

> source: `manual_style_sku` / target_table: `style+sku`
> 文件: `backend/app/modules/importer/adapters/style_sku.py`

---

## 1. 三方法签名（实现 U06a ImportAdapter 协议）

```python
class StyleSkuImportAdapter:
    source = "manual_style_sku"
    target_table = "style+sku"

    def parse_row(self, row, mapping) -> dict          # 纯函数：表头→字段 + 类型转换
    def validate(self, parsed) -> list[str]            # 纯函数：错误列表（空=通过）
    async def upsert(self, parsed, *, session, tenant_id, actor_id) -> tuple[UUID, bool]
```

---

## 2. 默认字段映射（_DEFAULT_COLUMNS，mapping=None 回退）

| source_col | target_field | type | required |
|---|---|---|---|
| 款式编码 | style_code | str | ✅ |
| 款式名称 | style_name | str | ✅ |
| 类目 | category | str | ✅ |
| 品牌编码 | brand_code | str | — |
| 季节 | season | str | — |
| SKU编码 | sku_code | str | ✅ |
| 颜色 | color | str | ✅ |
| 尺码 | size | str | ✅ |
| 成本价 | cost_price | decimal | — |
| 采购价 | purchase_price | decimal | — |
| 吊牌价 | base_price | decimal | — |
| 货源类型 | sourcing_type | str | — |

> 自定义 mapping：parse_row 用 `mapping.mapping_config["columns"]`（运营经 U06a field-mapping API 建 active 版本）。

---

## 3. parse_row 行为

| type | 转换 | 空 |
|---|---|---|
| str | `str(v).strip()` | "" / None → None |
| decimal | `_to_decimal`：去千分位 `,` + `Decimal()`（禁 float）；非法 → 保留原串 | "" / None → None |

---

## 4. validate 规则

| 校验 | 错误文案 |
|---|---|
| 必填 6 项（style_code/style_name/category/sku_code/color/size） | `<标签>不能为空` |
| cost_price/purchase_price/base_price 是 Decimal 且 ≥0 | `<标签>必须为非负数字` |
| sourcing_type ∈ {自产,采购,代发}（非空时） | `货源类型必须为 自产/采购/代发 之一` |
| 长度上限（style_code≤64/style_name≤255/category≤32/sku_code≤64/color≤64/size≤32） | `<字段> 超过长度上限 N` |

---

## 5. upsert 流程

```
1. StyleRepository(session).get_by_code(style_code)
   ├─ 命中 → 复用 style.id（不更新任何字段，BR-U06b-31）
   └─ 未命中 → _resolve_brand(brand_code) → Style(...) → add → flush（拿 id）
2. SkuRepository(session).upsert_atomic(tenant_id, values{sku_code, style_id, color, size,
   cost_price, purchase_price, base_price, sourcing_type or "自产"})
   → (sku, is_inserted)   # ON CONFLICT(tenant,sku_code) WHERE is_deleted=false
3. return (sku.id, is_inserted)
```

- **不 commit**（runner 控制）；style+sku 同 per-row 事务（sku 失败 → 整行回滚）
- tenant_id：runner SET LOCAL（NF-1）+ ORM before_flush 钩子注入 style.tenant_id；upsert_atomic 显式传 tenant_id
- `_resolve_brand`：brand_code 非空 → `select(Brand.id).where(tenant_id, brand_code)`；查不到 None（软关联）

---

## 6. 注册

```python
def register() -> None:
    ImportAdapterRegistry.register(StyleSkuImportAdapter())
```

由 U06a `register_import_adapters`（main.py lifespan + Celery worker_process_init）双进程调用（NF-4）。main.py 已预置 `app.modules.importer.adapters.style_sku` 模块路径，本模块落地后自动注册。

---

## 7. 使用（运营视角）

```
POST /api/imports/upload   (multipart)
  source = manual_style_sku
  file   = 款式SKU.csv（默认表头见 §2）
→ 202 {batch_id, status: processing}
→ 异步 run_import_batch → 逐行 style 复用/建 + sku upsert
→ GET /api/imports/batches/{id}   查看 imported/failed/status
→ GET /api/imports/batches/{id}/errors/download   下载失败明细
→ POST /api/imports/batches/{id}/retry   重试失败行
```
