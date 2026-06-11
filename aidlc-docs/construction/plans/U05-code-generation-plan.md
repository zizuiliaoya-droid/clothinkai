# U05 代码生成计划（Code Generation Plan）

> 单元：U05 — 财务结款核心  
> 阶段：MVP 第 5 个单元（与 U04 同批部署激活 SettlementRequested 事件链路）  
> 节奏：**Plan A 分 4 批**（与 U04 一致，含 FB1-FB8 全部反馈守护）  
> 完全继承 U04 已落地代码契约 — 不重新评估 8 P1

---

## 1. 单元上下文

### 1.0 范围扩展（实施时发现 — Option A 修订版）

**发现**：U05 NFR/Infra Design 假设 `attachment` 表已存在，但实际 U01 仅落地 R2 helper 类，无 ORM 表。

**决策（Option A 修订）**：U05 触发补齐 **shared attachment 基础设施**（不是 U05 私有表），代码位置和命名放在 core 层语义下，后续 U02/U03 V1 迁移到统一引用。

#### 1.0.1 关键定位：shared 而非 U05 私有

| 项 | 决策 |
|---|---|
| 代码位置 | **`core/attachment.py` 内追加 ORM 模型 + Service 扩展**（不放 modules/finance） |
| 命名 | 通用名 `Attachment`（不是 `SettlementProof` 之类） |
| API 位置 | **`core/attachment_api.py`**（通用 router，不放 modules/finance/api.py） |
| Endpoint | `POST /api/attachments/upload-init`（不是 `/api/settlements/upload-proof-init`） |
| Migration | **shared 段优先**：007 先建 attachment 表（语义独立段），再建 U05 settlement 表和 FK |
| Settlement.payment_proof_attachment_id | FK to attachment.id（不退回裸 R2 key）|
| U02/U03 现有 attachment_key | **暂不强制返工**，标记 V1 migration: attachment_key → attachment_id |

#### 1.0.2 attachment 表字段（约 11 字段）

```sql
CREATE TABLE attachment (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenant(id) ON DELETE RESTRICT,
    created_by UUID REFERENCES "user"(id) ON DELETE SET NULL,
    bucket VARCHAR(16) NOT NULL,           -- public / private / credentials / backups
    r2_key VARCHAR(512) NOT NULL,          -- R2 内部 path（不暴露前端）
    purpose VARCHAR(32) NOT NULL,          -- settlement_proof / 其他模块自定义
    filename VARCHAR(255),                 -- 原始文件名
    mime_type VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'uploading',  -- uploading / ready
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CHECK (size_bytes >= 0),
    CHECK (bucket IN ('public', 'private', 'credentials', 'backups')),
    CHECK (status IN ('uploading', 'ready'))
);

CREATE INDEX idx_attachment_tenant_purpose ON attachment (tenant_id, purpose);
CREATE INDEX idx_attachment_status ON attachment (status, created_at);  -- V1 GC 任务用
CREATE UNIQUE INDEX uq_attachment_r2_key ON attachment (r2_key);  -- R2 path 唯一
```

**RLS**：启用 tenant_isolation 策略（attachment 跨租户访问由 application layer + RLS 双重防护）。

#### 1.0.3 AttachmentService 扩展（3 新方法）

```python
# core/attachment.py
class AttachmentService:
    async def create_upload_record(
        self,
        *,
        session: AsyncSession,
        tenant_id: UUID,
        created_by: UUID,
        bucket: BucketKind,
        purpose: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
    ) -> tuple[Attachment, str]:
        """创建 attachment 记录（status='uploading'）+ 生成 r2_key + 返回 presigned PUT URL.
        
        前端用返回的 presigned URL 直传 R2，完成后调 mark_uploaded。
        """
        ...
    
    async def mark_uploaded(
        self,
        *,
        session: AsyncSession,
        attachment_id: UUID,
        tenant_id: UUID,
    ) -> Attachment:
        """前端直传完成后调用，将 status 从 'uploading' 改为 'ready'."""
        ...
    
    async def get_by_id(
        self,
        *,
        session: AsyncSession,
        attachment_id: UUID,
    ) -> Attachment | None:
        """供下游（如 U05 ProofAttachmentValidator）取 attachment 记录做 6 项校验."""
        ...
```

#### 1.0.4 通用 attachment API（新建）

```python
# core/attachment_api.py
@router.post("/api/attachments/upload-init", response_model=AttachmentUploadInitResponse)
async def upload_init(payload, user, attachment_service):
    """初始化上传：创建 attachment 记录 + 返回 presigned PUT URL.
    
    purpose 字段决定 bucket / 校验规则；service 层维护白名单。
    """
    ...


@router.post("/api/attachments/{attachment_id}/complete", response_model=AttachmentResponse)
async def complete_upload(attachment_id, user, attachment_service):
    """前端直传 R2 完成后调用，标记 status='ready'."""
    ...
```

#### 1.0.5 文件影响

| 类别 | 改动 |
|---|---|
| `core/attachment.py` | 修改：追加 Attachment ORM + AttachmentSchemas + Service 3 方法 |
| `core/attachment_api.py` | **新建**：upload-init / complete 2 端点 + Pydantic Schemas |
| `app/main.py` | 修改：注册 attachment_router |
| Migration 007 | 拆成两段：上半段建 shared attachment 表（含 RLS）+ 下半段建 U05 settlement 表 + FK |
| 测试 | 新增 `tests/integration/test_attachment_upload.py`（基础流程 + status 状态机） |
| **总文件数** | 47 → **约 50**（+1 attachment_api.py + 1 test + 1 修改 main.py） |

**Settlement.payment_proof_attachment_id**：保持 FK to attachment.id，不退回裸 R2 key。FB4 6 项强校验完整可实施。

**U02/U03 现有 attachment_key 字段**：保留，标记 V1 migration: `style.main_image_key` → `main_image_attachment_id` 等迁移。

---

### 1.1 覆盖故事
EP06-S02~S08（7 个故事）— 完整财务结款生命周期。

### 1.2 依赖
- **强依赖**：U04（监听 SettlementRequested 事件）+ U01（attachment 框架 + state_machine 基类 + events 总线）+ U02（FieldPermissionDenied 复用）+ U03（无直接复用）
- **被依赖**：U06e (settlement 导入) / U14 (投产报表 — payment_amount 计算 ROI) / U16 (V2 order_adjustment 调整单)
- **关键约束**：U04+U05 必须同批部署（FB1）；U05 deploy 必须先于或等于 U04 deploy（CI gate 已就绪）

### 1.3 项目结构

```
backend/app/modules/finance/             # U05 业务模块（新增）
├── __init__.py
├── enums.py                             # SettlementStatus / ExtraItemType
├── permissions.py                       # settlement:read/write/review/pay 权限字符串
├── legacy_field_permissions.py          # 3 类：PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD（U09 清理）
├── exceptions.py                        # 18 业务异常 + re-export FieldPermissionDenied
├── models.py                            # Settlement / SettlementExtraItem / SettlementSequence ORM（无 is_active 字段，FB3）
├── schemas.py                           # 12+ Pydantic
├── state_machines.py                    # SettlementStatusMachine（5 状态 6 转移）
├── events.py                            # SettlementPaid 反向事件（required_handler=False）
├── domain.py                            # compute_settlement_changes + audit 脱敏 + format_settlement_no
├── attachment_validator.py              # ProofAttachmentValidator 6 项强校验（FB4）
├── repository.py                        # SettlementRepository（含 next_settlement_sequence + update_state + daily_summary_*）
├── service.py                           # SettlementService（含 _log_event_dispatch_failure + 事件分发）
├── listeners.py                         # on_settlement_requested 强一致 listener
├── deps.py                              # FastAPI 依赖
└── api.py                               # 8 端点 + DELETE 405（FB3）

backend/app/modules/promotion/listeners.py  # 新增：on_settlement_paid 通知类反向 listener（在 promotion 模块下）

backend/app/core/metrics.py              # 修改：追加 5 个 settlement 指标
backend/app/main.py                      # 修改：register_event_listeners 双向扩展（finance + promotion 反向）

backend/alembic/versions/007_u05_create_settlement_tables.py
backend/alembic/versions/008_u05_backfill_settlements.py        # FB8 独立 backfill PL/pgSQL
backend/alembic/versions/009_u05_seed_smoke_test_data.py        # staging 专用 seed migration（FB1 e2e-smoke）

backend/tests/conftest.py                # 修改：追加 settlement_factory + attachment_factory + cross_unit_event_bus fixture
backend/tests/
├── unit/
│   ├── test_settlement_state_machine.py
│   ├── test_settlement_domain.py
│   ├── test_attachment_validator.py     # FB4 6 项校验各 1 + 跨租户 4 层防御
│   ├── test_settlement_field_perms.py   # 3 类 ROLES 矩阵
│   └── test_settlement_paid_event.py    # required_handler=False 验证
├── integration/
│   ├── test_settlement_create_via_event.py     # FB1+FB3+FB6 三重幂等 + flush
│   ├── test_settlement_review.py               # approve/reject + 自审禁止 + UPDATE WHERE
│   ├── test_settlement_extra_item.py           # 增加 + total 重算 + 状态约束
│   ├── test_settlement_fill_payment.py         # 状态推进
│   ├── test_settlement_mark_paid.py            # attachment 6 项 + SettlementPaid 反向
│   ├── test_settlement_concurrency.py          # FB7：100 并发 mark_paid + 跨租户
│   ├── test_settlement_attachment_cross_tenant.py  # FB4：跨租户 attachment 4 层防御
│   ├── test_daily_summary_as_of.py             # FB7 口径 B
│   ├── test_daily_summary_activity.py          # FB7 口径 A 含 audit JOIN
│   ├── test_settlement_paid_listener.py        # U04 端反向 listener + 缺失容忍
│   ├── test_settlement_immutable.py            # FB3 DELETE 405 + 零级联
│   └── test_e2e_review_to_paid.py              # 端到端 J4 完整旅程
├── api/
│   └── test_settlement_api.py                  # 鉴权 + OpenAPI + DELETE 405
└── performance/
    ├── test_settlement_list_perf.py            # 10K settlement
    └── test_daily_summary_perf.py              # FB7 双口径性能

frontend/src/features/finance/
├── api.ts
└── types.ts

aidlc-docs/construction/U05/code/
├── README.md
├── api-endpoints.md
└── test-coverage.md

# CI/CD
.github/workflows/ci.yml                  # 修改：可选追加 promotion.listeners grep（不阻塞）
.github/workflows/deploy-staging.yml      # 修改：启用真实 e2e-smoke（U04 batch 4 是 placeholder）
```

---

## 2. 执行步骤（4 批）

### Batch 1 — Step 1-3: 基础组件（~13 文件）

#### Step 1 — 模块基础（5 文件）
- [x] 1.1 `__init__.py` / `enums.py`（SettlementStatus 5 + ExtraItemType 3）
- [x] 1.2 `permissions.py`（settlement:read/write/review/pay）
- [x] 1.3 `legacy_field_permissions.py`（3 类：PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD，U09 清理）
- [x] 1.4 `exceptions.py`（18 业务异常 + re-export FieldPermissionDenied）

#### Step 2 — 横切扩展（1 修改）
- [x] 2.1 修改 `core/metrics.py`：追加 5 个 settlement 指标

> 注：U04 batch 1 已落地的 core/events.py / core/exceptions.MissingRequiredHandlerError / core/state_machine.py 框架，U05 直接消费，无需改动。

#### Step 3 — 模型 + Schema（4 文件）
- [x] 3.1 `models.py`（Settlement 22 字段无 is_active + SettlementExtraItem + SettlementSequence + 12 索引声明，FB3）
- [x] 3.2 `schemas.py`（12+ Pydantic：Create/Update/Response/Page/Review/PaymentAmount/PaymentProof/ExtraItem/DailySummaryAsOf/DailySummaryActivity）
- [x] 3.3 `events.py`（SettlementPaid 反向事件，required_handler=False，FB5）
- [x] 3.4 `state_machines.py`（SettlementStatusMachine 5 状态 6 转移）

### Batch 2 — Step 4-5: Domain + Repository + 共享 attachment 基础设施补齐（~7 文件 + 1 修改）

#### Step 4 — Domain 层（3 文件）+ shared attachment 补齐（1 修改 + 1 新建）
- [x] 4.0 修改 `core/attachment.py`：追加 Attachment ORM + 3 新方法（get_by_id / create_upload_record / mark_uploaded）+ AttachmentResponse Schema（**shared 基础设施补齐**）
- [x] 4.0a 新建 `core/attachment_api.py`：upload-init / complete 2 通用端点 + Pydantic Schemas（不放 modules/finance，**通用语义**）
- [x] 4.1 `domain.py`（compute_settlement_changes + build_settlement_audit_changes 脱敏 + format_settlement_no）
- [x] 4.2 `attachment_validator.py`（ProofAttachmentValidator 基于 Attachment ORM 的 6 项强校验 + 跨租户 4 层防御，FB4）

> 注：U05 不需要 urge_calculator / metrics_calculator（U04 已实现，U05 settlement 字段全部持久化无衍生字段）

#### Step 5 — Repository 层（2 文件）
- [x] 5.1 `repository.py`（SettlementRepository：next_settlement_sequence INSERT ON CONFLICT + update_state UPDATE WHERE 旧状态 + daily_summary_as_of + daily_summary_activity）
- [x] 5.2 `SettlementListFilters` dataclass（在 repository.py 内部）

### Batch 3 — Step 6-7: Service + API + 双向 listener（~6 文件 + 1 修改）

#### Step 6 — Service 层 + 双向 Listener（3 文件）
- [x] 6.1 `service.py`（SettlementService：4 状态推进 + add_extra_item + daily_summary × 2 + 4 私有方法 + _log_event_dispatch_failure 脱敏 + 失败处理不对称）
- [x] 6.2 `listeners.py`（finance.listeners.on_settlement_requested 强一致 + handler 内 flush，FB1+FB6）
- [x] 6.3 新建 `modules/promotion/listeners.py`（promotion.listeners.on_settlement_paid 通知类反向，FB5）

#### Step 7 — API + main.py（3 文件）
- [x] 7.1 `deps.py`（SettlementServiceDep）
- [x] 7.2 `api.py`（8 端点 + DELETE /settlements/{id} 硬编码 405，FB3）
- [x] 7.3 修改 `main.py`：注册 attachment_router（shared）+ register_event_listeners 双向扩展（第 1 步 finance fail fast + 第 2 步 promotion 通知类容忍）

### Batch 4 — Step 8-12: Migration + 测试 + Frontend + 文档

#### Step 8 — Alembic 迁移（3 文件）
- [x] 8.1 `alembic/versions/007_u05_create_settlement_tables.py`（**两段结构**：上半段建 shared attachment 表 + RLS + 索引；下半段建 U05 settlement / extra_item / sequence + FK to attachment + 12 索引 + 2 RLS + 永久 UNIQUE，FB3）
- [x] 8.2 `alembic/versions/008_u05_backfill_settlements.py`（FB8 PL/pgSQL DO $$ + 复用 settlement_sequence + 不可逆 downgrade）
- [x] 8.3 `alembic/versions/009_u05_seed_smoke_test_data.py`（staging 专用，仅 ENVIRONMENT=staging 跑）

#### Step 9 — 单元测试（5 文件）
- [x] 9.1 修改 conftest.py（settlement_factory + attachment_factory + cross_unit_event_bus fixture）
- [x] 9.2 unit/test_settlement_state_machine.py（6 transitions + assert_can_transition + 跨状态拒绝）
- [x] 9.3 unit/test_settlement_domain.py（audit 脱敏 + dict diff + format_settlement_no）
- [x] 9.4 unit/test_attachment_validator.py（FB4 6 项各 1 + 跨租户 4 层防御）
- [x] 9.5 unit/test_settlement_field_perms.py（3 类 ROLES 矩阵 + finance 不可写 payment_amount）
- [x] 9.6 unit/test_settlement_paid_event.py（required_handler=False + 与 SettlementRequested 不对称）

#### Step 10 — 集成测试（12 文件，按内聚可合并到 ~7-8 个）
- [x] 10.1 integration/test_settlement_create_via_event.py（FB1+FB3+FB6 三重幂等 + flush 立即暴露错误）
- [x] 10.2 integration/test_settlement_review.py（approve/reject + 自审禁止 + UPDATE WHERE 旧状态）
- [x] 10.3 integration/test_settlement_extra_item.py（增加 + total 重算 + 状态约束 + 字段权限）
- [x] 10.4 integration/test_settlement_fill_payment.py（待付款 → 待财务付款）
- [x] 10.5 integration/test_settlement_mark_paid.py（FB4：attachment 6 项强校验 + SettlementPaid 反向事件）
- [x] 10.6 integration/test_settlement_concurrency.py（FB7：100 并发 mark_paid + 跨租户 0 行匹配）
- [x] 10.7 integration/test_settlement_attachment_cross_tenant.py（FB4：跨租户 4 层防御 + Sentry mock + audit 验证）
- [x] 10.8 integration/test_daily_summary_as_of.py（FB7 口径 B + outstanding_total 计算）
- [x] 10.9 integration/test_daily_summary_activity.py（FB7 口径 A + audit_log JOIN）
- [x] 10.10 integration/test_settlement_paid_listener.py（U04 端反向 listener 同步 + 缺失容忍）
- [x] 10.11 integration/test_settlement_immutable.py（FB3：DELETE 405 + promotion 软删零级联）
- [x] 10.12 integration/test_e2e_review_to_paid.py（端到端 J4：U04 review approve → U05 整个流程 → SettlementPaid 反向同步）
- [x] 10.13 integration/test_attachment_upload.py（**shared attachment 基础设施**：upload-init → 直传 R2 → mark_uploaded 状态机 + 跨租户隔离）

> **实施备注**：参照 U04 经验，按内聚度合并部分测试文件以减少文件数（最终生成 6-8 个文件，每个 200-400 行）

#### Step 11 — API + Performance 测试（3 文件）
- [x] 11.1 api/test_settlement_api.py（鉴权 + OpenAPI 8 paths + DELETE 405）
- [x] 11.2 performance/test_settlement_list_perf.py（10K settlement P95 ≤ 200ms）
- [x] 11.3 performance/test_daily_summary_perf.py（双口径 P95 验证）

#### Step 12 — Frontend + 文档摘要 + CI/CD 修改（5 + 2 modified）
- [x] 12.1 frontend/src/features/finance/types.ts
- [x] 12.2 frontend/src/features/finance/api.ts
- [x] 12.3 修改 ci.yml（可选追加 promotion.listeners grep，不阻塞）
- [x] 12.4 修改 deploy-staging.yml（**启用真实 e2e-smoke** — U04 batch 4 是 placeholder）
- [x] 12.5 aidlc-docs/U05/code/README.md
- [x] 12.6 aidlc-docs/U05/code/api-endpoints.md
- [x] 12.7 aidlc-docs/U05/code/test-coverage.md

#### Step 13 — 完成校验
- [x] 13.1 全部诊断器无警告
- [x] 13.2 Plan 全部 [x]
- [x] 13.3 故事追溯：EP06-S02~S08 全覆盖
- [x] 13.4 8 P1 反馈测试全部通过
- [x] 13.5 双向 listener 注册框架完整（finance fail fast + promotion 容忍）

---

## 3. 故事追溯矩阵

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP06-S02 自动生成结算单 | `listeners.on_settlement_requested` | `test_settlement_create_via_event.py` |
| EP06-S03 PR 主管核查 approve | `service.review` action="approve" | `test_settlement_review.py` |
| EP06-S04 PR 主管驳回 reject | `service.review` action="reject" | `test_settlement_review.py` |
| EP06-S05 增加结算项 | `service.add_extra_item` | `test_settlement_extra_item.py` |
| EP06-S06 填写付款金额 | `service.fill_payment_amount` | `test_settlement_fill_payment.py` |
| EP06-S07 财务上传付款截图 | `service.upload_payment_proof` + `attachment_validator.validate` | `test_settlement_mark_paid.py` + `test_settlement_attachment_cross_tenant.py` |
| EP06-S08 当日结算汇总 | `service.get_daily_summary_as_of/activity` | `test_daily_summary_as_of.py` + `test_daily_summary_activity.py` |

---

## 4. 关键质量门 + 8 P1 反馈守护测试（继承 U04）

| 反馈 | 守护测试 |
|---|---|
| **FB1** SettlementRequested 强一致 | `test_settlement_create_via_event.py::test_handler_creates_settlement` + `test_e2e_review_to_paid.py`（端到端） |
| **FB2** 序列号原子（继承 U04） | `test_settlement_create_via_event.py::test_concurrent_first_create_no_duplicates`（100 并发首次） |
| **FB3** 财务记录永久不可替换 | `test_settlement_immutable.py::test_delete_returns_405` + `test_settlement_immutable.py::test_unique_promotion_id_permanent` + `test_settlement_immutable.py::test_promotion_soft_delete_no_cascade` |
| **FB4** Attachment 6 项强校验 | `test_attachment_validator.py`（unit 6 项各 1）+ `test_settlement_attachment_cross_tenant.py`（integration 4 层防御）|
| **FB5** audit 脱敏 + 兜底 + 反向事件容忍 | `test_settlement_create_via_event.py::test_event_failure_audit_sanitized` + `test_settlement_paid_listener.py::test_settlement_paid_no_listener_no_op` |
| **FB6** subscribe 幂等 + flush 立即暴露 | `test_settlement_create_via_event.py::test_handler_flush_exposes_error_immediately` + 复用 U04 unit/test_event_bus.py |
| **FB7** 状态机 WHERE 强化 + 双口径汇总 | `test_settlement_concurrency.py`（FB7 状态机）+ `test_daily_summary_as_of.py` + `test_daily_summary_activity.py`（FB7 双口径） |
| **FB8** 日期口径一致 | `test_daily_summary_as_of.py::test_uses_get_today` + `test_daily_summary_activity.py::test_freezegun_boundary_day` |

---

## 5. 文件总数预估

| 类别 | 数量 |
|---|---|
| Python 业务代码（modules/finance/） | 17 |
| **Shared attachment 基础设施补齐**（core/attachment.py 修改 + core/attachment_api.py 新建） | **1 修改 + 1 新建** |
| Python 横切修改 | 3（main.py 双向 listener + attachment router / metrics.py / + 新增 modules/promotion/listeners.py）|
| Alembic migration | 3（007 含 attachment 上半段 + settlement 下半段 / 008 / 009 staging seed） |
| Python 测试 | ~23（5 unit + ~13 integration 含 attachment_upload + 1 api + 2 performance + 1 conftest 修改） |
| TypeScript 前端 | 2 |
| 文档摘要 | 3 |
| CI/CD 修改 | 2 |
| **新增合计** | **~50 新文件 + 5 修改**（含 attachment 基础设施补齐） |

> 实际数字会按内聚合并部分集成测试（参照 U04 12 → 6 的经验，最终可能 47 → 42-44）

---

## 6. 与下一阶段衔接

U05 完成后 + 与 U04 同批部署激活后：
- ✅ EP05-S13 + EP06-S02~S08 端到端：PR 主管审核 → SettlementRequested → settlement 创建 → 财务付款 → SettlementPaid 反向同步
- ✅ MVP 财务流程闭环
- ✅ MVP 5/12 子单元交付（U01 + U02 + U03 + U04 + U05）
- 下一路径建议：
  - **U06a 统一导入框架**（独立分支，无依赖）
  - 或 **U07 企微基础**（监听 PromotionPublished 事件，预留 V1 通知功能）

---

## 7. 节奏决策

**Plan A 4 批 review 节奏**（与 U04 一致）：

| 批次 | 范围 | 文件数 | 复杂度 |
|---|---|---|---|
| Batch 1 | Step 1-3：基础组件 | ~13 | 低 |
| Batch 2 | Step 4-5：Domain + Repository | ~5 | 中（attachment_validator 含跨租户 4 层防御）|
| Batch 3 | Step 6-7：Service + API + 双向 listener | ~6 + 1 修改 | **高**（双向 listener + 失败处理不对称） |
| Batch 4 | Step 8-12：Migration + 测试 + Frontend + 文档 | ~23 + 2 修改 | 中（migration PL/pgSQL + e2e-smoke 启用） |

**理由**：
- 与 U04 经验一致（4 批被验证有效）
- Batch 3 是关键风险点（双向 listener 注册 + 失败处理不对称是 U05 最容易出错的部分）
- Batch 4 最大但模板化程度高（migration 已有 PL/pgSQL 完整代码 + 测试已有矩阵）

---

**等待用户回复"继续"或"A"批准 Plan A 节奏，开始 Batch 1 生成。**
