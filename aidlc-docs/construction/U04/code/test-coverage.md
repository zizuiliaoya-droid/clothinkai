# U04 测试覆盖矩阵

> 生成时间：2026-05-26  
> 单元：U04 — 推广合作核心  
> 文件总数：13（6 unit + 6 integration + 1 api + 1 perf + 1 conftest 修改）

---

## 1. 单元测试（6 文件）

### `tests/unit/test_promotion_state_machines.py`
- `TestPublishStatusMachine` — 5 合法转移参数化 + cancel 不允许已发布 + restore + get_allowed_transitions
- `TestRecallStatusMachine` — 4 合法转移 + 终态校验 + 跳跃禁止
- `TestSettlementStatusMachine` — 5 合法转移 + paid 终态 + 直接 approve 拒绝 + 字符串/枚举互通

**覆盖**：3 状态机 14 条 transitions + assert_can_transition + get_allowed_transitions

### `tests/unit/test_promotion_domain.py`
- `TestPromotionAuditChanges` — quote_amount/cost_snapshot 仅记 `*_changed: true`；非敏感字段不写；compute_state_change unchanged 返回空
- `TestComputePromotionChanges` — PATCH 语义 + 仅显式 set 的字段进 diff
- `TestFormatInternalCode` — basic / short tenant 补 X / 空 tenant / sequence 0001..9999 padding / uppercase

**覆盖**：BR-U04-01（internal_code 格式）+ BR-U04-40（audit 脱敏）+ dict diff

### `tests/unit/test_urge_calculator.py`
- `TestCalculateUrgeStatus` — 7 分支全覆盖 + 边界（diff == 0）
- `TestGetToday` — 时区固定 Asia/Shanghai + UTC 23:59 边界日（freezegun）

**覆盖**：BR-U04-30 全部分支 + FB8 时区一致性

### `tests/unit/test_metrics_calculator.py`
- `TestEffectiveLikeCount` — 5 平台系数参数化 + None / 0 / ROUND_HALF_UP（1.5 / 2.5）
- `TestIsHit` — 阈值边界 / None / 自定义阈值 / "用原始而非折算 like_count"
- `TestCpl` — 0 分母防御 / None 防御 / 4 位精度 / ROUND_HALF_UP

**覆盖**：BR-U04-31/32/33

### `tests/unit/test_event_bus.py`
- `TestSubscribeIdempotent` — FB6 重复注册仅一次 + clear_handlers 后重订阅
- `TestRequiredVsOptional` — FB1/FB4 required 抛错 + optional no-op
- `TestHandlerFailure` — FB1 异常冒泡 + 多 handler 第一个抛即中断
- `TestEventTypeValidation` — 缺 event_type 的事件抛 ValueError

**覆盖**：core/events.py 全部入口 + FB1/FB4/FB6 三条守护

### `tests/unit/test_promotion_field_perms.py`
- `TestAmountVisibility` — 9 角色组合参数化
- `TestAmountWritable` — finance 仅读不写

**覆盖**：legacy_field_permissions 全部入口（U09 重写后删除）

---

## 2. 集成测试（6 文件）

### `tests/integration/test_promotion_crud.py`
- `TestCreatePromotion` — blogger.quote 自动快照 / short_name fallback / explicit override / 不存在 style/blogger 校验
- `TestDuplicateWarning` — EP05-S04 重复非阻塞 warning
- `TestSequenceGeneration` — 同日递增 0001→0002 / 不同日期独立计数
- `TestUpdatePromotion` — quote_amount 编辑 / 不存在抛错

**覆盖**：EP05-S02 / S03 / S04 完整 CRUD

### `tests/integration/test_promotion_publish.py`
- `test_publish_advances_settlement` — publish + 同事务推 settlement_status 待核查
- `test_publish_emits_event` — PromotionPublished 事件
- `test_publish_already_published_raises` — 业务前置校验
- `test_publish_other_tenant_returns_no_match` — **FB7 跨租户防护**

**覆盖**：EP05-S07 + FB7

### `tests/integration/test_promotion_cancel_recall.py`
- `TestCancel` — unpublished 取消 / 已发布拒绝
- `TestRecall` — 启动召回 / 跨状态机校验 / 召回中→失败→重启→成功完整生命周期

**覆盖**：EP05-S08 / S09

### `tests/integration/test_promotion_review.py`
- `TestReviewApprove` — approve + SettlementRequested 事件 / 自审禁止
- `TestReviewReject` — reason 必填 / reject 含 reason
- `TestEventFailureRollback` — **FB1**: required_event 无 handler 抛错 / **FB5**: handler 抛敏感字符串异常 → audit 脱敏

**覆盖**：EP05-S13 + FB1 + FB5

### `tests/integration/test_promotion_concurrency.py`
- `TestSequenceConcurrent` — **FB2**：100 并发首次创建无重复
- `TestPublishConcurrent` — **FB7**：50 并发 publish 仅 1 成功

**覆盖**：FB2 + FB7 并发守护

### `tests/integration/test_urge_calculator_consistency.py`
- 100 mock 场景 Python vs SQL 双实现一致性（freezegun 边界日）
- 边界用例 scheduled == today → "重要催发"

**覆盖**：FB8 双实现一致性

---

## 3. API 测试（1 文件）

### `tests/api/test_promotion_api.py`
- 11 端点鉴权要求（401 unauthenticated）
- OpenAPI spec 验证 8 个 path 暴露

**覆盖**：API 契约层

---

## 4. 性能测试（1 文件）

### `tests/performance/test_promotion_list_perf.py`
- 1000 promotion + CTE 列表冒烟（< 1s 阈值；staging 跑完整 P95 ≤ 300ms）

**覆盖**：NFR §3.1 列表 SLA 冒烟

---

## 5. conftest.py 扩展

新增 fixtures：
- `_clear_event_handlers`（autouse）— 每测试前后清空事件总线（防 FB6 累计污染）
- `promotion_factory` — Promotion 测试数据工厂（28 字段 + 3 状态默认）
- `event_capture` — 订阅 SettlementRequested + PromotionPublished 并 push 到 list

---

## 6. 8 P1 反馈守护测试映射

| 反馈 | 守护测试 |
|---|---|
| FB1 | unit/test_event_bus::test_required_event_no_handler_raises + integration/test_promotion_review::test_required_event_no_handler_rolls_back |
| FB2 | integration/test_promotion_concurrency::TestSequenceConcurrent（100 并发首次创建） |
| FB3 | （main.py register_event_listeners 实施 + CI validate-event-listeners job） |
| FB4 | unit/test_event_bus::TestRequiredVsOptional |
| FB5 | integration/test_promotion_review::test_handler_exception_audit_sanitized |
| FB6 | unit/test_event_bus::TestSubscribeIdempotent + conftest auto-clear fixture |
| FB7 | integration/test_promotion_publish::test_publish_other_tenant_returns_no_match + integration/test_promotion_concurrency::TestPublishConcurrent |
| FB8 | unit/test_urge_calculator + integration/test_urge_calculator_consistency（100 mock + freezegun） |

---

## 7. 故事覆盖矩阵

| 故事 | 验收测试 |
|---|---|
| EP05-S02 创建推广 | test_promotion_crud::TestCreatePromotion |
| EP05-S03 商品简称自动填充 | test_promotion_crud::test_create_with_short_name_fallback |
| EP05-S04 同款博主重复检测 | test_promotion_crud::TestDuplicateWarning |
| EP05-S05 双平台标记 | test_urge_calculator_consistency（CTE 计算列） |
| EP05-S06 urge_status 实时 | test_urge_calculator + test_urge_calculator_consistency |
| EP05-S07 publish | test_promotion_publish |
| EP05-S08 cancel | test_promotion_cancel_recall::TestCancel |
| EP05-S09 recall | test_promotion_cancel_recall::TestRecall |
| EP05-S10 effective_like_count | test_metrics_calculator::TestEffectiveLikeCount |
| EP05-S11 is_hit | test_metrics_calculator::TestIsHit |
| EP05-S12 cpl | test_metrics_calculator::TestCpl |
| EP05-S13 review approve | test_promotion_review |

---

## 8. 覆盖率目标

| 层 | 目标 | 实施 |
|---|---|---|
| service.py | ≥ 80% | 6 集成 + 6 单元（state / domain / urge / metrics / event_bus / field_perms） |
| domain.py / urge_calculator / metrics_calculator | ≥ 90% | 3 unit 文件参数化覆盖全分支 |
| repository.py | ≥ 80% | CRUD + 并发 + CTE 一致性测试 |
| api.py | ≥ 60% | API 契约 + 端到端集成测试 |

CI 强制 `--cov-fail-under=70`（与 U01 / U02 / U03 一致）。
