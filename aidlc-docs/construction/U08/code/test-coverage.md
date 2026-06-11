# U08 测试覆盖（发文进度看板）

> Build & Test：Docker（PG16:5550 + Redis7:6405 + Python 3.12-slim）
> 结果：**638 passed, 11 deselected, 79.73% 覆盖率**（门槛 70%）；U08 新增 26 例（16 unit + 5 integration + 5 api），**首次运行全通过，无 bug**

---

## 1. 单元测试（16 例）

| 文件 | 用例 |
|---|---|
| `test_metric_common.py`（5） | safe_div 正常 / quantize / 分母 0 / None 操作数 / Decimal 输入 |
| `test_report_domain.py`（11） | resolve_time_range（last_7d/last_30d/this_month/last_month/custom + 缺日期/from>to/跨度超限 422/非法 preset 422）+ level_publish_rate + level_overdue_rate |

## 2. 集成测试（5 例，`test_publish_progress.py`，已知数据集断言）

| 用例 | 校验 |
|---|---|
| test_summary_metrics | 9 指标精确数值（quote=4/quote_amount=3800/cooperation=3000/publish=2/cancel=1/overdue=1/like=510 折算/publish_rate=0.5 yellow/overdue_rate=0.25/cpl 非空） |
| test_cards_group_by_style | total=1 + 卡片聚合数值 |
| test_detail_by_pr_and_time | by_pr GROUP pr + by_time 半月 bucket 求和 |
| test_empty_dataset_null_rates | 空集 count=0 + rate/level/cpl=null |
| test_tenant_isolation | tenant_b 视角查询不含 tenant_a 数据（显式 tenant_id 过滤） |

## 3. API 测试（5 例，`test_report_api.py`）

| 用例 | 校验 |
|---|---|
| 4× requires_auth | summary/cards/by-pr/by-time 无 token → 401 |
| openapi_exposes_report_endpoints | OpenAPI 暴露 4 端点 |

---

## 4. 故事追溯矩阵

| 故事 | 测试 | 结果 |
|---|---|---|
| EP09-S01 Layer1 汇总 | test_summary_metrics | ✅ |
| EP09-S01 Layer2 卡片 | test_cards_group_by_style | ✅ |
| EP09-S01 Layer3 PR/趋势 | test_detail_by_pr_and_time | ✅ |
| EP09-S01 分母 0→— | test_metric_common + test_empty_dataset_null_rates | ✅ |
| EP09-S07 时间筛选 | test_report_domain | ✅ |

## 5. 设计守护测试矩阵

| 守护 | 测试 | 结果 |
|---|---|---|
| P-U08-01 TimeRange 5 preset + 边界 | test_report_domain | ✅ |
| P-U08-02 聚合 SQL + tenant 隔离 | test_summary/cards + test_tenant_isolation | ✅ |
| P-U08-03 safe_div null + level | test_metric_common + test_empty_dataset_null_rates | ✅ |
| 点赞折算（抖音 ×0.1） | test_summary_metrics（like=510） | ✅ |

---

## 6. Build & Test 过程

- Docker（PG16:5550 + Redis7:6405 + Py3.12 + u08_net + u08_pipcache）；alembic 001→011 全链路成功（无新 migration，head=011）。
- U08 子集 26 passed；全量回归 **638 passed / 0 failed / 11 deselected**；覆盖率 **79.73%**。
- **首次运行全通过，无生产 bug，无测试修复**。
- report 模块覆盖：domain 100% / repository 100% / schemas 100% / exceptions 100% / service 91% / api 73%；metric/common 100%。
- 清理临时容器/网络/卷 + 临时脚本。

## 7. 已知无害告警
- 测试结束后 redis `AbstractConnection.__del__` 的 `RuntimeError: Event loop is closed` 为已知无害告警（638 passed）。
