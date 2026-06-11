# U14 逻辑组件（Logical Components）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> modules/report 追加 9 文件 + services/metric 3 子模块 + 横切 4 改动 + migration 018

---

## 1. 新建组件（modules/report/，追加）

| 文件 | 职责 |
|---|---|
| `work_progress_models.py` | TargetPlanning + StoreDaily ORM |
| `advanced_schemas.py` | PrWorkProgress/TargetCreate/TargetWithActual/StoreDailyRow/StoreDailyManualUpdate/ProductionRow/ProductionReport |
| `advanced_repository.py` | WorkProgressRepo/TargetRepo/StoreDailyRepo/ProductionRepo 聚合 SQL |
| `work_progress_service.py` | WorkProgressService.get_for_month |
| `target_planning_service.py` | TargetPlanningService.set_target/list_with_actuals |
| `store_daily_service.py` | StoreDailyService.get_dashboard/upsert_manual |
| `production_service.py` | ProductionService.get_report（周环比 + exclude_brushing 占位） |
| `advanced_api.py` | 6 端点 router（/api/reports/work-progress|targets|store-daily|production） |
| `advanced_permissions.py` | report.target/store_daily/work_progress/production scope 常量 |

## 2. 新建组件（services/metric/，追加）

| 文件 | 职责 |
|---|---|
| `work_progress.py` | HIT_STAT_THRESHOLD=500 + 工作进度 FILTER/like SQL 片段 |
| `store_daily.py` | 店铺聚合 SQL 片段 |
| `style_roi.py` | return_rate/add_to_cart_cost/net_roi/unit_deal_cost（safe_div + exclude_brushing 占位形参） |

## 3. 修改组件

| 组件 | 改动 |
|---|---|
| `modules/report/deps.py` | +4 service deps |
| `core/metrics.py` | +report_query_duration_seconds histogram |
| `app/main.py` | 注册 advanced_api router |
| `core/celery_app.py` | report 队列 + precompute_report_cache 占位（注释/可选 Beat） |
| `tasks/report_tasks.py` | precompute_report_cache 占位任务（V1 不强制启用） |
| `alembic/versions/018_u14_create_report_tables.py` | target_planning + store_daily + 6 scope seed |

## 4. 复用组件

| 复用 | 来源 |
|---|---|
| resolve_time_range（domain） | U08 report |
| safe_div（metric/common） | U08 |
| like_sum_expr（metric/publish_progress） | U08 |
| URGE_STATUS_SQL_EXPR | U04 promotion |
| qianniu_daily / ad_daily | U13 |
| platform_product 关联 | U10b |
| settlement | U05 |
| TenantScopedModel + RLS + require_permission | U01 |

## 5. 依赖图

```
report/advanced_api (6 端点)
  → WorkProgressService → advanced_repository → promotion(U04)
  → TargetPlanningService → advanced_repository → target_planning + promotion
  → StoreDailyService → advanced_repository → qianniu_daily(U13) + store_daily
  → ProductionService → advanced_repository → qianniu_daily + ad_daily(U13) + promotion + platform_product(U10b)
                      → services/metric/style_roi (safe_div 5 公式)
  全部 → resolve_time_range(U08) + safe_div(U08) + report_query_duration(metrics)
```
- 无循环依赖：report 聚合层单向依赖 U04/U05/U13/U10b/U08/U01。

## 6. migration 018

```text
target_planning：UNIQUE(tenant,pr_id,style_id,period_month) + FK user RESTRICT/style CASCADE
                 + CHECK min_target>=0 + idx(tenant,period_month) + RLS
store_daily：UNIQUE(tenant,date) + 手动 3 字段(ad_spend_total/zhitongche_spend/yinli_spend) + remark + RLS
scope seed（ON CONFLICT DO NOTHING）：
  report.work_progress:read / report.production:read / report.target:read / report.store_daily:read → 通配已覆盖 operations/pr_manager，显式 seed 便于发现
  report.target:write → pr_manager（+admin "*"）
  report.store_daily:write → operations（+admin "*"）
downgrade：DROP 2 表 + DELETE scope
```

## 7. 测试文件

| 文件 | 类型 |
|---|---|
| tests/unit/test_style_roi.py | 5 公式 safe_div 边界 + exclude_brushing 占位 |
| tests/integration/test_advanced_reports.py | 工作进度/爆款约篇 set+list/店铺聚合+手动 upsert/投产跨表+周环比 + RLS |
| tests/api/test_advanced_report_api.py | 6 端点鉴权 + OpenAPI |

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 9（report）+ 3（metric）+ 修改 6 + migration 018 | ✅ |
| 复用 U04/U05/U08/U10b/U13/U01 | ✅ |
| 无循环依赖 | ✅ |
| 与 P-U14-01/02/03 一致 | ✅ |
