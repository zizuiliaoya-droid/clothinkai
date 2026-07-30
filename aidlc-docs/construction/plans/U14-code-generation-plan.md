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

## 2. 交付后增量 TASK 14 — 时间粒度补齐（已批准）

> 范围：店铺数据补充按年聚合；投产趋势支持 day/week/month/year，周起点固定为周一；两页复用统一时间范围筛选。
> 兼容：趋势 API 默认 `day`；不生成无数据零值桶；不改变现有投产口径；无数据库迁移。

### Increment 14.1 — 计划与设计约束
- [x] 14.1.1 读取现有前后端调用链、相关测试、AI-DLC 状态与审计记录。
- [x] 14.1.2 执行 ui-ux-pro-max 设计系统与 React 栈检索；保持 Ant Design 亮色后台一致性，采纳可访问标签、焦点、响应式与无布局位移规则。
- [x] 14.1.3 将已批准增量范围、兼容约束和验证步骤追加到 U14 单一事实源计划。

### Increment 14.2 — 前端共享时间筛选与店铺聚合
- [x] 14.2.1 新建 `ReportTimeRangeFilter`，统一时间预设、自定义日期区间和请求启用条件。
- [x] 14.2.2 `StoreDailyPage` 复用共享筛选，增加按年选项，修复周一分桶的时区风险。
- [x] 14.2.3 店铺非日粒度聚合深复制 `extra`，避免修改 React Query 原始缓存。

### Increment 14.3 — 投产趋势四粒度
- [x] 14.3.1 后端 API/Service/Repository 增加 `day|week|month|year`，使用固定 SQL 映射并保持默认 day。
- [x] 14.3.2 前端类型/API/queryKey/Modal 增加趋势粒度并修复 custom 日期未完成时仍请求的问题。
- [x] 14.3.3 按粒度格式化图表标签，轻量 SVG 图表完整展示调用方标签。

### Increment 14.4 — 本地验证
- [x] 14.4.1 对所有改动文件执行 diagnostics，修复发现的问题。
- [x] 14.4.2 执行 frontend build、Python compileall、migration null-byte/AST 回归、`git diff --check`。
- [x] 14.4.3 运行现有相关 pytest；本机在加载 `tests/conftest.py` 时缺少 `boto3`，按约束记录环境阻断且未安装依赖、未新增测试文件。

### Increment 14.5 — 发布、线上验收与清理
- [x] 14.5.1 提交 `a9c45ab` 并推送 main；Zeabur frontend `6a6b568a9cd65e28a342e31e`、backend `6a6b568d9cd65e28a342e31f` 均 RUNNING，`/ready` 的 DB/Redis 均为 ok。
- [x] 14.5.2 使用上一任务真实登录表单建立且未写入 localStorage token 的认证会话完成验收：店铺日/周/月/年可切换，PRODX 趋势四粒度均 200；最终控制台 0 error，runtime logs 为空。
- [x] 14.5.3 本轮未新增测试数据，无需清理；已更新 `aidlc-state.md`，并仅追加 `audit.md` 后提交收尾文档。

---

**原始 U14 三批与 Build & Test 已完成；当前执行已批准的交付后增量 TASK 14。**
