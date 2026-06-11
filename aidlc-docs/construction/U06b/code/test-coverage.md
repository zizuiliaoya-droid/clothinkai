# U06b 测试覆盖摘要

> U06b 子集 17 用例全绿；全量 511 passed / 0 failed；总覆盖率 78.17%；style_sku.py adapter 96%
> 真实环境：Docker python:3.12-slim + PostgreSQL 16 + Redis 7（匹配 CI）

---

## 1. 测试文件清单（2 文件，17 用例）

| 文件 | 层 | 用例数 | 覆盖点 |
|---|---|---|---|
| `unit/test_style_sku_adapter.py` | unit | 15 | _to_decimal（千分位/空/非法/禁 float）+ parse_row（默认/自定义 mapping/strip）+ validate（通过/各必填/负数/非 Decimal/sourcing 白名单/超长/空 sourcing） |
| `integration/test_import_style_sku.py` | integration | 2 | 端到端 partial（新建+复用 style + 缺字段失败 + Decimal + tenant_id）+ retry only_failed 幂等 |

---

## 2. 故事追溯（EP07-S07~S10）

| 故事 | 守护测试 |
|---|---|
| EP07-S07 上传 | test_import_style_sku::test_end_to_end_partial（upload→runner→入库） |
| EP07-S08 去重 | 复用 U06a（框架层，已覆盖） |
| EP07-S09 映射版本 | test_style_sku_adapter::test_parse_row_custom_mapping |
| EP07-S10 失败下载/重试 | test_import_style_sku::test_retry_only_failed_idempotent |

---

## 3. 设计守护测试映射

| 守护 | 测试 | 验证 |
|---|---|---|
| P-U06b-01 单行两实体 | test_end_to_end_partial | 一行 → style + sku 入库 |
| BR-U06b-31 复用不覆盖 | test_end_to_end_partial | 第 2 行复用 ST..A（不重复建 style；最终仅 1 个 ST..A） |
| per-row 原子（FB-C） | test_end_to_end_partial | ST..B（缺 sku_code）整行回滚，style 不残留 |
| BR-U06b-13 Decimal 禁 float | test_decimal_parse + 集成 cost_price={1299.00,39.90} | 千分位解析 + Decimal 类型 |
| 跨租户 tenant_id（NF-1） | test_end_to_end_partial | sku.tenant_id == batch.tenant_id |
| sku 幂等 | test_retry_only_failed_idempotent | 重跑 sku 仍 2 个（不重复）+ attempt_count 递增 |

---

## 4. 覆盖率

| 文件 | 覆盖率 |
|---|---|
| `adapters/style_sku.py` | **96%**（仅 _resolve_brand 部分分支 + register 未完全覆盖） |
| `adapters/__init__.py` | 100% |

> adapter 核心路径（parse_row / validate / upsert style 复用+创建 / sku upsert）全部被真实端到端测试执行。

---

## 5. 真实环境验证记录
- `alembic upgrade head`：001→010 全链路成功（U06b 无新 migration，head 保持 010）
- U06b 子集：`17 passed`
- 全量回归：`511 passed / 0 failed`（含注册 style_sku adapter 后 U06a 框架仍绿 + main 启动正常）
- coverage：`78.17%`（U06b 前 77.89%，新增 adapter 96% 拉升）
- 命令：`pytest -m "not rls and not performance"`（与 CI 一致）
- 发现并修复 2 个测试断言问题（详见 README §4）：sku 排序断言 / retry 状态守卫模拟
- Docker 容器/网络/卷 + 临时脚本全部清理
