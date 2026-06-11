# U05 财务结款核心 — 测试覆盖矩阵

> 单元：U05  
> 测试文件：5 unit + 7 integration + 1 api + 2 performance + conftest 修改  
> 覆盖率目标：service ≥ 80% / domain ≥ 90% / api ≥ 60%（继承 U01 基线）

---

## 1. 单元测试（5 文件）

### 1.1 test_settlement_state_machine.py
- 6 合法转移逐一验证（参数化）+ Enum 入参
- 6 非法转移抛 IllegalStateTransitionError（含 details from/to/action）
- get_allowed_transitions（待核查 2 路径 / 已付款终态 / 待付款 2 路径）

### 1.2 test_settlement_domain.py
- format_settlement_no：基础格式 + prefix 补位（短/空）+ 4 位序号补零
- build_settlement_audit_changes（FB3+FB4）：金额脱敏 *_changed / attachment_id 脱敏 / 非敏感字段跳过 / 状态值原样
- compute_state_change：记录变更 / 无变更空

### 1.3 test_settlement_field_perms.py（FB4）
- 3 类角色矩阵：PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD
- 财务可见不可写（payment_amount）
- PR 主管可写金额但不能上传付款截图（职责分离）
- 完整 4 角色 × 3 维度矩阵断言

### 1.4 test_settlement_paid_event.py（FB5）
- required_handler=False（通知类）
- frozen 不可变
- 与 SettlementRequested（required_handler=True）的不对称断言

### 1.5 test_attachment_validator.py（FB4）
- happy path（6 项全合规通过）
- 6 项各 1 失败用例（not_found / bucket / purpose / mime / size / status）
- 跨租户 4 层防御：抛 InvalidAttachmentReferenceError + 不泄露存在性 + 记 tenant_mismatch 指标（mock sentry / bypass session）

---

## 2. 集成测试（7 文件，按内聚合并）

### 2.1 test_settlement_create_via_event.py（FB1 + FB3 + FB6 + FB2）
- handler 创建 settlement + 起点 settlement_status="待核查"（FB1）
- 重复事件幂等跳过（永久 UNIQUE + service SELECT，FB3）
- 顺序创建序号不重复（FB2）

### 2.2 test_settlement_lifecycle.py（review + extra_item + fill_payment + resubmit + immutable）
- TestReview：approve→待付款 / reject→已驳回（含 reason）/ 自审禁止
- TestExtraItem：total 重算 / 非待付款拒绝 / PR 字段权限拒绝
- TestFillPaymentAndResubmit：fill_payment→待财务付款 / finance 不可写拒绝 / resubmit→待核查
- TestImmutable（FB3）：无 is_active 字段 / service 无 delete 方法

### 2.3 test_settlement_mark_paid.py（FB4 + FB5）
- happy path：校验通过→已付款 + 发 SettlementPaid（FB5）
- TestMarkPaidAttachmentValidation（FB4）：跨租户 / not_ready / missing 拒绝
- TestSettlementPaidReverseListener（FB5）：反向同步 promotion / 无 listener 不阻塞 / listener 失败不阻塞（不对称）

### 2.4 test_settlement_concurrency.py（FB7）
- 100 并发首次序列号无重复
- 50 并发状态推进只 1 成功
- 跨租户 update_state 0 行匹配
- from_state 不匹配 0 行匹配

### 2.5 test_settlement_daily_summary.py（FB7 + FB8）
- as_of 各状态 bucket + outstanding_total 计算（FB7 口径 B）
- as_of PR 角色 FieldPermissionDenied
- activity newly_created 计数（FB7 口径 A）
- activity date_value=None 默认 get_today（FB8）

### 2.6 test_attachment_upload.py（shared 基础设施）
- mark_uploaded：uploading→ready 状态机
- 跨租户 mark_uploaded 拒绝（WHERE tenant_id）
- 重复 mark（已 ready）拒绝
- get_by_id：存在 / 缺失返回 None

### 2.7 test_e2e_review_to_paid.py（J4 端到端）
- U04 review approve → SettlementRequested → U05 settlement（待核查）
- → review approve → fill_payment → mark_paid → 已付款
- → SettlementPaid → promotion.settlement_status="已付款"（cross_unit_event_bus）

---

## 3. API 测试（1 文件）

### test_settlement_api.py
- 鉴权：list / get / review / payment-proof / daily-summary 未登录 401
- DELETE → 405（FB3）
- OpenAPI：8 settlement 端点 + 2 attachment 端点暴露

---

## 4. 性能测试（2 文件，CI 默认跳过 `-m "not performance"`）

### 4.1 test_settlement_list_perf.py
- 1000 settlement 列表冒烟 < 1s（staging 目标 P95 ≤ 200ms）
- settlement_no GIN trgm 关键字搜索冒烟

### 4.2 test_daily_summary_perf.py（FB7）
- 口径 B（GROUP BY）2000 行冒烟 < 1s
- 口径 A（audit JOIN）2000 行冒烟 < 1.5s

---

## 5. conftest.py 新增 fixtures

| Fixture | 用途 |
|---|---|
| `attachment_factory` | 创建 Attachment 行（默认 6 项合规 ready；可覆盖任一字段构造失败用例） |
| `settlement_factory` | 直接落 Settlement 行（绕过 listener，测下游流程） |
| `cross_unit_event_bus` | 注册真实 finance + promotion 双向 listener（端到端事件链路测试） |

> 复用 U04 已有：`_clear_event_handlers`（autouse）/ `event_capture` / `promotion_factory` / `product_factory` / `blogger_factory` / 角色 fixtures。

---

## 6. 8 P1 反馈守护测试覆盖确认

| 反馈 | 守护测试位置 | 状态 |
|---|---|---|
| FB1 | test_settlement_create_via_event + test_e2e_review_to_paid | ✅ |
| FB2 | test_settlement_create_via_event + test_settlement_concurrency::TestSequenceConcurrent | ✅ |
| FB3 | test_settlement_lifecycle::TestImmutable + test_settlement_api::test_delete_returns_405 + 幂等跳过 | ✅ |
| FB4 | test_attachment_validator + test_settlement_mark_paid::TestMarkPaidAttachmentValidation | ✅ |
| FB5 | test_settlement_paid_event + test_settlement_mark_paid::TestSettlementPaidReverseListener | ✅ |
| FB6 | test_settlement_create_via_event（flush 后断言） | ✅ |
| FB7 | test_settlement_concurrency::TestUpdateStateConcurrent + test_settlement_daily_summary | ✅ |
| FB8 | test_settlement_daily_summary::test_activity_uses_today_when_no_date | ✅ |

---

## 7. 运行命令

```bash
# 全部（含 RLS，需独立 PG 角色）
cd backend && pytest -v

# CI 模式（跳过 RLS + 性能）
pytest -v -m "not rls and not performance" --cov=app --cov-fail-under=70

# 仅 U05 单元
pytest tests/unit/test_settlement_state_machine.py tests/unit/test_settlement_domain.py \
       tests/unit/test_settlement_field_perms.py tests/unit/test_settlement_paid_event.py \
       tests/unit/test_attachment_validator.py -v

# 仅 U05 集成
pytest tests/integration/test_settlement_*.py tests/integration/test_attachment_upload.py \
       tests/integration/test_e2e_review_to_paid.py -v
```
