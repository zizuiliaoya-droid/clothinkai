# U14 代码生成计划（Code Generation Plan）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表（EP09-S02~S05）
> 分批：**3 批** + Build & Test
> Build & Test：Docker PG16:5557 + Redis7:6412 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 复用 modules/report，追加 work_progress_models/advanced_schemas/advanced_repository/4 service/advanced_api/advanced_permissions；services/metric 追加 work_progress/store_daily/style_roi。
- [Answer] 2 新表 target_planning/store_daily（TenantScopedModel + RLS）。
- [Answer] 全部聚合用 text() 原生 SQL，比率 service 层 safe_div；时间筛选复用 resolve_time_range。
- [Answer] 投产子查询预聚合防笛卡尔积 + extra JSONB COALESCE + 周环比等长上期。
- [Answer] exclude_brushing V1 占位透传不改 SQL；style_roi 形参占位。
- [Answer] core/metrics +report_query_duration_seconds；celery_app report 队列 + precompute 占位；main 注册 advanced_api。
- [Answer] migration 018 + conftest import + 3 测试。

---

## 1. 步骤（3 批）

### Batch 1 — 模型 + Schema + Permissions + Metric 子模块
- [x] 1.1 modules/report/work_progress_models.py（TargetPlanning + StoreDaily ORM）
- [x] 1.2 modules/report/advanced_schemas.py（7 schema）
- [x] 1.3 modules/report/advanced_permissions.py（scope 常量）
- [x] 1.4 services/metric/work_progress.py（HIT_STAT_THRESHOLD=500）+ store_daily.py + style_roi.py（5 公式 safe_div + exclude_brushing 占位）

### Batch 2 — Repository + Service + Deps
- [x] 2.1 modules/report/advanced_repository.py（4 报表聚合 SQL）
- [x] 2.2 modules/report/work_progress_service.py + target_planning_service.py + store_daily_service.py + production_service.py
- [x] 2.3 modules/report/deps.py 追加 4 service deps

### Batch 3 — API + 横切 + migration + 测试
- [x] 3.1 modules/report/advanced_api.py（6 端点）
- [x] 3.2 core/metrics.py +report_query_duration_seconds + celery_app report 队列+precompute 占位 + tasks/report_tasks.py 占位 + main 注册 advanced_router
- [x] 3.3 alembic/versions/018_u14_create_report_tables.py
- [x] 3.4 conftest 追加 report models import + tests/unit/test_style_roi.py + tests/integration/test_advanced_reports.py + tests/api/test_advanced_report_api.py

### Build & Test
- [x] B.1 Docker PG16:5557 + Redis7:6412；alembic upgrade head（含 018）；U14 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 3 批 + Build & Test。**
