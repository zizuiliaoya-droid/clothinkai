# U08 技术栈决策（Tech Stack Decisions）

> 单元：U08 — 发文进度看板
> 原则：复用 U01-U07 技术栈，**零新增运行时依赖 / 零新表 / 零 migration**

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 状态 |
|---|---|---|
| 聚合查询 | SQLAlchemy core（text / select + func） | ✅ 已有 |
| 读模型 | Pydantic 2.x | ✅ 已有 |
| 日期 | stdlib datetime + zoneinfo（复用 U04 get_today） | ✅ 已有 |

> requirements.txt / requirements-dev.txt **不改动**。

---

## 2. safe_div（services/metric/common.py）

```python
from decimal import Decimal

def safe_div(numerator, denominator, *, quantize=None):
    """分母 0 / None → None（前端"—"）。"""
    if denominator in (None, 0, Decimal(0)) or numerator is None:
        return None
    result = Decimal(str(numerator)) / Decimal(str(denominator))
    return result.quantize(quantize) if quantize is not None else result
```

- 比率（发布率 / 超时率）quantize 到 4 位；CPL quantize 到 4 位（与 U04 metrics_calculator 一致）。

---

## 3. TimeRange 解析（modules/report/domain.py 或 schemas）

```python
from datetime import date, timedelta
from app.modules.promotion.urge_calculator import get_today

def resolve_time_range(preset, date_from=None, date_to=None) -> tuple[date, date]:
    today = get_today()  # Asia/Shanghai
    if preset == "last_7d":   return today - timedelta(days=6), today
    if preset == "last_30d":  return today - timedelta(days=29), today
    if preset == "this_month":return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if preset == "custom":
        if not date_from or not date_to or date_from > date_to:
            raise ReportInvalidTimeRangeError()
        if (date_to - date_from).days > 366:
            raise ReportInvalidTimeRangeError()
        return date_from, date_to
    raise ReportInvalidTimePresetError()
```

---

## 4. 聚合 SQL 模式（PostgreSQL FILTER + CASE）

```sql
-- summary（单条，无 GROUP BY）
SELECT
  COUNT(*)                                              AS quote_count,
  COALESCE(SUM(quote_amount),0)                         AS quote_amount,
  COALESCE(SUM(quote_amount) FILTER (WHERE publish_status='已发布'),0) AS cooperation_amount,
  COUNT(*) FILTER (WHERE publish_status='已发布')        AS publish_count,
  COUNT(*) FILTER (WHERE publish_status='已取消')        AS cancel_count,
  COUNT(*) FILTER (WHERE ({URGE_EXPR})='超时')           AS overdue_count,
  COALESCE(SUM(CASE WHEN platform IN ('抖音','快手')
                    THEN like_count*0.1 ELSE like_count END),0) AS like_count
FROM promotion
WHERE tenant_id=:tid AND is_active = true
  AND cooperation_date BETWEEN :date_from AND :date_to
```

- 比率 / CPL 不在 SQL 算，取回计数后 Python `safe_div`（统一 null 语义）。
- `URGE_EXPR` = U04 `URGE_STATUS_SQL_EXPR`（注入 :today / :urge_days / :important_days）。
- 折算系数平台列表 + 系数来源 U04 `PLATFORM_LIKE_COEFFICIENT`（代码生成时由常量拼 CASE，避免硬编码漂移）。

---

## 5. 索引复用

- 复用 U04 promotion 索引：`(tenant_id, cooperation_date)` 类 + `style_id` + `pr_id`。
- MVP 不新增索引。Build & Test 若 cards GROUP BY style_id 慢 → 评估 `idx_promotion_tenant_coop_style (tenant_id, cooperation_date, style_id)`（记录，不强制）。

---

## 6. 监控

- 无新增自定义 Prometheus 指标；HTTP 时延由 instrumentator 自动暴露（/api/reports/* handler 分组）。
- structlog 记 tenant_id / preset / date 范围 / 耗时。

---

## 7. 模块落点（无新表）

```
backend/app/modules/report/__init__.py
backend/app/modules/report/exceptions.py        # ReportInvalidTimePreset/Range + StyleNotFound
backend/app/modules/report/schemas.py           # TimeRange + 5 读模型
backend/app/modules/report/repository.py        # PublishProgressRepository（4 聚合 SQL）
backend/app/modules/report/service.py           # PublishProgressService
backend/app/modules/report/deps.py
backend/app/modules/report/api.py               # 4 端点 /api/reports/publish-progress/*
backend/app/services/metric/__init__.py
backend/app/services/metric/common.py           # safe_div
backend/app/services/metric/publish_progress.py # 聚合辅助（可选，或并入 repository）
backend/app/main.py                             # 注册 report_router（修改）
```

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1 |
| safe_div null 安全 | ✅ §2 |
| TimeRange 5 preset + 边界 | ✅ §3 |
| 聚合 FILTER/CASE + Python safe_div | ✅ §4 |
| 复用索引无新增 | ✅ §5 |
| 无新增指标 | ✅ §6 |
| 无新表 / migration | ✅ §7 |
