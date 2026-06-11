# U16 NFR 设计模式（拍单 / 刷单 / 余额）

> 4 个设计模式伪代码：拍单自动生成 listener / 刷单录入 + 金额解析 / 余额计算 + 校验 / ROI 隔离聚合改造。
> 复用 U05 finance listeners（多 handler）+ U14 ProductionRepository 聚合模式。

---

## P-U16-01：拍单自动生成（SettlementRequested listener + auto_create 幂等）

### listener（best-effort，不阻塞 settlement 创建）
```python
# modules/finance/listeners.py（追加）
async def on_settlement_requested_auto_order(event: SettlementRequested, session) -> None:
    from app.modules.promotion.repository import PromotionRepository
    promo = await PromotionRepository(session).get_by_id(event.promotion_id)
    if promo is None or not promo.in_store_order:
        return                                            # BR-U16-02
    try:
        await OrderAdjustmentService(session).auto_create_from_promotion(promo)
    except Exception as exc:  # noqa: BLE001 — best-effort，不阻塞 settlement（BR-U16-05）
        log.warning("auto_order_create_failed",
                    extra={"promotion_id": str(event.promotion_id), "err": str(exc)})
        order_adjustment_auto_created_total.labels(result="failed").inc()

def register() -> None:
    subscribe("SettlementRequested", on_settlement_requested)            # U05 既有
    subscribe("SettlementRequested", on_settlement_requested_auto_order)  # U16 新增（同事件多 handler）
```
> dispatch 按注册顺序遍历 handler；U05 创建 settlement 在前，U16 拍单在后。U16 内部 try/except 防止失败回滚整个 review approve 事务。

### auto_create_from_promotion（幂等）
```python
async def auto_create_from_promotion(self, promo) -> OrderAdjustment | None:
    existing = await self._repo.get_by_promotion(promo.id)
    if existing is not None:
        order_adjustment_auto_created_total.labels(result="skipped").inc()
        return existing                                    # BR-U16-04 幂等
    row = OrderAdjustment(
        order_type=OrderType.STORE_ORDER.value,            # 拍单
        order_date=promo.cooperation_date,
        style_id=promo.style_id, sku_id=promo.sku_id,
        blogger_identifier=str(promo.blogger_id),          # 博主快照标识
        promotion_id=promo.id,
        amount=Decimal("0"), exclude_from_roi=False,
        status=OrderAdjustmentStatus.PENDING_PAYMENT.value,
    )
    self._repo.add(row)
    try:
        await self._session.flush()
    except IntegrityError:                                  # 并发 UNIQUE(tenant,promotion_id) partial
        order_adjustment_auto_created_total.labels(result="skipped").inc()
        return None
    order_adjustment_auto_created_total.labels(result="created").inc()
    return row
```

---

## P-U16-02：刷单录入 + 金额表达式解析

### parse_amount_expr（不 eval）
```python
import re
from decimal import Decimal, InvalidOperation

_NUM = r"\d+(?:\.\d{1,2})?"
_EXPR = re.compile(rf"^\s*({_NUM})\s*(?:-\s*({_NUM}))?\s*$")

def parse_amount_expr(raw: str | Decimal) -> Decimal:
    if isinstance(raw, Decimal):
        return raw
    m = _EXPR.match(str(raw))
    if not m:
        raise AmountExpressionInvalidError(f"非法金额格式: {raw}")
    try:
        base = Decimal(m.group(1))
        rebate = Decimal(m.group(2)) if m.group(2) else Decimal("0")
    except InvalidOperation as exc:
        raise AmountExpressionInvalidError(f"非法金额: {raw}") from exc
    amount = base - rebate
    if amount < 0:
        raise AmountExpressionInvalidError(f"金额不能为负: {raw}")
    return amount
```

### create_brushing
```python
async def create_brushing(self, payload: BrushingCreate, user) -> dict:
    amount = parse_amount_expr(payload.amount_expr)        # "100-30" → 70（BR-U16-21）
    duplicate = False
    if payload.order_no:
        duplicate = await self._repo.exists_order_no(payload.order_no)  # warning 不阻断（BR-U16-22）
    row = OrderAdjustment(
        order_type=OrderType.BRUSHING.value,               # 刷单
        order_date=payload.order_date, order_no=payload.order_no,
        style_id=payload.style_id, sku_id=payload.sku_id,
        blogger_identifier=payload.blogger_identifier,
        amount=amount, exclude_from_roi=True,              # 默认剔除 ROI（BR-U16-20）
        status=OrderAdjustmentStatus.PENDING_PAYMENT.value, remark=payload.remark,
    )
    self._repo.add(row)
    await AuditService(self._session).log("finance.order.brushing_create",
                                          resource="order_adjustment", user_id=user.id)
    await self._session.commit()
    return {"id": row.id, "amount": str(amount), "duplicate": duplicate}
```

---

## P-U16-03：余额计算 + 校验（add_record）

```python
_EXPENSE_TYPES = {"推广支出", "刷拍单支出"}

async def add_record(self, payload: BalanceRecordCreate, user) -> BalanceRecord:
    self._validate_type_field(payload)                     # BR-U16-43/44
    prev = await self._repo.last_balance(user.tenant_id)   # Decimal('0') if none（BR-U16-41）
    income = payload.income or Decimal("0")
    expense = payload.expense or Decimal("0")
    balance_after = prev + income - expense                # BR-U16-40
    if payload.expected_balance is not None and payload.expected_balance != balance_after:
        raise BalanceMismatchError(                        # BR-U16-42 标红不保存
            f"余额不一致：计算={balance_after} 填写={payload.expected_balance}")
    row = BalanceRecord(
        record_date=payload.record_date, record_type=payload.record_type,
        income=payload.income, expense=payload.expense,
        balance_after=balance_after, remark=payload.remark, created_by=user.id,
    )
    self._repo.add(row)
    await AuditService(self._session).log("finance.balance.add",
                                          resource="balance_record", user_id=user.id)
    await self._session.commit()
    return row

@staticmethod
def _validate_type_field(p) -> None:
    if p.record_type == "充值":
        if not p.income or p.income <= 0 or p.expense is not None:
            raise BalanceTypeFieldMismatchError("充值仅可填收入(income)")
    elif p.record_type in _EXPENSE_TYPES:
        if not p.expense or p.expense <= 0 or p.income is not None:
            raise BalanceTypeFieldMismatchError("支出类仅可填支出(expense)")
    else:  # 其他：至少一项 > 0 且不同时
        if bool(p.income) == bool(p.expense):
            raise BalanceTypeFieldMismatchError("income/expense 须且仅填一项")
```

### last_balance（仓储）
```python
async def last_balance(self, tenant_id) -> Decimal:
    stmt = (select(BalanceRecord.balance_after)
            .where(BalanceRecord.tenant_id == tenant_id)   # 显式 tenant（RLS 兜底）
            .order_by(BalanceRecord.created_at.desc()).limit(1))
    val = (await self._s.execute(stmt)).scalar_one_or_none()
    return val if val is not None else Decimal("0")
```

---

## P-U16-04：ROI 隔离聚合改造（aggregate_by_style + exclude_brushing）

```python
# report/advanced_repository.py ProductionRepository.aggregate_by_style 增加 exclude_brushing 参数
# pay_amount 在 exclude_brushing=true 时减去刷单金额：
brushing_subtract = """
  - COALESCE((
      SELECT SUM(oa.amount) FROM order_adjustment oa
      WHERE oa.tenant_id = :tenant_id AND oa.style_id = s.id
        AND oa.order_type = '刷单' AND oa.exclude_from_roi = true
        AND oa.order_date BETWEEN :date_from AND :date_to
    ), 0)
""" if exclude_brushing else ""

sql = text(f"""
  SELECT s.id AS style_id, ...,
    (COALESCE(SUM(q.pay_amount), 0){brushing_subtract}) AS pay_amount,
    ...
  FROM style s LEFT JOIN platform_product pp ... LEFT JOIN qianniu_daily q ...
  WHERE s.tenant_id = :tenant_id AND s.is_deleted = false
  GROUP BY s.id, ...
""")
```
```python
# style_roi.py 移除 exclude_brushing 占位 TODO（公式不变，基于剔除后 pay 计算）
# production_service.py：
async def get_report(self, tenant_id, time_range, *, exclude_brushing: bool = True):  # V2 默认 true
    ... aggregate_by_style(..., exclude_brushing=exclude_brushing)
```
- exclude_brushing=false → 不含减项（保留 V1 含刷单口径，可对比）。
- 无刷单数据 → 子查询 SUM 为 NULL → COALESCE 0 → pay 不变（U14 测试安全）。

---

## 故事 / NFR 映射

| 模式 | 故事 | 规则 |
|---|---|---|
| P-U16-01 | EP06-S09 | BR-U16-01~06 |
| P-U16-02 | EP06-S10 录入 | BR-U16-20~22 |
| P-U16-03 | EP06-S11 | BR-U16-40~45 |
| P-U16-04 | EP06-S10 ROI | BR-U16-23~27 |
