# U08 代码生成计划（Code Generation Plan）

> 单元：U08 — 发文进度看板（MVP 最后一个单元）
> 节奏：**单批生成**（纯读聚合层；无 migration / 无新表 / 无新依赖）
> 依赖：U04（promotion + URGE_STATUS_SQL_EXPR + PLATFORM_LIKE_COEFFICIENT）+ U02（style）+ U01（RLS/权限）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP09-S01（三层看板）+ EP09-S07（时间筛选）

### 1.2 设计守护（P-U08-01~03）

| 守护 | 落地 |
|---|---|
| P-U08-01 TimeRange 解析 + 编排 | domain.resolve_time_range（5 preset + custom≤366）+ service |
| P-U08-02 聚合 SQL FILTER+CASE+URGE_EXPR | repository 4 方法；只读 RLS 自动隔离 |
| P-U08-03 safe_div null + level 着色 | services/metric/common + domain.level |
| 复用 U04 折算系数 | _like_expr 动态拼 CASE |

### 1.3 项目结构
```
backend/app/services/metric/__init__.py            # 新建
backend/app/services/metric/common.py              # 新建（safe_div）
backend/app/services/metric/publish_progress.py    # 新建（折算辅助占位）
backend/app/modules/report/__init__.py             # 新建
backend/app/modules/report/exceptions.py           # 新建
backend/app/modules/report/permissions.py          # 新建
backend/app/modules/report/domain.py               # 新建（resolve_time_range + level）
backend/app/modules/report/schemas.py              # 新建
backend/app/modules/report/repository.py           # 新建（4 聚合 SQL）
backend/app/modules/report/service.py              # 新建
backend/app/modules/report/deps.py                 # 新建
backend/app/modules/report/api.py                  # 新建（4 GET）
backend/app/main.py                                # 修改（注册 report_router）
backend/tests/unit/test_metric_common.py           # 新建
backend/tests/unit/test_report_domain.py           # 新建
backend/tests/integration/test_publish_progress.py # 新建
backend/tests/api/test_report_api.py               # 新建
aidlc-docs/construction/U08/code/{README,api-endpoints,test-coverage}.md
# 不改：migration / config / metrics / celery_app / default_roles
```

---

## 2. 执行步骤（单批）

### Step 1 — services/metric（3 文件）
- [x] 1.1 `services/metric/{__init__,common(safe_div),publish_progress}.py`

### Step 2 — modules/report（9 文件）
- [x] 2.1 `__init__` + `exceptions`（3 异常）+ `permissions` + `domain`（resolve_time_range + level）
- [x] 2.2 `schemas`（TimeRange + 5 读模型）+ `repository`（4 聚合）+ `service` + `deps` + `api`（4 GET）

### Step 3 — 注册 + 测试（5 文件）
- [x] 3.1 `main.py` 注册 report_router
- [x] 3.2 `test_metric_common`（safe_div）+ `test_report_domain`（TimeRange + level）
- [x] 3.3 `test_publish_progress`（聚合数值 + 空集 null + 多租户）+ `test_report_api`（鉴权 + OpenAPI + 422）

### Step 4 — 文档 + 完成校验
- [x] 4.1 `U08/code/{README,api-endpoints,test-coverage}.md`
- [x] 4.2 全部诊断器无警告 + Plan 全 [x]

### Step 5 — Build & Test（真实环境）
- [x] 5.1 Docker（PG16:5550 + Redis7:6405 + Py3.12）：alembic upgrade head（no-op，head=011）
- [x] 5.2 U08 子集（unit + integration + api）
- [x] 5.3 全量回归（确认 612 + U08 新增全绿）+ 覆盖率 ≥70%
- [x] 5.4 清理临时容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP09-S01 Layer1 汇总 | service.get_summary + repo.aggregate_summary | test_publish_progress::summary |
| EP09-S01 Layer2 卡片 | service.get_cards + repo.aggregate_cards | test_publish_progress::cards |
| EP09-S01 Layer3 PR/趋势 | get_detail_by_pr/by_time | test_publish_progress::detail |
| EP09-S01 分母 0→— | safe_div | test_metric_common + test_publish_progress::empty |
| EP09-S07 时间筛选 | domain.resolve_time_range | test_report_domain |

---

## 4. 节奏决策
单批：纯读聚合层，无 migration / 无新依赖 / 无写。

---

**等待用户回复"继续"批准单批节奏，开始 U08 代码生成。**
