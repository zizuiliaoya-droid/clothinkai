# U06e NFR 设计模式（NFR Design Patterns）

> 单元：U06e — 结算导入适配器
> 范围：1 个增量模式 P-U06e-01（INSERT-only + promotion 派生 + UNIQUE 冲突 catch + 合成 event_id + 不触发事件）；其余继承 U06a + U06d + U05
> 关键：历史迁移 INSERT-only；FB3 不覆盖；不触发事件；adapter 不自 commit（FB-C）

---

## 0. 继承声明

| 模式 | 来源 | 依赖点 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（per-row SET LOCAL，NF-1） | U06a | adapter 在该事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4） | U06a | 实现 + register() |
| P-U06d-01 INSERT-only + FK 解析 | U06d | 复用结构 |
| U05 next_settlement_sequence + format_settlement_no | U05 | settlement_no 生成 |

---

## P-U06e-01：INSERT-only settlement 编排（历史迁移）

### 问题
结算历史迁移与 U06d 类似（INSERT-only + 序列），但增加：
- **promotion 派生**（blogger/style/pr 从 promotion，不让文件提供）
- **UNIQUE(promotion_id) 一对一**：重复 promotion → 冲突（FB3 不覆盖）
- **合成 request_event_id**（导入无真实事件）
- **不触发事件**（区别 U05 service 的 SettlementPaid）

### 方案

```python
# modules/importer/adapters/settlement.py
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from sqlalchemy.exc import IntegrityError

from app.modules.finance.enums import SettlementStatus
_VALID_STATUS = {s.value for s in SettlementStatus}  # 5 枚举

_DEFAULT_COLUMNS = [  # 9 列（domain-entities §4）
    {"source_col": "推广编号", "target_field": "promotion_internal_code", "type": "str"},
    {"source_col": "结算日期", "target_field": "settlement_date", "type": "date"},
    {"source_col": "金额", "target_field": "amount", "type": "decimal"},
    {"source_col": "总金额", "target_field": "total_amount", "type": "decimal"},
    {"source_col": "付款金额", "target_field": "payment_amount", "type": "decimal"},
    {"source_col": "付款日期", "target_field": "payment_date", "type": "date"},
    {"source_col": "结算状态", "target_field": "settlement_status", "type": "str"},
    {"source_col": "笔记标题", "target_field": "note_title", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]
_REQUIRED = (("promotion_internal_code", "推广编号"),)


def _to_date(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        return str(raw)


def _to_decimal(raw):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())  # 禁 float
    except InvalidOperation:
        return str(raw)


class SettlementImportAdapter:
    source = "manual_settlement"
    target_table = "settlement"

    def __init__(self):
        self._tenant_code_cache: dict = {}

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
        # amount / total_amount 必填 Decimal ≥0
        for f, label in [("amount", "金额"), ("total_amount", "总金额")]:
            v = parsed.get(f)
            if v is None:
                errs.append(f"{label}不能为空")
            elif not isinstance(v, Decimal) or v < 0:
                errs.append(f"{label}必须为非负数字")
        # payment_amount 可选 Decimal ≥0
        pa = parsed.get("payment_amount")
        if pa is not None and (not isinstance(pa, Decimal) or pa < 0):
            errs.append("付款金额必须为非负数字")
        # date
        sd = parsed.get("settlement_date")
        if sd is None:
            errs.append("结算日期不能为空")
        elif not isinstance(sd, date):
            errs.append("结算日期格式错误（应为 YYYY-MM-DD）")
        pd = parsed.get("payment_date")
        if pd is not None and not isinstance(pd, date):
            errs.append("付款日期格式错误（应为 YYYY-MM-DD）")
        # status 枚举
        st = parsed.get("settlement_status")
        if st and st not in _VALID_STATUS:
            errs.append("结算状态必须为 待核查/待付款/待财务付款/已付款/已驳回 之一")
        nt = parsed.get("note_title")
        if nt and len(nt) > 255:
            errs.append("note_title 超过长度上限 255")
        return errs

    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        promotions = PromotionRepository(session)
        settlements = SettlementRepository(session)

        # 1) promotion 派生（不让文件提供 blogger/style/pr）
        promo = await promotions.get_by_internal_code(parsed["promotion_internal_code"])
        if promo is None:
            raise RowValidationError(
                f"推广编号 {parsed['promotion_internal_code']} 不存在"
            )

        # 2) settlement_no（tenant_code 缓存 + FB2 序列）+ 合成 event_id
        tenant_code = await self._get_tenant_code(session, tenant_id)
        seq = await settlements.next_settlement_sequence(
            tenant_id=tenant_id, date_key=parsed["settlement_date"]
        )  # SequenceOverflowError → runner failed
        settlement_no = format_settlement_no(
            tenant_code=tenant_code,
            date_key=parsed["settlement_date"],
            sequence=seq,
        )

        # 3) INSERT settlement（不触发事件；FB3 不覆盖既有）
        settlement = Settlement(
            promotion_id=promo.id,
            blogger_id=promo.blogger_id,       # 派生
            style_id=promo.style_id,           # 派生
            pr_id=promo.pr_id,                 # 派生
            settlement_no=settlement_no,
            amount=parsed["amount"],
            total_amount=parsed["total_amount"],
            payment_amount=parsed.get("payment_amount"),
            payment_date=parsed.get("payment_date"),
            settlement_status=parsed.get("settlement_status") or "待核查",
            note_title=parsed.get("note_title"),
            remark=parsed.get("remark"),
            request_event_id=uuid4(),          # 合成（导入无真实事件）
        )
        settlements.add(settlement)
        try:
            await session.flush()
        except IntegrityError as exc:
            # UNIQUE(promotion_id) 冲突 → 该 promotion 已有 settlement（FB3 不覆盖）
            raise RowValidationError(
                "该推广已有结算单（不可重复，FB3）"
            ) from exc
        return settlement.id, True   # INSERT-only

    async def _get_tenant_code(self, session, tenant_id):
        if tenant_id not in self._tenant_code_cache:
            from app.modules.auth.models import Tenant
            code = (await session.execute(
                select(Tenant.code).where(Tenant.id == tenant_id)
            )).scalar_one_or_none() or ""
            self._tenant_code_cache[tenant_id] = code
        return self._tenant_code_cache[tenant_id]


def register() -> None:
    ImportAdapterRegistry.register(SettlementImportAdapter())
```

### 关键点
- **INSERT-only**：add + flush；is_inserted 恒 True
- **promotion 派生**：blogger/style/pr 从 promotion（保证一致，不让文件提供）
- **UNIQUE(promotion_id) catch**：IntegrityError → RowValidationError（FB3 不覆盖；per-row 事务隔离，runner 的 AsyncSessionApp context manager 异常冒泡回滚该行）
- **合成 request_event_id = uuid4()**：满足 UNIQUE 约束 + 标识导入来源
- **不触发事件**：不调 event_bus.dispatch（导入是数据迁移）
- **不经 U05 Service**：避免 commit/audit/事件/状态机校验
- date/Decimal 禁 float；status ∈ 5 枚举校验

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| adapter 不自 commit（FB-C） | ✅ upsert 仅用传入 session |
| INSERT-only（is_inserted 恒 True） | ✅ add + flush |
| promotion 派生（不让文件提供） | ✅ blogger/style/pr from promo |
| UNIQUE(promotion_id) 冲突 catch（FB3） | ✅ IntegrityError → RowValidationError |
| 合成 request_event_id | ✅ uuid4() |
| 不触发事件 | ✅ 不调 event_bus |
| settlement_no（FB2 序列 + format） | ✅ next_settlement_sequence + format_settlement_no |
| date/Decimal 禁 float + status 枚举 | ✅ _to_date/_to_decimal/_VALID_STATUS |
| 跨租户 tenant_id（NF-1 + RLS） | ✅ promotion 查受 RLS + ORM 钩子注入 |
