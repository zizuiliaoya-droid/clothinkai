# U08 代码生成说明（发文进度看板）

> 单元：U08 — 发文进度看板（MVP 最后一个单元，EP09-S01 + S07）
> 节奏：单批生成（纯读聚合层；无 migration / 无新表 / 无新依赖）

---

## 1. 交付物清单

### 新建 — services/metric/
- `__init__.py` / `common.py`（safe_div）/ `publish_progress.py`（like_sum_expr 折算 CASE）
- `app/services/__init__.py`（新建包）

### 新建 — modules/report/（9 文件）
__init__ / exceptions（3 异常）/ permissions / domain（resolve_time_range + level 着色）/
schemas（5 读模型）/ repository（PublishProgressRepository 4 聚合 + style_exists）/
service（PublishProgressService）/ deps / api（4 GET 端点）

### 新建 — 测试（4 文件）
- `tests/unit/test_metric_common.py`（5）+ `tests/unit/test_report_domain.py`（11）
- `tests/integration/test_publish_progress.py`（5）+ `tests/api/test_report_api.py`（5）

### 修改
- `main.py`（注册 report_router）

**未改动**：migration / config / metrics / celery_app / default_roles（权限已 seed）。

---

## 2. 设计守护落地（P-U08-01~03）

| 守护 | 落地 |
|---|---|
| P-U08-01 TimeRange 解析 + 编排 | domain.resolve_time_range（5 preset + custom≤366）+ service |
| P-U08-02 聚合 SQL FILTER+CASE+URGE_EXPR | repository 4 方法 + **显式 tenant_id 过滤**（防御层，同 U04 list_with_cte） |
| P-U08-03 safe_div null + level 着色 | services/metric/common + domain.level_* |
| 折算系数复用 U04 | like_sum_expr 动态拼 CASE（PLATFORM_LIKE_COEFFICIENT） |

---

## 3. 关键语义
- **纯读聚合层**：无写 / 无事务 / 不触发事件 / 无新表。
- **tenant_id 显式过滤**：repository 所有聚合 `WHERE tenant_id=:tid`（RLS 之外防御层，保证 bypass-role 测试与生产都隔离正确）。
- **9 汇总指标**：约篇量/金额、合作金额、发布量/率、超时量/率、点赞量（折算）、CPL、取消量。
- **分母 0→null**：safe_div 全程（前端"—"）；计数/金额 SQL COALESCE 归零。
- **超时量**：复用 U04 URGE_STATUS_SQL_EXPR（:today/:urge_days/:important_days）。
- **点赞折算**：抖音/快手 ×0.1（系数来自 U04 PLATFORM_LIKE_COEFFICIENT）。

---

## 4. 故事覆盖

| 故事 | 实施 |
|---|---|
| EP09-S01 Layer1 汇总 | service.get_summary + repo.aggregate_summary |
| EP09-S01 Layer2 卡片 | service.get_cards + repo.aggregate_cards（GROUP BY style） |
| EP09-S01 Layer3 PR/趋势 | get_detail_by_pr（GROUP pr）/ get_detail_by_time（半月 bucket） |
| EP09-S01 分母 0→— | safe_div |
| EP09-S07 时间筛选 | domain.resolve_time_range（5 preset + custom） |

---

## 5. 验证
- 全部新文件诊断器无警告。
- Build & Test：638 passed / 0 failed / 79.73%（见 test-coverage.md）。
