# U14 技术栈决策（Tech Stack Decisions）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 增量式：零新增依赖，复用 U01/U08/U13

---

## 1. 依赖决策

| 能力 | 选型 | 状态 |
|---|---|---|
| 聚合查询 | SQLAlchemy 2.0 text() 原生 SQL | 复用（同 U08） |
| 除零 | services/metric/common.safe_div | 复用 U08 |
| 时间筛选 | report/domain.resolve_time_range | 复用 U08 |
| 点赞折算 | services/metric/publish_progress.like_sum_expr | 复用 U08 |
| 催发状态 | promotion.urge_calculator.URGE_STATUS_SQL_EXPR | 复用 U04 |
| 指标 | prometheus（core/metrics.py） | 复用，+1 histogram |

> **结论：requirements.txt 不变。**

---

## 2. 代码落点

### modules/report/（追加）

| 文件 | 职责 | 新建/修改 |
|---|---|---|
| `work_progress_models.py` | TargetPlanning / StoreDaily ORM | 新建 |
| `work_progress_service.py` | WorkProgressService.get_for_month | 新建 |
| `target_planning_service.py` | TargetPlanningService.set_target/list_with_actuals | 新建 |
| `store_daily_service.py` | StoreDailyService.get_dashboard/upsert_manual | 新建 |
| `production_service.py` | ProductionService.get_report（含周环比 + exclude_brushing 占位） | 新建 |
| `advanced_repository.py` | 4 报表聚合 SQL（work_progress/target/store_daily/production） | 新建 |
| `advanced_schemas.py` | PrWorkProgress/TargetCreate/TargetWithActual/StoreDailyRow/StoreDailyManualUpdate/ProductionRow/ProductionReport | 新建 |
| `advanced_api.py` | 6 端点 router | 新建 |
| `deps.py` | +4 service deps | 修改 |
| `permissions.py` | +report.target/store_daily/work_progress/production scope | 新建/追加 |

### services/metric/（追加）

| 文件 | 职责 |
|---|---|
| `work_progress.py` | HIT_STAT_THRESHOLD=500 + 工作进度 SQL 片段 |
| `store_daily.py` | 店铺聚合 SQL 片段 |
| `style_roi.py` | return_rate / add_to_cart_cost / net_roi / unit_deal_cost（safe_div + exclude_brushing 占位形参） |

### 修改

| 组件 | 改动 |
|---|---|
| `core/metrics.py` | +report_query_duration_seconds histogram |
| `app/main.py` | 注册 advanced_api router（report 模块已注册，追加新 router 或并入） |
| `core/celery_app.py` | report 队列 + precompute_report_cache 占位（注释/可选） |
| `tasks/report_tasks.py` | precompute_report_cache 占位任务（V1 不强制） |
| `alembic/versions/018_u14_create_report_tables.py` | target_planning + store_daily + scope seed |

---

## 3. style_roi metric（exclude_brushing 占位）

```python
# services/metric/style_roi.py
def net_roi(confirmed_amount, total_spend, *, exclude_brushing: bool = False):
    # exclude_brushing 占位（V1 默认 False 不影响；U16 启用剔除 order_adjustment）
    return safe_div(confirmed_amount, total_spend, quantize=Decimal("0.0001"))

def return_rate(refund_amount, pay_amount):
    return safe_div(refund_amount, pay_amount, quantize=Decimal("0.0001"))

def add_to_cart_cost(total_spend, add_cart_count):
    return safe_div(total_spend, add_cart_count, quantize=Decimal("0.0001"))
```

---

## 4. HIT_STAT_THRESHOLD（work_progress 常量）

```python
# services/metric/work_progress.py
HIT_STAT_THRESHOLD = 500
"""工作进度表爆文统计阈值（≠ 爆文标记阈值 1000）。V1+ system_setting 可配。"""
```

---

## 5. 指标

```python
report_query_duration_seconds = Histogram(
    "report_query_duration_seconds",
    "Duration of advanced report aggregation queries",
    labelnames=("report_type",),  # work_progress/target/store_daily/production
    buckets=(0.05, 0.1, 0.3, 0.5, 0.8, 2.0),
)
```

---

## 6. migration 018 片段

```python
revision = "018_u14_create_report_tables"
down_revision = "017_u13_create_crawler_tables"

# target_planning：UNIQUE(tenant,pr_id,style_id,period_month) + FK user/style + CHECK min_target>=0
# store_daily：UNIQUE(tenant,date) + 手动 3 字段
# RLS + scope seed:
#   report.target:read/write → pr_manager（write）+ report.*:read 通配（read）
#   report.store_daily:read/write → operations（write）
#   report.work_progress:read / report.production:read（通配已覆盖，显式 seed 便于发现）
```

---

## 7. 测试落点

| 文件 | 类型 |
|---|---|
| tests/unit/test_style_roi.py | 5 公式 safe_div 边界 + exclude_brushing 占位 |
| tests/integration/test_advanced_reports.py | 工作进度/爆款约篇/店铺/投产 端到端 + 周环比 + RLS |
| tests/api/test_advanced_report_api.py | 6 端点鉴权 + OpenAPI |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| modules/report 追加 + services/metric 3 子模块 | ✅ |
| exclude_brushing 占位 | ✅ |
| HIT_STAT_THRESHOLD 500 | ✅ |
| report_query_duration 指标 | ✅ |
| migration 018 2 表 + scope | ✅ |
| 测试 3 文件 | ✅ |
| 与 nfr-requirements 一致 | ✅ |
