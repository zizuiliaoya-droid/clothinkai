# U06d 测试覆盖摘要

> U06d 子集 19 用例全绿；全量 552 passed / 0 failed；总覆盖率 78.87%；adapters/promotion.py 95%
> 真实环境：Docker python:3.12-slim + PostgreSQL 16 + Redis 7（匹配 CI）

---

## 1. 测试文件清单（2 文件，19 用例）

| 文件 | 层 | 用例数 | 覆盖点 |
|---|---|---|---|
| `unit/test_promotion_adapter.py` | unit | 17 | _to_date（合法/非法/空）+ _to_decimal（禁 float）+ parse_row（默认/自定义）+ validate（必填/数值/date 各分支） |
| `integration/test_import_promotion.py` | integration | 2 | 端到端 partial（FK 解析 + internal_code 连续 + 3 状态初始态 + tenant_id + 缺 style/xhs failed）+ 缺 blogger FK failed |

---

## 2. 故事追溯（EP07-S07~S10）

| 故事 | 守护测试 |
|---|---|
| EP07-S07 上传 | test_import_promotion::test_end_to_end_fk_resolution_and_sequence |
| EP07-S08 去重 | 复用 U06a（框架层） |
| EP07-S09 映射版本 | test_promotion_adapter::test_parse_row_custom_mapping |
| EP07-S10 失败下载/重试 | test_import_promotion（缺 FK → failed） |

---

## 3. 设计守护测试映射

| 守护 | 测试 | 验证 |
|---|---|---|
| P-U06d-01 INSERT-only + FK 解析 | test_end_to_end_fk_resolution_and_sequence | FK 解析 + 建 promotion |
| internal_code 生成 + 序号连续 | test_end_to_end（codes 前缀同 + seq+1） | 同 cooperation_date 序号连续 |
| FK 缺失 raise → failed | test_end_to_end（缺 style）+ test_missing_blogger_fails | 行失败 + error_detail |
| 3 状态初始态 | test_end_to_end（publish_status=未发布 / settlement_status=未核查） | server_default 生效 |
| date/Decimal 禁 float | test_to_date / test_to_decimal | 类型正确 |
| 跨租户 tenant_id（NF-1） | test_end_to_end（promotion.tenant_id == batch.tenant_id） | RLS 正确 |

---

## 4. 覆盖率

| 文件 | 覆盖率 |
|---|---|
| `adapters/promotion.py` | **95%**（仅少数错误分支未覆盖） |
| `adapters/__init__.py` | 100% |

---

## 5. 真实环境验证记录
- `alembic upgrade head`：001→010 全链路成功（U06d 无新 migration，head 保持 010）
- U06d 子集：`19 passed`
- 全量回归：`552 passed / 0 failed`（U06b style_sku + U06c blogger + U06d promotion 3 adapter 同时注册无冲突）
- coverage：`78.87%`（U06d 前 78.50%，新增 adapter 95% 拉升）
- 命令：`pytest -m "not rls and not performance"`（与 CI 一致）
- **adapter 一次实现通过，无生产 bug、无测试断言修复**（U06d 最复杂但实现最顺利，得益于 NFR Design 完整伪代码）
- Docker 容器/网络/卷 + 临时脚本全部清理
