# U06c 测试覆盖摘要

> U06c 子集 22 用例全绿；全量 533 passed / 0 failed；总覆盖率 78.50%；adapters/blogger.py 99%
> 真实环境：Docker python:3.12-slim + PostgreSQL 16 + Redis 7（匹配 CI）

---

## 1. 测试文件清单（2 文件，22 用例）

| 文件 | 层 | 用例数 | 覆盖点 |
|---|---|---|---|
| `unit/test_blogger_adapter.py` | unit | 20 | _split_tags（多分隔符/空/strip）+ _to_int（千分位/非法）+ _to_decimal（禁 float）+ parse_row（默认/自定义）+ validate（必填/数值/长度各分支） |
| `integration/test_import_blogger.py` | integration | 2 | 端到端 partial（新建 + 同 ID UPDATE + 缺 ID failed + 标签 JSONB + int + Decimal + tenant_id）+ 多标签 JSONB 数组 |

---

## 2. 故事追溯（EP07-S07~S10）

| 故事 | 守护测试 |
|---|---|
| EP07-S07 上传 | test_import_blogger::test_end_to_end_partial_and_update |
| EP07-S08 去重 | 复用 U06a（框架层） |
| EP07-S09 映射版本 | test_blogger_adapter::test_parse_row_custom_mapping |
| EP07-S10 失败下载/重试 | test_import_blogger（缺 ID → failed → partial） |

---

## 3. 设计守护测试映射

| 守护 | 测试 | 验证 |
|---|---|---|
| P-U06c-01 单实体 upsert | test_end_to_end_partial_and_update | 一行 → blogger 入库 |
| 标签 list → JSONB | test_split_tags + test_tags_jsonb_first_row | category_tags=['美妆','护肤','穿搭'] |
| int/Decimal 禁 float | test_to_int / test_to_decimal + 集成 follower=13000/quote=600 | 类型正确 |
| 同 ID UPDATE 幂等 | test_end_to_end_partial_and_update | 同 xiaohongshu_id 仅 1 条（UPDATE 改名/follower/quote） |
| 跨租户 tenant_id（NF-1） | test_end_to_end_partial_and_update | blogger.tenant_id == batch.tenant_id |

---

## 4. 覆盖率

| 文件 | 覆盖率 |
|---|---|
| `adapters/blogger.py` | **99%**（仅 1 行未覆盖） |
| `adapters/__init__.py` | 100% |

---

## 5. 真实环境验证记录
- `alembic upgrade head`：001→010 全链路成功（U06c 无新 migration，head 保持 010）
- U06c 子集：`22 passed`
- 全量回归：`533 passed / 0 failed`（注册 blogger adapter 后框架 + main 启动正常；U06b style_sku + U06c blogger 同时注册无冲突）
- coverage：`78.50%`（U06c 前 78.17%，新增 adapter 99% 拉升）
- 命令：`pytest -m "not rls and not performance"`（与 CI 一致）
- 修复 1 个测试数据问题（README §4）：CSV 未加引号逗号 → 标签溢出列
- Docker 容器/网络/卷 + 临时脚本全部清理
