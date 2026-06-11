# U08 NFR 设计模式（NFR Design Patterns）

> 单元：U08 — 发文进度看板
> 范围：3 个增量模式 P-U08-01~03；其余继承 U01-U07
> 关键：纯读聚合，无新表/无写/无事务边界问题

---

## 0. 继承声明

| 模式 | 来源 | 依赖点 |
|---|---|---|
| RLS + ORM 钩子（只读 app 引擎） | U01 | 多租户隔离 |
| URGE_STATUS_SQL_EXPR + get_today | U04 | 超时量 + 统一日期（:today） |
| PLATFORM_LIKE_COEFFICIENT | U04 legacy_settings | 点赞折算 CASE |
| @require_permission | U01 | report.publish_progress:read |

---

## P-U08-01：TimeRange 解析 + 聚合编排

### 方案
```python
# modules/report/domain.py
from datetime import date, timedelta
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.exceptions import (
    ReportInvalidTimePresetError, ReportInvalidTimeRangeError)

_MAX_SPAN_DAYS = 366

def resolve_time_range(preset, date_from=None, date_to=None) -> tuple[date, date]:
    today = get_today()  # Asia/Shanghai（FB8）
    if preset == "last_7d":    return today - timedelta(days=6), today
    if preset == "last_30d":   return today - timedelta(days=29), today
    if preset == "this_month": return today.replace(day=1), today
    if preset == "last_month":
        first = today.replace(day=1)
        prev_last = first - timedelta(days=1)
        return prev_last.replace(day=1), prev_last
    if preset == "custom":
        if not date_from or not date_to or date_from > date_to:
            raise ReportInvalidTimeRangeError()
        if (date_to - date_from).days > _MAX_SPAN_DAYS:
            raise ReportInvalidTimeRangeError()
        return date_from, date_to
    raise ReportInvalidTimePresetError()

# modules/report/service.py
class PublishProgressService:
    _URGE_DAYS, _IMPORTANT_DAYS = 10, 3

    def __init__(self, session): self._repo = PublishProgressRepository(session)

    async def get_summary(self, tr) -> ProgressSummary:
        row = await self._repo.aggregate_summary(
            date_from=tr[0], date_to=tr[1], today=get_today(),
            urge_days=self._URGE_DAYS, important_days=self._IMPORTANT_DAYS)
        return _build_summary(row)   # safe_div + level（P-U08-03）
```

### 关键点
- 统一 `get_today()`（与 URGE_EXPR 的 :today 同源，FB8）。
- service 仅编排：解析 → repo 聚合 → safe_div 组装；无写/事务。

---

## P-U08-02：聚合 SQL（FILTER + CASE 折算 + URGE_EXPR）

### summary（单条，无 GROUP BY）
```python
# modules/report/repository.py
from app.modules.promotion.urge_calculator import URGE_STATUS_SQL_EXPR
from app.modules.promotion.legacy_settings import PLATFORM_LIKE_COEFFICIENT

# 折算 CASE 由系数<1 的平台动态拼（避免硬编码漂移）
_DISCOUNT = [p for p, c in PLATFORM_LIKE_COEFFICIENT.items() if c < 1]
# 例：CASE WHEN platform IN ('抖音','快手') THEN like_count*0.1 ELSE like_count END
def _like_expr() -> str:
    if not _DISCOUNT:
        return "COALESCE(like_count,0)"
    plats = ",".join(f"'{p}'" for p in _DISCOUNT)
    coef = float(PLATFORM_LIKE_COEFFICIENT[_DISCOUNT[0]])
    return (f"COALESCE(SUM(CASE WHEN platform IN ({plats}) "
            f"THEN like_count*{coef} ELSE like_count END),0)")

class PublishProgressRepository:
    def __init__(self, session): self._s = session

    async def aggregate_summary(self, *, date_from, date_to, today,
                                urge_days, important_days):
        sql = text(f"""
            SELECT
              COUNT(*) AS quote_count,
              COALESCE(SUM(quote_amount),0) AS quote_amount,
              COALESCE(SUM(quote_amount) FILTER (WHERE publish_status='已发布'),0)
                 AS cooperation_amount,
              COUNT(*) FILTER (WHERE publish_status='已发布') AS publish_count,
              COUNT(*) FILTER (WHERE publish_status='已取消') AS cancel_count,
              COUNT(*) FILTER (WHERE ({URGE_STATUS_SQL_EXPR})='超时') AS overdue_count,
              {_like_expr()} AS like_count
            FROM promotion
            WHERE is_active = true
              AND cooperation_date BETWEEN :date_from AND :date_to
        """)
        return (await self._s.execute(sql, {
            "date_from": date_from, "date_to": date_to,
            "today": today, "urge_days": urge_days,
            "important_days": important_days,
        })).mappings().one()
```
> RLS 自动注入 tenant_id（app 引擎），WHERE 不显式写 tenant_id。

### cards（GROUP BY style_id + JOIN style，分页）
```python
    async def aggregate_cards(self, *, date_from, date_to, today,
                              urge_days, important_days, page, page_size):
        # 内层聚合（无表别名，保证 URGE_EXPR 列名不歧义）+ 外层 JOIN style
        base = f"""
            FROM promotion p JOIN style s ON s.id = p.style_id
            WHERE p.is_active = true
              AND p.cooperation_date BETWEEN :date_from AND :date_to
        """
        # 注：URGE_EXPR 用裸列名 → 子查询内先 SELECT p.* 再算，或直接限定 p. 前缀。
        # 实现时用 p.publish_status / p.scheduled_publish_date 限定版 URGE 表达式。
        ...  # SELECT p.style_id, s.style_code, s.style_name, s.main_image_id,
             # COUNT/SUM/FILTER..., GROUP BY p.style_id, s.*  ORDER BY quote_count DESC
             # LIMIT :ps OFFSET :off ;  + 单独 count(distinct style_id) 求 total
```

### detail_by_pr / detail_by_time
```python
    async def aggregate_by_pr(self, *, style_id, date_from, date_to, ...):
        # WHERE p.style_id=:sid ... GROUP BY p.pr_id, u.display_name, u.username
        # LEFT JOIN "user" u ON u.id=p.pr_id ; pr_name=COALESCE(display_name,username,'未分配')
    async def aggregate_by_half_month(self, *, style_id, date_from, date_to, ...):
        bucket = ("to_char(cooperation_date,'YYYY-MM') || "
                  "(CASE WHEN extract(day FROM cooperation_date)<=15 "
                  "THEN ' 上半月' ELSE ' 下半月' END)")
        # GROUP BY bucket, MIN(cooperation_date) ORDER BY MIN(cooperation_date)
```

### 关键点
- PostgreSQL `FILTER (WHERE ...)` 做条件聚合，单查询出全部计数。
- 折算 CASE 系数来自 U04 常量（拼字符串，非硬编码）。
- cards 的 URGE_EXPR 用 `p.` 限定列名版本（JOIN 下防歧义）；summary 无 JOIN 用裸列名版本。
- 计数/金额 COALESCE 归零；比率/CPL 不在 SQL 算。

---

## P-U08-03：safe_div null 后处理 + level 着色

### 方案
```python
# services/metric/common.py
def safe_div(numerator, denominator, *, quantize=None):
    if denominator in (None, 0, Decimal(0)) or numerator is None:
        return None
    r = Decimal(str(numerator)) / Decimal(str(denominator))
    return r.quantize(quantize) if quantize is not None else r

# modules/report/service.py（组装）
_Q4 = Decimal("0.0001")
def _build_summary(row) -> ProgressSummary:
    quote = row["quote_count"]
    publish_rate = safe_div(row["publish_count"], quote, quantize=_Q4)
    overdue_rate = safe_div(row["overdue_count"], quote, quantize=_Q4)
    cpl = safe_div(row["cooperation_amount"], row["like_count"], quantize=_Q4)
    return ProgressSummary(
        quote_count=quote, quote_amount=row["quote_amount"],
        cooperation_amount=row["cooperation_amount"],
        publish_count=row["publish_count"], publish_rate=publish_rate,
        publish_rate_level=_level_publish(publish_rate),
        overdue_count=row["overdue_count"], overdue_rate=overdue_rate,
        overdue_rate_level=_level_overdue(overdue_rate),
        like_count=int(row["like_count"]), cpl=cpl,
        cancel_count=row["cancel_count"])

def _level_publish(r):  # BR-U08-32
    if r is None: return None
    return "green" if r >= Decimal("0.8") else "yellow" if r >= Decimal("0.5") else "red"
def _level_overdue(r):
    if r is None: return None
    return "green" if r <= Decimal("0.1") else "yellow" if r <= Decimal("0.3") else "red"
```

### 关键点
- 分母 0/None → None（前端"—"）；level 同步 None。
- 比率 quantize 4 位（与 U04 CPL 精度一致）。

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| TimeRange 5 preset + custom 边界（≤366 天） | ✅ P-U08-01 |
| 统一 get_today（FB8，与 URGE_EXPR :today 同源） | ✅ P-U08-01/02 |
| FILTER + CASE 单查询聚合 | ✅ P-U08-02 |
| 折算系数来自 U04 常量（非硬编码） | ✅ P-U08-02 |
| 只读 + RLS 自动隔离（无显式 tenant_id WHERE） | ✅ P-U08-02 |
| 分母 0 → null + level None | ✅ P-U08-03 |
| 无写 / 无事务 / 无新表 | ✅ |
