# U06b NFR 设计模式（NFR Design Patterns）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：1 个增量模式 P-U06b-01（单行两实体 upsert 编排）；其余继承 U06a P-U06a-01~05 + U02 P-U02-03
> 关键：adapter 复用 runner 传入的 session，不自 commit（FB-C）；style+sku 同 per-row 事务原子

---

## 0. 继承声明（不重复设计）

| 模式 | 来源 | U06b 依赖点 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（per-row SET LOCAL，NF-1） | U06a | adapter 在该事务内执行；成功/失败 job 写入由 runner 负责 |
| P-U06a-02 Adapter 协议 + Registry（NF-4 双进程注册） | U06a | StyleSkuImportAdapter 实现 + register() |
| P-U06a-03 上传 DB 先行 + hash 去重（NF-2） | U06a | upload 走框架，adapter 无关 |
| P-U06a-04 两类失败重试 + 批次互斥（NF-3） | U06a | 行级失败 → import_job.failed → retry only_failed |
| P-U06a-05 安全文件处理（NF-6 + csv_safe） | U06a | 解析 / 上限 / 注入防护由框架处理 |
| P-U02-03 数据库原子 upsert | U02 | `SkuRepository.upsert_atomic`（ON CONFLICT RETURNING） |

---

## P-U06b-01：单行两实体 upsert 编排（Style 复用/创建 + Sku upsert）

### 问题
一行导入数据对应**两个实体**（Style + Sku）。需保证：
- adapter 不自行 commit（runner 持有 per-row 事务边界，FB-C）
- style + sku 在同一 per-row 事务原子（不产生"建了 style 没 sku"的孤儿，Q5）
- style 复用优先（不覆盖既有款式资料，BR-U06b-31）
- sku 幂等（ON CONFLICT，复用 U02 P-U02-03）
- Decimal 价格精度（禁 float，BR-U06b-13）
- worker 无 HTTP User → 直接用 Repository（不经 U02 Service 的 commit/audit/权限，Q1）

### 方案：adapter 复用 runner session + Repository 直操作

```python
# modules/importer/adapters/style_sku.py
from decimal import Decimal, InvalidOperation

_DEFAULT_COLUMNS = [  # mapping=None 回退（domain-entities §4）
    {"source_col": "款式编码", "target_field": "style_code", "required": True, "type": "str"},
    {"source_col": "款式名称", "target_field": "style_name", "required": True, "type": "str"},
    {"source_col": "类目", "target_field": "category", "required": True, "type": "str"},
    {"source_col": "品牌编码", "target_field": "brand_code", "required": False, "type": "str"},
    {"source_col": "季节", "target_field": "season", "required": False, "type": "str"},
    {"source_col": "SKU编码", "target_field": "sku_code", "required": True, "type": "str"},
    {"source_col": "颜色", "target_field": "color", "required": True, "type": "str"},
    {"source_col": "尺码", "target_field": "size", "required": True, "type": "str"},
    {"source_col": "成本价", "target_field": "cost_price", "required": False, "type": "decimal"},
    {"source_col": "采购价", "target_field": "purchase_price", "required": False, "type": "decimal"},
    {"source_col": "吊牌价", "target_field": "base_price", "required": False, "type": "decimal"},
    {"source_col": "货源类型", "target_field": "sourcing_type", "required": False, "type": "str"},
]
_REQUIRED = ("style_code", "style_name", "category", "sku_code", "color", "size")
_SOURCING = {"自产", "采购", "代发"}
_DECIMAL_FIELDS = ("cost_price", "purchase_price", "base_price")


class StyleSkuImportAdapter:
    source = "manual_style_sku"
    target_table = "style+sku"

    # ---- parse_row（纯函数，含 Decimal 转换）----
    def parse_row(self, row, mapping):
        columns = (mapping.mapping_config["columns"] if mapping else _DEFAULT_COLUMNS)
        parsed = {}
        for col in columns:
            raw = row.get(col["source_col"])
            tf = col.get("type", "str")
            if tf == "decimal":
                parsed[col["target_field"]] = self._to_decimal(raw)  # 失败 → 保留原值串供 validate
            else:
                parsed[col["target_field"]] = (str(raw).strip() if raw not in (None, "") else None)
        return parsed

    @staticmethod
    def _to_decimal(raw):
        if raw in (None, "") or str(raw).strip() == "":
            return None
        try:
            return Decimal(str(raw).replace(",", "").strip())  # 禁 float
        except InvalidOperation:
            return str(raw)  # 非 Decimal → validate 检出

    # ---- validate（纯函数）----
    def validate(self, parsed):
        errs = []
        for f, label in [("style_code","款式编码"),("style_name","款式名称"),
                         ("category","类目"),("sku_code","SKU编码"),
                         ("color","颜色"),("size","尺码")]:
            if not parsed.get(f):
                errs.append(f"{label}不能为空")
        for f, label in [("cost_price","成本价"),("purchase_price","采购价"),("base_price","吊牌价")]:
            v = parsed.get(f)
            if v is not None and (not isinstance(v, Decimal) or v < 0):
                errs.append(f"{label}必须为非负数字")
        st = parsed.get("sourcing_type")
        if st and st not in _SOURCING:
            errs.append("货源类型必须为 自产/采购/代发 之一")
        # 长度上限（BR-U06b-15）
        for f, n in [("style_code",64),("style_name",255),("category",32),
                     ("sku_code",64),("color",64),("size",32)]:
            v = parsed.get(f)
            if v and len(v) > n:
                errs.append(f"{f} 超过长度上限 {n}")
        return errs

    # ---- upsert（复用 runner session，不 commit，FB-C）----
    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        styles = StyleRepository(session)
        skus = SkuRepository(session)

        # 1) style get-or-create（复用不覆盖，BR-U06b-31）
        style = await styles.get_by_code(parsed["style_code"])
        if style is None:
            brand_id = await self._resolve_brand(session, tenant_id, parsed.get("brand_code"))
            style = Style(
                style_code=parsed["style_code"],
                style_name=parsed["style_name"],
                category=parsed["category"],
                season=parsed.get("season"),
                brand_id=brand_id,
                owner_id=actor_id,
                design_status="大货",
            )
            session.add(style)            # tenant_id 由 ORM before_flush 钩子注入
            await session.flush()         # 拿 style.id；UNIQUE 冲突 → 该行 failed（runner 捕获）

        # 2) sku upsert（ON CONFLICT，复用 U02 P-U02-03）
        sku, is_inserted = await skus.upsert_atomic(
            tenant_id=tenant_id,
            values={
                "sku_code": parsed["sku_code"],
                "style_id": style.id,
                "color": parsed["color"],
                "size": parsed["size"],
                "cost_price": parsed.get("cost_price"),
                "purchase_price": parsed.get("purchase_price"),
                "base_price": parsed.get("base_price"),
                "sourcing_type": parsed.get("sourcing_type") or "自产",
            },
        )
        return sku.id, is_inserted   # resource_id=sku.id；is_inserted 取 sku 路径

    async def _resolve_brand(self, session, tenant_id, brand_code):
        if not brand_code:
            return None
        row = (await session.execute(
            select(Brand.id).where(Brand.tenant_id == tenant_id, Brand.brand_code == brand_code)
        )).scalar_one_or_none()
        return row  # 查不到 → None（软关联，不报错，BR-U06b-33）


def register() -> None:
    ImportAdapterRegistry.register(StyleSkuImportAdapter())
```

### 关键点
- **不自 commit**：upsert 内全部用 runner 传入的 `session`；commit/rollback 由 P-U06a-01 runner 的 `async with AsyncSessionApp()` 控制
- **per-row 原子**：style flush + sku upsert 同事务；sku 失败 → 整行回滚（含新建 style，Q5），不留孤儿
- **复用不覆盖**：get_by_code 命中 → 仅用 style.id，不 setattr 任何 style 字段
- **Decimal 禁 float**：`_to_decimal` 用 Decimal；非法值保留原串 → validate 检出 → import_job.failed
- **brand 软关联**：仅建 style 时查；查不到 None 不报错
- **不经 U02 Service**：避免 Service 自带 commit/audit/权限与 runner 事务边界冲突（worker 无 HTTP User）

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| adapter 不自 commit（FB-C） | ✅ upsert 仅用传入 session |
| style+sku 同 per-row 事务原子（Q5） | ✅ 继承 P-U06a-01 runner 事务 |
| style 复用不覆盖（BR-U06b-31） | ✅ get_by_code 命中仅用 id |
| sku 幂等（U02 ON CONFLICT，P-U02-03） | ✅ upsert_atomic |
| Decimal 禁 float（BR-U06b-13） | ✅ _to_decimal |
| brand 软关联查不到留空（BR-U06b-33） | ✅ _resolve_brand → None |
| 内置默认 vs field_mapping 双路（Q7） | ✅ parse_row columns 选择 |
| 跨租户 tenant_id（NF-1） | ✅ runner SET LOCAL + ORM 钩子 + upsert_atomic 显式 tenant_id |
| 注册（NF-4 双进程） | ✅ register() + U06a register_import_adapters |
