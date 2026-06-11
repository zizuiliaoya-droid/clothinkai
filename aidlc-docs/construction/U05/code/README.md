# U05 财务结款核心 — 代码生成总结

> 单元：U05（MVP 第 5 个单元）  
> 阶段：Construction Code Generation 全部完成（Batch 1-4 / 13 Step）  
> 完成时间：2026-05-26  
> 关键差异：**shared attachment 基础设施补齐**（Option A 修订）+ **双向事件 listener**（强一致正向 + 通知类反向）

---

## 1. 文件清单

### 1.1 业务模块（modules/finance/）— 16 文件
- `__init__.py` 模块说明
- `enums.py` SettlementStatus（5）/ ExtraItemType（3）
- `permissions.py` settlement:read/write/review/pay
- `legacy_field_permissions.py` 3 类 ROLES：PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD（U09 清理）
- `exceptions.py` 18 业务异常 + re-export FieldPermissionDenied
- `models.py` Settlement（22 字段无 is_active，FB3）+ SettlementExtraItem + SettlementSequence
- `schemas.py` 13+ Pydantic Schema
- `state_machines.py` SettlementStatusMachine（5 状态 6 转移）
- `events.py` SettlementPaid（required_handler=False，FB5）
- `domain.py` format_settlement_no + compute_settlement_changes + build_settlement_audit_changes 脱敏
- `attachment_validator.py` ProofAttachmentValidator 6 项强校验 + 跨租户 4 层防御（FB4）
- `repository.py` SettlementRepository（next_settlement_sequence + update_state + 双口径 daily_summary）
- `service.py` SettlementService（4 状态推进 + add_extra_item + daily_summary × 2 + 失败处理不对称）
- `listeners.py` on_settlement_requested 强一致正向（FB1+FB6）
- `deps.py` SettlementServiceDep
- `api.py` 8 端点 + DELETE 405（FB3）

### 1.2 Shared attachment 基础设施补齐 — 1 修改 + 1 新建
- 修改 `backend/app/core/attachment.py`：追加 Attachment ORM（11 字段）+ ALLOWED_PURPOSES + 3 方法（create_upload_record / mark_uploaded / get_by_id）
- 新建 `backend/app/core/attachment_api.py`：POST /api/attachments/upload-init + /{id}/complete（通用语义，不放 modules/finance）

### 1.3 横切扩展 — 1 新建 + 2 修改
- 新建：`backend/app/modules/promotion/listeners.py`（on_settlement_paid 通知类反向，FB5）
- 修改：
  - `backend/app/core/metrics.py` 追加 5 个 settlement 指标（Batch 1）
  - `backend/app/main.py` 注册 finance_router + attachment_router + register_event_listeners 双向扩展

### 1.4 数据库迁移 — 3 文件
- `007_u05_create_settlement_tables.py`（**两段**：上半段 shared attachment 表 + RLS；下半段 settlement/extra_item/sequence + FK + 永久 UNIQUE + GIN trgm，FB3）
- `008_u05_backfill_settlements.py`（FB8 PL/pgSQL + 复用 settlement_sequence + 不可逆 downgrade）
- `009_u05_seed_smoke_test_data.py`（staging 专用，ENVIRONMENT=staging 守卫）

### 1.5 测试套件 — 12 文件
- 单元（5）：test_settlement_state_machine / test_settlement_domain / test_settlement_field_perms / test_settlement_paid_event / test_attachment_validator（FB4）
- 集成（6，按内聚合并）：test_settlement_create_via_event（FB1+FB3+FB6）/ test_settlement_lifecycle（review+extra_item+fill_payment+resubmit+immutable）/ test_settlement_mark_paid（FB4+FB5+反向 listener）/ test_settlement_concurrency（FB7）/ test_settlement_daily_summary（FB7+FB8）/ test_attachment_upload（shared 基础设施）/ test_e2e_review_to_paid（J4）
- API（1）：test_settlement_api（鉴权 + OpenAPI 8 端点 + DELETE 405）
- 性能（2）：test_settlement_list_perf / test_daily_summary_perf
- conftest.py 修改（settlement_factory + attachment_factory + cross_unit_event_bus fixture）

### 1.6 前端 — 2 文件
- `frontend/src/features/finance/types.ts`
- `frontend/src/features/finance/api.ts`（含 R2 直传 putFileToR2）

### 1.7 CI/CD — 2 修改
- `.github/workflows/ci.yml` validate-event-listeners 升级（finance 强一致 fail fast + promotion 反向 warning）
- `.github/workflows/deploy-staging.yml` **启用真实 e2e-smoke**（替换 U04 placeholder，验证 FB1 强一致）

### 1.8 文档摘要 — 3 文件
- `aidlc-docs/construction/U05/code/README.md`（本文件）
- `aidlc-docs/construction/U05/code/api-endpoints.md`
- `aidlc-docs/construction/U05/code/test-coverage.md`

**总计**：约 44 新文件 + 5 修改（含 shared attachment 基础设施补齐）

---

## 2. 故事追溯矩阵

| 故事 ID | 业务点 | 实施位置 | 测试 |
|---|---|---|---|
| EP06-S02 | 自动生成结算单 | listeners.on_settlement_requested | test_settlement_create_via_event |
| EP06-S03 | PR 主管核查 approve | service.review action=approve | test_settlement_lifecycle::TestReview |
| EP06-S04 | PR 主管驳回 reject | service.review action=reject | test_settlement_lifecycle::TestReview |
| EP06-S05 | 增加结算项 | service.add_extra_item | test_settlement_lifecycle::TestExtraItem |
| EP06-S06 | 填写付款金额 | service.fill_payment_amount | test_settlement_lifecycle::TestFillPaymentAndResubmit |
| EP06-S07 | 财务上传付款截图 | service.upload_payment_proof + ProofAttachmentValidator | test_settlement_mark_paid |
| EP06-S08 | 当日结算汇总 | service.get_daily_summary_as_of / activity | test_settlement_daily_summary |

---

## 3. 8 P1 反馈守护测试矩阵

| 反馈 | 守护测试 | 状态 |
|---|---|---|
| **FB1** SettlementRequested 强一致 + 起点"待核查" | test_settlement_create_via_event::test_handler_creates_settlement_pending_review + test_e2e_review_to_paid | ✅ |
| **FB2** 序列号原子 | test_settlement_create_via_event::test_concurrent_first_create_no_duplicates + test_settlement_concurrency::TestSequenceConcurrent | ✅ |
| **FB3** 财务记录永久不可替换 | test_settlement_lifecycle::TestImmutable + test_settlement_api::test_delete_returns_405 + test_settlement_create_via_event::test_duplicate_event_idempotent_skip | ✅ |
| **FB4** Attachment 6 项强校验 + 跨租户 4 层 | test_attachment_validator（unit 6 项 + 跨租户）+ test_settlement_mark_paid::TestMarkPaidAttachmentValidation | ✅ |
| **FB5** 反向事件容忍 + 失败不对称 | test_settlement_paid_event + test_settlement_mark_paid::TestSettlementPaidReverseListener（no_listener / listener_failure 不阻塞） | ✅ |
| **FB6** handler 内 flush | test_settlement_create_via_event（flush 后断言可见） | ✅ |
| **FB7** 状态机 WHERE + 双口径汇总 | test_settlement_concurrency::TestUpdateStateConcurrent + test_settlement_daily_summary | ✅ |
| **FB8** 日期口径一致 | test_settlement_daily_summary::test_activity_uses_today_when_no_date + 008 backfill 复用 settlement_sequence | ✅ |

---

## 4. 关键架构决策

### 4.1 Shared attachment 基础设施（Option A 修订）
- attachment 表定位为 **shared 基础设施**（不是 U05 私有），代码在 `core/attachment.py` / `core/attachment_api.py`
- Settlement.payment_proof_attachment_id FK → attachment.id（不裸存 R2 key）
- upload 端点通用：`POST /api/attachments/upload-init`（白名单 ALLOWED_PURPOSES 控制）
- U02/U03 现有 attachment_key 保留，标记 V1 migration: attachment_key → attachment_id

### 4.2 双向事件 + 失败处理不对称（FB5 核心）
- **正向强一致**：SettlementRequested（required_handler=True）→ U05 创建失败 → U04 review approve 回滚
- **反向通知类**：SettlementPaid（required_handler=False）→ U04 同步失败不阻塞 U05 mark_paid 主流程
- main.py register_event_listeners：第 1 步 finance fail fast，第 2 步 promotion 通知类容忍

---

## 5. 后续单元接口

### 5.1 U06e（结算导入）
- 复用 `SettlementService` 业务规则；序列号原子分配支持批量并发

### 5.2 U09（字段级权限）
- 替换 `legacy_field_permissions`（3 类 ROLES）为 `Permission.field_filter()` / `field_writable()`

### 5.3 U14（投产报表）
- 消费 settlement.payment_amount 计算 ROI

### 5.4 V1 attachment 整合
- U02/U03 attachment_key → attachment_id 迁移
- attachment GC 任务（idx_attachment_status 已预留）+ reference_count 字段

### 5.5 V1 reconcile
- SettlementPaid 反向同步失败兜底（每天 03:00 对账 promotion.settlement_status）

---

## 6. 质量门状态

- [x] 全部 13 Step 实施完成
- [x] 全部生成文件诊断器无警告
- [x] Python AST 解析通过
- [x] 8 P1 反馈守护测试覆盖
- [x] 故事追溯 EP06-S02~S08 完整闭环
- [x] CI validate-event-listeners（双向）+ e2e-smoke-after-deploy（真实）就绪
- [x] migration 007 两段（shared attachment + settlement）+ 008 backfill + 009 staging seed

---

**单元交付状态**：U05 全部 5 阶段完成，与 U04 同批部署激活 SettlementRequested 事件链路，MVP 财务流程闭环。
