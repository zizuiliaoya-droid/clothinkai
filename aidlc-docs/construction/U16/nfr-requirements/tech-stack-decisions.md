# U16 技术栈决策（拍单 / 刷单 / 余额）

> 零新依赖：复用 modules/finance + core/events + U14 ProductionService + Decimal/re（标准库）。

---

## 1. 依赖

**无新增第三方依赖。** 复用：
- `decimal.Decimal` / `re`（金额表达式解析，标准库）
- core/events（SettlementRequested 多 handler）
- U14 ProductionService / ProductionRepository
- U05 finance 模块落点 + AuditService + U01 metrics

---

## 2. 文件落点（modules/finance 追加）

### 新建（6）
| 文件 | 内容 |
|---|---|
| `order_adjustment_models.py` | OrderAdjustment + BalanceRecord ORM（TenantScopedModel + RLS） |
| `order_adjustment_schemas.py` | BrushingCreate / OrderAdjustmentResponse / BalanceRecordCreate / BalanceRecordResponse |
| `order_adjustment_repository.py` | OrderAdjustment + BalanceRecord 仓储（last_balance / brushing_sum_by_style / get_by_promotion） |
| `order_adjustment_service.py` | auto_create_from_promotion / create_brushing（金额解析）/ list |
| `balance_service.py` | add_record（计算 + 校验）/ list |
| `order_adjustment_api.py` | 拍单/刷单 + 余额 API（finance.order/balance scope） |

### 横切修改
| 文件 | 改动 |
|---|---|
| `finance/enums.py` | +OrderType / OrderAdjustmentStatus / BalanceRecordType |
| `finance/exceptions.py` | +AmountExpressionInvalidError / BalanceMismatchError / BalanceTypeFieldMismatchError |
| `finance/listeners.py` | +on_settlement_requested_auto_order + register() 追加 subscribe |
| `finance/permissions.py` | +finance.order:read/write / finance.balance:read/write |
| `finance/deps.py` | +OrderAdjustmentServiceDep / BalanceServiceDep |
| `promotion/models.py` | +in_store_order Boolean |
| `services/metric/style_roi.py` | 启用 exclude_brushing（移除占位 TODO） |
| `report/advanced_repository.py` | aggregate_by_style +exclude_brushing 减去刷单 SUM |
| `report/production_service.py` | get_report 默认 exclude_brushing=true |
| `report/advanced_api.py` | get_production query 默认 true |
| `core/metrics.py` | +order_adjustment_auto_created_total |
| `main.py` | 挂 order_adjustment_router |
| `tests/conftest.py` | 追加 finance.order_adjustment_models import |
| `alembic/versions/020_*.py` | 2 表 + promotion ALTER + scope seed |

---

## 3. 金额表达式解析（不 eval）

```python
import re
from decimal import Decimal, InvalidOperation

_NUM = r"\d+(?:\.\d{1,2})?"
_EXPR = re.compile(rf"^\s*({_NUM})\s*(?:-\s*({_NUM}))?\s*$")

def parse_amount_expr(s: str) -> Decimal:
    m = _EXPR.match(s)
    if not m:
        raise AmountExpressionInvalidError(f"非法金额格式: {s}")
    try:
        base = Decimal(m.group(1))
        rebate = Decimal(m.group(2)) if m.group(2) else Decimal("0")
    except InvalidOperation as exc:
        raise AmountExpressionInvalidError(f"非法金额: {s}") from exc
    amount = base - rebate
    if amount < 0:
        raise AmountExpressionInvalidError(f"金额不能为负: {s}")
    return amount
```
- 仅匹配"数字"或"数字-数字"（单减号）；多运算符/字母 → 不匹配 → 422。

---

## 4. 余额计算

```python
async def add_record(self, payload, user):
    # 类型字段匹配
    if payload.record_type == "充值":
        if payload.income is None or payload.income <= 0 or payload.expense is not None:
            raise BalanceTypeFieldMismatchError(...)
    elif payload.record_type in ("推广支出", "刷拍单支出"):
        if payload.expense is None or payload.expense <= 0 or payload.income is not None:
            raise BalanceTypeFieldMismatchError(...)
    prev = await self._repo.last_balance(user.tenant_id)  # Decimal('0') if none
    balance_after = prev + (payload.income or 0) - (payload.expense or 0)
    if payload.expected_balance is not None and payload.expected_balance != balance_after:
        raise BalanceMismatchError(...)
    # insert balance_after + 审计 + commit
```

---

## 5. ROI 隔离（aggregate_by_style 增量）

```sql
-- exclude_brushing=true 时，pay_amount 减去刷单金额
COALESCE(SUM(q.pay_amount), 0)
  - COALESCE((
      SELECT SUM(oa.amount) FROM order_adjustment oa
      WHERE oa.tenant_id = :tenant_id AND oa.style_id = s.id
        AND oa.order_type = '刷单' AND oa.exclude_from_roi = true
        AND oa.order_date BETWEEN :date_from AND :date_to
    ), 0) AS pay_amount
```
- exclude_brushing=false 时不含该减项（保留 V1 含刷单口径）。
- production_service 默认 exclude_brushing=true。

---

## 6. 指标 + migration 020

```python
order_adjustment_auto_created_total = Counter(
    "order_adjustment_auto_created_total",
    "Total auto-created store orders from promotion (U16)",
    labelnames=("result",),  # created/skipped/failed
)
```

```python
revision = "020_u16_order_adjustment_balance"
down_revision = "019_u15_wecom_alert_tables"
# order_adjustment（base + 13 字段 + UNIQUE promotion_id partial + idx + CHECK）
# balance_record（base + record_date/type/income/expense/balance_after/remark/created_by + idx）
# ALTER promotion ADD in_store_order Boolean NOT NULL DEFAULT false
# enable_rls 两表；seed finance.order/balance:read/write（finance 显式 + admin 通配）
```
- revision id `"020_u16_order_adjustment_balance"`（30 字符 ≤ 32）。

---

## 7. 测试落点

| 文件 | 重点 |
|---|---|
| `tests/unit/test_order_amount_balance.py` | parse_amount_expr + balance 计算/类型匹配纯逻辑 |
| `tests/integration/test_order_adjustment.py` | 自动拍单 listener 幂等 + 刷单 + ROI 剔除 + 余额校验 + RLS |
| `tests/api/test_order_adjustment_api.py` | brushing + balance-records 401 + OpenAPI |

- 本地 Docker PG16:5559 + Redis7:6414 + Py3.12（U16 唯一端口）。
