# U06d NFR 设计模式（NFR Design Patterns）

> 单元：U06d — 推广导入适配器
> 范围：1 个增量模式 P-U06d-01（INSERT-only + FK 解析 + 序列生成）；其余继承 U06a P-U06a-01~05 + U04 sequence/format_internal_code
> 关键：INSERT-only（非 upsert）；FK 解析失败 raise；internal_code 系统生成；adapter 不自 commit（FB-C）

---

## 0. 继承声明

| 模式 | 来源 | 依赖点 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（per-row SET LOCAL，NF-1） | U06a | adapter 在该事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4 双进程注册） | U06a | 实现 + register() |
| P-U06a-03/04/05 上传/重试/安全 | U06a | 框架处理 |
| U04 next_internal_sequence（FB2 INSERT ON CONFLICT RETURNING） | U04 | 原子序列 |
| U04 format_internal_code | U04 | internal_code 格式化 |

---

## P-U06d-01：INSERT-only promotion 编排（FK 解析 + 序列生成）

### 问题
推广导入与 U06b/c 的 upsert 不同：
- **INSERT-only**（internal_code 系统生成，无文件业务键 → 无法 upsert）
- **2 必需 FK 解析**（style_code/xiaohongshu_id）+ 1 可选（sku_code）
- **internal_code 生成**（需 tenant_code + sequence + format）
- 快照 + 3 状态默认 + pr_id=actor_id

需保证：adapter 不自 commit（FB-C）；FK+sequence+INSERT 同 per-row 事务原子（FK 缺失整行回滚，不建残缺 promotion）；worker 无 HTTP User → 直接用 Repository。

### 方案

```python
# modules/importer/adapters/promotion.py
from datetime import date
from decimal import Decimal, InvalidOperation

_DEFAULT_COLUMNS = [  # 10 列（domain-entities §4）
    {"source_col": "款式编码", "target_field": "style_code", "type": "str"},
    {"source_col": "SKU编码", "target_field": "sku_code", "type": "str"},
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "报价金额", "target_field": "quote_amount", "type": "decimal"},
    {"source_col": "成本快照", "target_field": "cost_snapshot", "type": "decimal"},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "合作日期", "target_field": "cooperation_date", "type": "date"},
    {"source_col": "计划发布日期", "target_field": "scheduled_publish_date", "type": "date"},
    {"source_col": "笔记标题", "target_field": "note_title", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]
_REQUIRED = (("style_code", "款式编码"), ("xiaohongshu_id", "小红书ID"))
_MAX_LEN = (("style_code", 64), ("sku_code", 64), ("xiaohongshu_id", 64),
            ("platform", 16), ("note_title", 255))


def _to_date(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return str(raw)  # 非法 → validate 检出


def _to_decimal(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())  # 禁 float
    except InvalidOperation:
        return str(raw)


class PromotionImportAdapter:
    source = "manual_promotion"
    target_table = "promotion"

    def __init__(self):
        self._tenant_code_cache: dict = {}   # tenant_id → code（不可变，安全）

    def parse_row(self, row, mapping):
        columns = (mapping.mapping_config["columns"] if mapping else _DEFAULT_COLUMNS)
        parsed = {}
        for col in columns:
            raw = row.get(col["source_col"])
            t = col.get("type", "str")
            tgt = col["target_field"]
            if t == "decimal":
                parsed[tgt] = _to_decimal(raw)
            elif t == "date":
                parsed[tgt] = _to_date(raw)
            else:
                parsed[tgt] = (str(raw).strip() if raw not in (None, "") else None)
        return parsed

    def validate(self, parsed):
        errs = []
        for f, label in _REQUIRED:
            if not parsed.get(f):
                errs.append(f"{label}不能为空")
        q = parsed.get("quote_amount")
        if q is None:
            errs.append("报价金额不能为空")
        elif not isinstance(q, Decimal) or q < 0:
            errs.append("报价金额必须为非负数字")
        c = parsed.get("cost_snapshot")
        if c is not None and (not isinstance(c, Decimal) or c < 0):
            errs.append("成本快照必须为非负数字")
        cd = parsed.get("cooperation_date")
        if cd is None:
            errs.append("合作日期不能为空")
        elif not isinstance(cd, date):
            errs.append("合作日期格式错误（应为 YYYY-MM-DD）")
        sd = parsed.get("scheduled_publish_date")
        if sd is not None and not isinstance(sd, date):
            errs.append("计划发布日期格式错误（应为 YYYY-MM-DD）")
        for f, n in _MAX_LEN:
            v = parsed.get(f)
            if v and isinstance(v, str) and len(v) > n:
                errs.append(f"{f} 超过长度上限 {n}")
        return errs

    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        styles = StyleRepository(session)
        bloggers = BloggerRepository(session)
        promotions = PromotionRepository(session)

        # 1) FK 解析（必需 raise → runner failed）
        style = await styles.get_by_code(parsed["style_code"])
        if style is None:
            raise ImportRowError(f"款式编码 {parsed['style_code']} 不存在")
        blogger = await bloggers.get_by_xiaohongshu_id(parsed["xiaohongshu_id"])
        if blogger is None:
            raise ImportRowError(f"博主 {parsed['xiaohongshu_id']} 不存在")
        sku_id = None
        if parsed.get("sku_code"):
            sku = await SkuRepository(session).get_by_code(parsed["sku_code"])
            if sku is None:
                raise ImportRowError(f"SKU编码 {parsed['sku_code']} 不存在")
            sku_id = sku.id

        # 2) internal_code 生成（tenant_code 缓存 + FB2 原子序列）
        tenant_code = await self._get_tenant_code(session, tenant_id)
        seq = await promotions.next_internal_sequence(
            tenant_id=tenant_id, date_key=parsed["cooperation_date"]
        )  # SequenceOverflowError → runner failed
        internal_code = format_internal_code(
            tenant_code=tenant_code,
            cooperation_date=parsed["cooperation_date"],
            sequence=seq,
        )

        # 3) INSERT promotion（3 状态走 server_default 初始态）
        promotion = Promotion(
            style_id=style.id, sku_id=sku_id, blogger_id=blogger.id,
            pr_id=actor_id, internal_code=internal_code,
            style_code_snapshot=style.style_code,
            style_short_name_snapshot=style.short_name or style.style_name,
            quote_amount=parsed["quote_amount"],
            cost_snapshot=parsed.get("cost_snapshot"),
            platform=parsed.get("platform") or "小红书",
            cooperation_date=parsed["cooperation_date"],
            scheduled_publish_date=parsed.get("scheduled_publish_date"),
            note_title=parsed.get("note_title"),
            remark=parsed.get("remark"),
        )
        promotions.add(promotion)
        await session.flush()
        return promotion.id, True   # INSERT-only → is_inserted 恒 True

    async def _get_tenant_code(self, session, tenant_id):
        if tenant_id not in self._tenant_code_cache:
            from app.modules.auth.models import Tenant
            code = (await session.execute(
                select(Tenant.code).where(Tenant.id == tenant_id)
            )).scalar_one_or_none() or ""
            self._tenant_code_cache[tenant_id] = code
        return self._tenant_code_cache[tenant_id]


def register() -> None:
    ImportAdapterRegistry.register(PromotionImportAdapter())
```

> `ImportRowError`：U06d 内部行级异常（runner 捕获写 import_job.failed）。复用 U06a RowValidationError 或定义轻量异常均可（Code Gen 阶段决定，倾向复用 ValueError/U06a RowValidationError，避免新异常类）。

### 关键点
- **INSERT-only**：add + flush（无 upsert）；is_inserted 恒 True
- **不自 commit**：复用 runner 传入 session（P-U06a-01）
- **FK+sequence+INSERT 同 per-row 事务原子**：FK 缺失 raise → runner rollback 整行（含已 UPDATE 的 sequence，不浪费号）
- **tenant_code 实例级缓存**：worker 进程长生命周期跨 batch 复用（tenant.code 不可变）
- **3 状态 server_default**：不显式传（与 U04 create 一致）
- **date/Decimal 禁 float**：非法保留原串 → validate 检出
- **不经 U04 Service**：避免 commit/audit/重复检测 warning/权限与 runner 事务冲突

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| adapter 不自 commit（FB-C） | ✅ upsert 仅用传入 session |
| INSERT-only（is_inserted 恒 True） | ✅ add + flush |
| FK 解析必需 raise → failed | ✅ style/blogger/sku |
| FK+sequence+INSERT per-row 原子 | ✅ 继承 P-U06a-01 |
| internal_code（FB2 序列 + format） | ✅ next_internal_sequence + format_internal_code |
| tenant_code 缓存安全 | ✅ 不可变 + 实例级 |
| date/Decimal 禁 float | ✅ _to_date/_to_decimal |
| 跨租户 tenant_id（NF-1 + RLS） | ✅ FK 查询受 RLS + ORM 钩子注入 |
