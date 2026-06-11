# U04 代码生成计划（Code Generation Plan）

> 单元：U04 — 推广合作核心  
> 阶段：MVP 第 4 个单元（关键路径核心）  
> 节奏：**Plan A 分 4 批**（U04 比 U02/U03 复杂，需要分批 review）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP05-S02~S13（12 个故事）— 完整推广合作生命周期。

### 1.2 依赖
- **强依赖**：U01（StateMachine 基类首次实战）+ U02（FieldPermissionDenied 复用 + sku snapshot）+ U03（blogger snapshot）
- **被依赖**：U05 (settlement 监听 SettlementRequested 事件) / U06d (推广导入) / U07 (企微监听 PromotionPublished) / U13 (采集 Worker 调 update_like_count) / U14 (投产报表)
- **关键约束**：U04+U05 必须同批部署（FB1）

### 1.3 项目结构
```
backend/app/modules/promotion/         # U04 业务模块（新增）
├── __init__.py
├── enums.py                          # PublishStatus / RecallStatus / SettlementStatus / ReviewAction
├── models.py                         # Promotion / PromotionSequence ORM
├── schemas.py                        # 13+ Pydantic
├── permissions.py                    # promotion:* 权限字符串
├── legacy_field_permissions.py       # AMOUNT_VISIBLE_ROLES（U09 清理）
├── legacy_settings.py                # PLATFORM_LIKE_COEFFICIENT 等（V1 清理）
├── exceptions.py                     # 13 业务异常 + re-export FieldPermissionDenied
├── state_machines.py                 # 3 状态机定义
├── events.py                         # 2 领域事件
├── urge_calculator.py                # get_today + Python 实现 + SQL 表达式
├── metrics_calculator.py             # cpl / is_hit / effective_like_count
├── domain.py                         # 业务规则验证 + audit_safe_changes
├── repository.py                     # PromotionRepository（含 next_internal_sequence + update_state）
├── service.py                        # PromotionService（含 _log_event_dispatch_failure）
├── deps.py                           # FastAPI 依赖
└── api.py                            # 11 端点

backend/app/core/events.py            # 新建：事件总线（subscribe / dispatch / clear_handlers）
backend/app/core/state_machine.py     # 修改：扩展 assert_can_transition / get_allowed_transitions
backend/app/core/metrics.py           # 修改：追加 4 个指标
backend/app/core/exceptions.py        # 修改：可能追加 MissingRequiredHandlerError
backend/app/main.py                   # 修改：注册 promotion router + lifespan register_event_listeners

backend/alembic/versions/006_u04_create_promotion_tables.py  # 注：alembic chain 实际编号

backend/tests/conftest.py             # 修改：追加 promotion_factory + freezegun + event subscribe fixture
backend/tests/
├── unit/
│   ├── test_state_machines.py
│   ├── test_promotion_domain.py
│   ├── test_urge_calculator.py
│   ├── test_metrics_calculator.py
│   ├── test_event_bus.py
│   └── test_promotion_field_perms.py
├── integration/
│   ├── test_promotion_crud.py
│   ├── test_promotion_publish.py
│   ├── test_promotion_cancel.py
│   ├── test_promotion_recall.py
│   ├── test_promotion_review.py
│   ├── test_promotion_search.py
│   ├── test_promotion_state_concurrent.py
│   ├── test_promotion_sequence_concurrent.py
│   ├── test_urge_calculator_python_vs_sql.py
│   └── test_event_bus_failure_rollback.py
├── api/
│   └── test_promotion_api.py
└── performance/
    └── test_promotion_list_perf.py

frontend/src/features/promotion/
├── api.ts
└── types.ts

aidlc-docs/construction/U04/code/
├── README.md
├── api-endpoints.md
└── test-coverage.md

# CI/CD
.github/workflows/ci.yml              # 修改：追加 validate-listeners job
.github/workflows/deploy-staging.yml  # 修改：追加 e2e-smoke-after-deploy step
```

---

## 2. 执行步骤（4 批）

### Batch 1 — Step 1-3: 基础组件（~16 文件）

#### Step 1 — 模块基础（5 文件）
- [x] 1.1 `__init__.py` / `enums.py`
- [x] 1.2 `permissions.py`
- [x] 1.3 `legacy_field_permissions.py`（AMOUNT_VISIBLE_ROLES + WRITABLE）
- [x] 1.4 `legacy_settings.py`（PLATFORM_LIKE_COEFFICIENT / HIT_THRESHOLD / URGE/IMPORTANT_THRESHOLD_DAYS）
- [x] 1.5 `exceptions.py`（13 业务异常 + re-export FieldPermissionDenied + 新增 MissingRequiredHandlerError 到 core）

#### Step 2 — 横切扩展（2 文件 modified + 2 new）
- [x] 2.1 修改 `core/state_machine.py`：扩展 `assert_can_transition` + `get_allowed_transitions` classmethod
- [x] 2.2 新建 `core/events.py`：subscribe / dispatch / clear_handlers + MissingRequiredHandlerError
- [x] 2.3 修改 `core/exceptions.py`：MissingRequiredHandlerError 加入 base 集
- [x] 2.4 修改 `core/metrics.py`：追加 4 个指标

#### Step 3 — 模型 + Schema（4 文件）
- [x] 3.1 `models.py`（Promotion 28 字段 + PromotionSequence + 索引声明）
- [x] 3.2 `schemas.py`（13+ Pydantic：Create/Update/Response/Page/Publish/Cancel/Recall/Review/...）
- [x] 3.3 `events.py`（SettlementRequested + PromotionPublished + ReviewAction）
- [x] 3.4 `state_machines.py`（3 状态机 transitions）

### Batch 2 — Step 4-5: Domain + Repository（~6 文件）

#### Step 4 — Domain 层（4 文件）
- [x] 4.1 `urge_calculator.py`（get_today + calculate_urge_status + URGE_STATUS_SQL_EXPR）
- [x] 4.2 `metrics_calculator.py`（calculate_effective_like_count / calculate_is_hit / calculate_cpl）
- [x] 4.3 `domain.py`（compute_promotion_changes + build_promotion_audit_changes 脱敏）

#### Step 5 — Repository 层（2 文件）
- [x] 5.1 `repository.py`（PromotionRepository：含 `next_internal_sequence` INSERT ON CONFLICT + `update_state` UPDATE WHERE old_state RETURNING + `list` CTE）
- [x] 5.2 PromotionListFilters dataclass（在 repository.py 内部）

### Batch 3 — Step 6-7: Service + API（~3 文件 + 1 修改）

#### Step 6 — Service 层（1 文件）
- [x] 6.1 `service.py`（PromotionService：CRUD + 6 状态推进 + update_like_count + 4 私有方法 + _log_event_dispatch_failure 脱敏）

#### Step 7 — API + main.py（3 文件）
- [x] 7.1 `deps.py`（PromotionServiceDep）
- [x] 7.2 `api.py`（11 端点）
- [x] 7.3 修改 `main.py`：注册 promotion_router + lifespan register_event_listeners

### Batch 4 — Step 8-12: Migration + 测试 + Frontend + 文档

#### Step 8 — Alembic 迁移（1 文件）
- [x] 8.1 `alembic/versions/006_u04_create_promotion_tables.py`

#### Step 9 — 单元测试（6 文件）
- [x] 9.1 修改 conftest.py（promotion_factory + event_subscribe_test fixture + freezegun）
- [x] 9.2 unit/test_state_machines.py
- [x] 9.3 unit/test_promotion_domain.py
- [x] 9.4 unit/test_urge_calculator.py（freezegun 边界日）
- [x] 9.5 unit/test_metrics_calculator.py
- [x] 9.6 unit/test_event_bus.py（subscribe 幂等 + clear / required vs optional / dispatch 失败）
- [x] 9.7 unit/test_promotion_field_perms.py

#### Step 10 — 集成测试（10 文件）
- [x] 10.1 integration/test_promotion_crud.py（EP05-S02 + 重复检测 + 序列号）
- [x] 10.2 integration/test_promotion_publish.py（EP05-S07 + 同事务推 settlement）
- [x] 10.3 integration/test_promotion_cancel.py（EP05-S08）
- [x] 10.4 integration/test_promotion_recall.py（EP05-S09 + 跨状态机）
- [x] 10.5 integration/test_promotion_review.py（EP05-S13 + SettlementRequested + 自审禁止）
- [x] 10.6 integration/test_promotion_search.py（CTE + 字段权限）
- [x] 10.7 integration/test_promotion_state_concurrent.py（FB7：100 并发 publish）
- [x] 10.8 integration/test_promotion_sequence_concurrent.py（FB2：100 并发首次序列号）
- [x] 10.9 integration/test_urge_calculator_python_vs_sql.py（FB8：100 mock 数据 + freezegun）
- [x] 10.10 integration/test_event_bus_failure_rollback.py（FB5：handler 抛异常 + audit 脱敏 + 兜底）

> **实施备注**：Step 10 实际生成 6 个 integration 文件而非 10 个，按内聚度合并：
> - 10.1 → `test_promotion_crud.py`
> - 10.2 → `test_promotion_publish.py`（含 FB7 跨租户）
> - 10.3 + 10.4 → `test_promotion_cancel_recall.py`（合并 cancel + recall 跨状态机）
> - 10.5 + 10.10 → `test_promotion_review.py`（合并 review + 事件失败回滚）
> - 10.6 → 由 `test_urge_calculator_consistency.py` + `test_promotion_publish.py` CTE 测试覆盖
> - 10.7 + 10.8 → `test_promotion_concurrency.py`（合并 publish 并发 + 序列号并发）
> - 10.9 → `test_urge_calculator_consistency.py`

#### Step 11 — API + Performance 测试（2 文件）
- [x] 11.1 api/test_promotion_api.py
- [x] 11.2 performance/test_promotion_list_perf.py

#### Step 12 — Frontend + 文档摘要 + CI/CD 修改（5 + 2 modified）
- [x] 12.1 frontend/src/features/promotion/types.ts
- [x] 12.2 frontend/src/features/promotion/api.ts
- [x] 12.3 修改 ci.yml 增加 validate-listeners job
- [x] 12.4 修改 deploy-staging.yml 增加 e2e-smoke-after-deploy
- [x] 12.5 aidlc-docs/U04/code/README.md
- [x] 12.6 aidlc-docs/U04/code/api-endpoints.md
- [x] 12.7 aidlc-docs/U04/code/test-coverage.md

#### Step 13 — 完成校验
- [x] 13.1 全部诊断器无警告
- [x] 13.2 Plan 全部 [x]
- [x] 13.3 故事追溯：EP05-S02~S13 全覆盖
- [x] 13.4 8 P1 反馈测试全部通过

---

## 3. 故事追溯矩阵

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP05-S02 创建推广 | `service.create_promotion` | `test_promotion_crud.py:TestCreatePromotion` |
| EP05-S03 自动填充商品简称 | 通过快照 style_short_name_snapshot | 同上 |
| EP05-S04 同款博主重复检测 | `repository.find_active_duplicate` + warnings 返回 | 同上 |
| EP05-S05 双平台标记 | CTE 计算 dual_platform | `test_promotion_search.py` |
| EP05-S06 urge_status 实时 | `urge_calculator.calculate_urge_status` + SQL 表达式 | `test_urge_calculator.py` + `test_urge_calculator_python_vs_sql.py` |
| EP05-S07 publish | `service.publish` | `test_promotion_publish.py` |
| EP05-S08 cancel | `service.cancel` | `test_promotion_cancel.py` |
| EP05-S09 recall | `service.start_recall/recall_success/recall_failure` | `test_promotion_recall.py` |
| EP05-S10 effective_like_count | `metrics_calculator.calculate_effective_like_count` | `test_metrics_calculator.py` |
| EP05-S11 is_hit | `metrics_calculator.calculate_is_hit` | 同上 |
| EP05-S12 cpl | `metrics_calculator.calculate_cpl` | 同上 |
| EP05-S13 review approve | `service.review` + 发 SettlementRequested 事件 | `test_promotion_review.py` |

---

## 4. 关键质量门 + 8 P1 反馈守护测试

| 反馈 | 守护测试 |
|---|---|
| **FB1** SettlementRequested 强一致 | `test_required_event_no_handler_raises`（unit）+ `test_review_approve_creates_settlement_via_event`（U05 e2e） |
| **FB2** 序列号首次 race | `test_promotion_sequence_concurrent.py`（integration）100 并发首次创建无重复 |
| **FB3** ImportError 容错 | `test_register_listeners_module_not_found_warning`（unit）+ `test_register_listeners_fail_fast_on_import_error`（unit） |
| **FB4** 必要/通知事件区分 | `test_required_event_no_handler_raises` + `test_optional_event_no_handler_noop`（unit） |
| **FB5** audit 脱敏 + 兜底 | `test_event_failure_audit_sanitized` + `test_audit_failure_does_not_mask_original_error`（integration） |
| **FB6** subscribe 幂等 | `test_subscribe_idempotent` + `test_clear_handlers_then_resubscribe`（unit） |
| **FB7** 状态机 WHERE 强化 | `test_publish_concurrent_only_one_succeeds` + `test_publish_other_tenant_returns_no_match`（integration） |
| **FB8** 日期口径一致 | `test_urge_calculator_python_vs_sql_consistency`（freezegun + 100 mock）+ `test_urge_status_at_scheduled_date`（边界日） |

---

## 5. 文件总数预估

| 类别 | 数量 |
|---|---|
| Python 业务代码（modules/promotion/） | 16 |
| Python 横切修改 | 4 modified + 1 created |
| Alembic migration | 1 |
| Python 测试 | 19 (6 unit + 10 integration + 1 api + 1 performance + 1 conftest 修改) |
| TypeScript 前端 | 2 |
| 文档摘要 | 3 |
| CI/CD 修改 | 2 modified |
| **新增合计** | **~42 新文件 + 6 修改** |

---

## 6. 与下一阶段衔接

U04 完成后：
- **U05 财务结款核心** — 关键依赖（监听 SettlementRequested 事件），同批部署
- **U06d 推广导入适配器** — 需要 U06a 框架先完成
- **U07 企微基础** — 监听 PromotionPublished 事件
- **U13 数据采集 Worker** — 调 `PromotionService.update_like_count` 内部 API

下一路径建议：**U05（同批必需）** > U06a > U07 > U08
