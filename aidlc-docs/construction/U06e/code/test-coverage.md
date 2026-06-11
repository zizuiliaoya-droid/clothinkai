# U06e 测试覆盖（结算导入适配器）

> 单元：U06e — 结算导入适配器
> Build & Test：Docker（PG16:5548 + Redis7:6403 + Python 3.12-slim）
> 结果：**576 passed, 11 deselected, 79.30% 覆盖率**（门槛 70%）

---

## 1. 测试清单（新增 24 例）

### 1.1 单元测试 `tests/unit/test_settlement_adapter.py`（22 例，纯函数无 DB）

| 分组 | 用例 |
|---|---|
| `_to_date` | valid / empty / invalid_keeps_raw |
| `_to_decimal` | thousands_no_float / empty_and_invalid |
| `parse_row` | default_mapping / optional_empty / custom_mapping |
| `validate` | pass / pass_with_optionals / missing_promotion_code / missing_amount / missing_total_amount / negative_amount / invalid_amount_kept_raw / negative_payment_amount / missing_settlement_date / invalid_settlement_date / invalid_payment_date / invalid_status / all_five_status_values / note_title_length |

### 1.2 集成测试 `tests/integration/test_import_settlement.py`（2 例，真实 adapter→runner→入库）

| 用例 | 校验 |
|---|---|
| `test_end_to_end_derive_sequence_and_conflicts` | 1 成功（promotion 派生 blogger/style + settlement_no + 合成 event_id + 跨租户）+ 1 重复 promotion（UNIQUE 冲突 → "已有结算单"）+ 1 缺 promotion（→ "推广编号 X 不存在"）→ partial(1/2)；event_capture 空（不触发事件） |
| `test_full_row_all_fields_persisted` | 单行全字段（含千分位引号金额、付款金额/付款日期/笔记标题/备注）→ completed(1/0)；全字段入库；event_capture 空 |

---

## 2. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | upload(source=manual_settlement) + adapter.upsert | test_end_to_end_derive_sequence_and_conflicts |
| EP07-S08 去重 | U06a hash + UNIQUE(promotion_id) | test_end_to_end（重复 promotion failed） |
| EP07-S09 映射 | parse_row 默认/自定义双路 | test_parse_row_custom_mapping |
| EP07-S10 失败/重试 | promotion 缺失/UNIQUE 冲突 → import_job.failed | test_end_to_end（缺 promotion failed） |

---

## 3. 设计守护测试矩阵

| 守护 | 测试 | 结果 |
|---|---|---|
| P-U06e-01 INSERT-only + promotion 派生 | test_end_to_end（settlement.blogger_id/style_id == promotion 的） | ✅ |
| UNIQUE(promotion_id) 冲突 catch（FB3） | test_end_to_end（"已有结算单"） | ✅ |
| 合成 request_event_id | test_end_to_end（request_event_id 非空） | ✅ |
| 不触发事件 | test_end_to_end / test_full_row（event_capture == []） | ✅ |
| date/Decimal 禁 float | test_to_date / test_to_decimal_thousands_no_float | ✅ |
| settlement_status 5 枚举 | test_validate_all_five_status_values / test_validate_invalid_status | ✅ |
| 跨租户 tenant_id（NF-1 + RLS） | test_end_to_end（settlement.tenant_id == batch.tenant_id） | ✅ |
| settlement_no（FB2 序列 + format） | test_end_to_end（"S260601" in settlement_no） | ✅ |

---

## 4. 覆盖率

- **总覆盖率 79.30%**（门槛 70%，PASS）。
- `adapters/settlement.py`：parse_row / validate / upsert / _get_tenant_code 全路径覆盖。
- 全量回归 576 passed（U06d 552 + U06e 24），确认注册 4 个 adapter（style_sku/blogger/promotion/settlement）后框架仍绿。

---

## 5. 已知无害告警
- 测试结束后 `RuntimeError: Event loop is closed`（redis `AbstractConnection.__del__`）为已知无害告警，不影响测试结果（576 passed）。
