# U08 逻辑组件（Logical Components）

> 单元：U08 — 发文进度看板
> 范围：U08 新增/修改组件清单 + 依赖图 + 测试组件（**无新表 / 无 migration**）

---

## 1. 组件清单

### 1.1 新建 — modules/report/

| 文件 | 职责 | 关键内容 |
|---|---|---|
| `__init__.py` | 包标识 | — |
| `exceptions.py` | 异常 | ReportInvalidTimePresetError(422) / ReportInvalidTimeRangeError(422) / ReportStyleNotFoundError(404) |
| `permissions.py` | 权限点 | report.publish_progress:read（已存在于 default_roles，本文件做常量声明） |
| `domain.py` | 纯函数 | resolve_time_range（5 preset + custom≤366）+ level 着色（_level_publish/_level_overdue） |
| `schemas.py` | Pydantic 读模型 | TimeRangeQuery / ProgressSummary / StyleCard / StyleCardPage / PrDetail / TimeSeriesPoint |
| `repository.py` | 聚合 SQL | PublishProgressRepository（aggregate_summary / aggregate_cards / aggregate_by_pr / aggregate_by_half_month）+ _like_expr 折算 CASE |
| `service.py` | 编排 | PublishProgressService（get_summary / get_cards / get_detail_by_pr / get_detail_by_time）+ safe_div 组装 |
| `deps.py` | DI | get_publish_progress_service + 权限依赖 |
| `api.py` | HTTP | 4 GET 端点 /api/reports/publish-progress/* |

### 1.2 新建 — services/metric/

| 文件 | 职责 |
|---|---|
| `__init__.py` | 包标识 |
| `common.py` | safe_div（分母 0/None → null，可选 quantize） |
| `publish_progress.py` | 折算/装配辅助（轻量；可并入 repository，保留模块占位供 V1 work_progress/style_roi 扩展） |

### 1.3 修改 — 横切

| 文件 | 改动 |
|---|---|
| `main.py` | 注册 report_router（/api/reports 前缀已含） |

> **无 migration / 无 config / 无 metrics / 无 celery / 无 default_roles 改动**（report.publish_progress:read 在 U04/U07 已 seed 给 pr/pr_manager/operations）。

---

## 2. API 端点

| 方法 | 路径 | 权限 | service |
|---|---|---|---|
| GET | /api/reports/publish-progress/summary | report.publish_progress:read | get_summary |
| GET | /api/reports/publish-progress/cards | report.publish_progress:read | get_cards（分页） |
| GET | /api/reports/publish-progress/styles/{style_id}/by-pr | report.publish_progress:read | get_detail_by_pr |
| GET | /api/reports/publish-progress/styles/{style_id}/by-time | report.publish_progress:read | get_detail_by_time |

公共查询参数：`preset` + `date_from?` + `date_to?`（custom 必填后两者）；cards 额外 `page` + `page_size`。

---

## 3. 依赖图

```
api (4 GET) ─→ service(PublishProgressService)
                  ├─→ domain.resolve_time_range（TimeRange 解析）
                  ├─→ repository(PublishProgressRepository) ─→ promotion + style + user（聚合 SQL）
                  │        └─ 复用 URGE_STATUS_SQL_EXPR（U04）+ PLATFORM_LIKE_COEFFICIENT（U04）
                  └─→ services.metric.common.safe_div（null 安全）+ domain.level（着色）
```

依赖方向：api → service → {domain, repository, metric}；无反向依赖；无循环。

---

## 4. 复用清单

| 复用 | 来源 |
|---|---|
| URGE_STATUS_SQL_EXPR / get_today | U04 urge_calculator |
| PLATFORM_LIKE_COEFFICIENT | U04 legacy_settings |
| promotion / style / user 表 + 索引 | U04 / U02 / U01 |
| RLS + app 引擎 Session（SessionDep） | U01 |
| @require_permission / CurrentActiveUser | U01 |
| AppException + 全局 error handler | U01 |

---

## 5. 注册序列

| 时机 | 动作 |
|---|---|
| import time | 无新 ORM 模型（report 不建表，复用 promotion/style） |
| lifespan（HTTP） | include_router(report_router) |
| Celery | 无（U08 无异步任务） |

---

## 6. 测试组件

| 测试文件 | 覆盖 |
|---|---|
| `tests/unit/test_metric_common.py` | safe_div（正常 / 分母 0 / None / quantize） |
| `tests/unit/test_report_domain.py` | resolve_time_range（5 preset + custom 边界 + 跨度>366 422）+ level 着色 |
| `tests/integration/test_publish_progress.py` | 构造 promotion 数据集 → summary 9 指标断言 / cards GROUP BY style / by_pr / by_time / 空数据集 rate=null / 多租户隔离 |
| `tests/api/test_report_api.py` | 鉴权（report.publish_progress:read）/ OpenAPI / 非法 preset 422 |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 组件分层（api→service→domain/repository/metric） | ✅ §3 无循环 |
| 无新表 / migration / config / metrics / celery | ✅ §1.3 |
| 权限已 seed（不改 default_roles） | ✅ §1.3 |
| 聚合复用 U04 索引 + URGE_EXPR + 折算常量 | ✅ §4 |
| 4 GET 端点只读 + RLS | ✅ §2 |
| 测试覆盖聚合正确性 + TimeRange + null + 多租户 | ✅ §6 |
