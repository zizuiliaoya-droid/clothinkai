# U04 推广合作核心 — 代码生成总结

> 单元：U04（MVP 第 4 个单元）  
> 阶段：Construction Code Generation 全部完成（Batch 1-4 / 13 Step）  
> 完成时间：2026-05-26

---

## 1. 文件清单

### 1.1 业务模块（modules/promotion/）— 16 文件
- `__init__.py` 模块说明
- `enums.py` PublishStatus / RecallStatus / SettlementStatus / ReviewAction
- `permissions.py` promotion:read/write/delete + promotion.review:approve
- `legacy_field_permissions.py` AMOUNT_VISIBLE_ROLES + AMOUNT_WRITABLE_ROLES（U09 后清理）
- `legacy_settings.py` PLATFORM_LIKE_COEFFICIENT / HIT_THRESHOLD / URGE/IMPORTANT_THRESHOLD_DAYS（V1 system_setting 后清理）
- `exceptions.py` 13 业务异常 + re-export FieldPermissionDenied
- `models.py` Promotion 28 字段 + PromotionSequence ORM
- `schemas.py` 14 Pydantic Schema
- `state_machines.py` 3 状态机（PublishStatus/RecallStatus/SettlementStatus）
- `events.py` SettlementRequested + PromotionPublished
- `urge_calculator.py` get_today + calculate_urge_status + URGE_STATUS_SQL_EXPR
- `metrics_calculator.py` calculate_effective_like_count / is_hit / cpl
- `domain.py` compute_promotion_changes + build_promotion_audit_changes 脱敏 + format_internal_code
- `repository.py` PromotionRepository（含 next_internal_sequence + update_state + list_with_cte）
- `service.py` PromotionService（CRUD 5 + 状态推进 6 + 内部 API + 4 私有方法）
- `deps.py` PromotionServiceDep
- `api.py` 11 端点

### 1.2 横切扩展 — 1 新建 + 3 修改
- 新建：`backend/app/core/events.py`（subscribe / dispatch / clear_handlers + required_handler 分类）
- 修改：
  - `backend/app/core/exceptions.py` 追加 MissingRequiredHandlerError
  - `backend/app/core/metrics.py` 追加 4 个 promotion 指标
  - `backend/app/main.py` 注册 promotion_router + lifespan register_event_listeners

### 1.3 数据库迁移 — 1 文件
- `backend/alembic/versions/006_u04_create_promotion_tables.py`（promotion + promotion_sequence + 11 索引 + 3 GIN trgm + 4 CHECK + RLS）

### 1.4 测试套件 — 13 文件
- 单元（6）：test_promotion_state_machines / test_promotion_domain / test_urge_calculator / test_metrics_calculator / test_event_bus / test_promotion_field_perms
- 集成（6）：test_promotion_crud / test_promotion_publish / test_promotion_cancel_recall / test_promotion_review / test_promotion_concurrency / test_urge_calculator_consistency
- API（1）：test_promotion_api
- 性能（1）：test_promotion_list_perf
- conftest.py 修改（promotion_factory + event_capture + auto-clear handlers）

### 1.5 前端 — 2 文件
- `frontend/src/features/promotion/types.ts`
- `frontend/src/features/promotion/api.ts`

### 1.6 CI/CD — 2 修改
- `.github/workflows/ci.yml` 追加 validate-event-listeners job
- `.github/workflows/deploy-staging.yml` 追加 e2e-smoke-after-deploy

### 1.7 文档摘要 — 3 文件
- `aidlc-docs/construction/U04/code/README.md`（本文件）
- `aidlc-docs/construction/U04/code/api-endpoints.md`
- `aidlc-docs/construction/U04/code/test-coverage.md`

**总计**：约 42 新文件 + 6 修改

---

## 2. 故事追溯矩阵

| 故事 ID | 业务点 | 实施位置 | 测试 |
|---|---|---|---|
| EP05-S02 | 创建推广 | service.create_promotion | test_promotion_crud / api / e2e |
| EP05-S03 | 自动填充商品简称 | service.create_promotion 快照 fallback | test_promotion_crud |
| EP05-S04 | 同款博主重复检测 | repository.find_active_duplicate + warnings | test_promotion_crud::TestDuplicateWarning |
| EP05-S05 | 双平台标记 | repository.has_other_platforms_for_style + CTE | test_urge_calculator_consistency |
| EP05-S06 | urge_status 实时 | urge_calculator.calculate_urge_status + URGE_STATUS_SQL_EXPR | test_urge_calculator + test_urge_calculator_consistency（FB8） |
| EP05-S07 | publish | service.publish | test_promotion_publish + test_promotion_concurrency（FB7） |
| EP05-S08 | cancel | service.cancel | test_promotion_cancel_recall::TestCancel |
| EP05-S09 | recall | service.start_recall / recall_success / recall_failure | test_promotion_cancel_recall::TestRecall |
| EP05-S10 | effective_like_count | metrics_calculator.calculate_effective_like_count | test_metrics_calculator |
| EP05-S11 | is_hit | metrics_calculator.calculate_is_hit | test_metrics_calculator |
| EP05-S12 | cpl | metrics_calculator.calculate_cpl | test_metrics_calculator |
| EP05-S13 | review approve | service.review + SettlementRequested 事件 | test_promotion_review（FB1） |

---

## 3. 8 P1 反馈守护测试矩阵

| 反馈 | 守护测试 | 状态 |
|---|---|---|
| **FB1** SettlementRequested 强一致 | test_event_bus::test_required_event_no_handler_raises + test_promotion_review::test_required_event_no_handler_rolls_back | ✅ |
| **FB2** 序列号首次创建 race | test_promotion_concurrency::TestSequenceConcurrent | ✅ |
| **FB3** ImportError 容错 | main.py register_event_listeners 实施（CI validate-event-listeners） | ✅ |
| **FB4** 必要/通知事件区分 | test_event_bus::TestRequiredVsOptional + events.py required_handler ClassVar | ✅ |
| **FB5** audit 失败脱敏 + 兜底 | test_promotion_review::test_handler_exception_audit_sanitized + service._log_event_dispatch_failure | ✅ |
| **FB6** subscribe 防重复 | test_event_bus::TestSubscribeIdempotent | ✅ |
| **FB7** 状态机 WHERE 强化 | test_promotion_publish::test_publish_other_tenant_returns_no_match + test_promotion_concurrency::TestPublishConcurrent | ✅ |
| **FB8** 日期口径一致 | test_urge_calculator + test_urge_calculator_consistency（freezegun + 100 mock）| ✅ |

---

## 4. 后续单元接口

### 4.1 U05（财务结款）— 同批部署必需
- 必须订阅 `SettlementRequested` 事件：
  ```python
  # modules/finance/listeners.py
  def register() -> None:
      from app.core.events import subscribe
      subscribe("SettlementRequested", SettlementService.on_settlement_requested)
  ```
- main.py 启动时通过 `from app.modules.finance.listeners import register` 自动加载

### 4.2 U06d（推广导入）
- 通过 `PromotionService.create_promotion` 重用业务规则
- 序列号原子分配天然支持批量并发导入

### 4.3 U07（企微通知）
- 订阅 `PromotionPublished` 事件即可启用通知

### 4.4 U13（数据采集 Worker）
- 调 `PromotionService.update_like_count`（内部 API，不暴露 HTTP）

### 4.5 U09（字段级权限）
- 替换 `legacy_field_permissions` 为 `Permission.field_filter()` / `Permission.field_writable()`
- 替换 `service._check_amount_write_permission` 为统一字段写权限装饰器

### 4.6 V1+ system_setting
- 替换 `legacy_settings.py` 为 `SystemSettingsService.get_promotion_settings()`

---

## 5. 质量门状态

- [x] 全部 13 Step 实施完成
- [x] 全部生成文件诊断器无警告
- [x] Python AST 解析通过
- [x] 8 P1 反馈守护测试覆盖
- [x] 故事追溯 EP05-S02~S13 完整闭环
- [x] CI validate-event-listeners + e2e-smoke-after-deploy 就绪

---

**单元交付状态**：U04 全部 5 阶段完成，等待与 U05 同批部署。
