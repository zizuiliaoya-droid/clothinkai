## U05 逻辑组件（Logical Components）

> 单元：U05 — 财务结款核心  
> 范围：U05 新增组件 + 与 U04 ↔ U05 双向 listener 契约

---

## 1. 组件总览

### 1.1 U05 新增组件清单

| # | 组件 | 类型 | 文件位置 | 复用来源 |
|---|---|---|---|---|
| 1 | `Settlement` ORM | Domain | `modules/finance/models.py` | TenantScopedModel (U01) |
| 2 | `SettlementExtraItem` ORM | Domain | `modules/finance/models.py` | TenantScopedModel |
| 3 | `SettlementSequence` ORM | Domain | `modules/finance/models.py` | TenantScopedModel + U04 模式 |
| 4 | `SettlementStatus` / `ExtraItemType` Enum | Domain | `modules/finance/enums.py` | — |
| 5 | `SettlementCreate/Update/Response/Page` Pydantic | Schema | `modules/finance/schemas.py` | — |
| 6 | `SettlementReview/PaymentAmount/PaymentProof` 请求 Schema | Schema | 同上 | — |
| 7 | `SettlementListFilters` dataclass | Schema | `modules/finance/repository.py` | — |
| 8 | `SettlementRepository` | Repository | `modules/finance/repository.py` | TenantScopedModel + Session + U04 update_state 模式 |
| 9 | `SettlementService` | Service | `modules/finance/service.py` | @audit / @require_permission |
| 10 | **`SettlementStatusMachine`** | State Machine | `modules/finance/state_machines.py` | **U01 core/state_machine.py + U04 模式** |
| 11 | **`SettlementPaid` Event** | Event | `modules/finance/events.py` | core/events.py |
| 12 | **`ProofAttachmentValidator`** | Domain Helper | `modules/finance/attachment_validator.py` | U01 AttachmentService（FB4 独立封装） |
| 13 | **`on_settlement_requested` Listener** | Listener | `modules/finance/listeners.py` | core/events.py（强一致） |
| 14 | **`on_settlement_paid` Listener**（U04 端） | Listener | `modules/promotion/listeners.py` | **U04 端新建**（通知类反向） |
| 15 | `compute_settlement_changes` / `build_settlement_audit_changes` / `format_settlement_no` | Domain | `modules/finance/domain.py` | 复用 U02/U03/U04 模式 |
| 16 | `PAYMENT_VISIBLE_ROLES` + `PAYMENT_WRITABLE_ROLES` + `PROOF_UPLOAD_ROLES` | Legacy | `modules/finance/legacy_field_permissions.py` | 待 U09 清理 |
| 17 | 业务异常 18 个 | Exception | `modules/finance/exceptions.py` | 继承 U01 base |
| 18 | `settlement_state_transitions_total` / `settlement_created_via_event_total` / `settlement_sequence_lock_duration_seconds` / `attachment_validation_failures_total` / `settlement_paid_sync_no_match_total` Metric | Metric | `core/metrics.py`（修改） | prometheus-client |
| 19 | `settlement_router` | API | `modules/finance/api.py` | FastAPI Router |
| 20 | `get_settlement_service` | Dependency | `modules/finance/deps.py` | U01 Session 注入 |
| 21 | `register_event_listeners` 扩展 | Bootstrap | `app/main.py`（修改） | U04 batch 4 已搭框架 |

### 1.2 复用 U01-U04 组件（不重复定义）

| 组件 | 来源 | U05 复用方式 |
|---|---|---|
| `TenantScopedModel` | U01 | Settlement / SettlementExtraItem / SettlementSequence 继承 |
| `AsyncSession` 依赖注入 | U01 | SettlementRepository 注入 |
| `AsyncSessionBypass` | U01 | 失败 audit 独立 session（attachment 跨租户告警） |
| `AuditService` + `@audit` | U01 | service 层方法装饰 |
| `Permission` + `@require_permission` | U01 | API 端点装饰 |
| `core/errors.register_error_handlers` | U01 | 全局异常映射（含 405） |
| `tenancy.current_tenant_id()` / `bypass_rls_ctx` / `user_id_ctx` | U01 | service / repository 取租户 / 跨租户 audit |
| `StateMachine` 基类 | U01 / U04 | SettlementStatusMachine 继承（5 状态 6 转移） |
| **`core/events.py` 事件总线** | U04 | 监听 SettlementRequested + 发出 SettlementPaid |
| `RateLimiter` | U01 | API 默认应用 |
| `RequestIdMiddleware` / `TenancyMiddleware` | U01 | 透明继承 |
| `prometheus-fastapi-instrumentator` | U01 | API 指标自动暴露 |
| `Sentry SDK` | U01 | 异常自动捕获 + tag = "module=finance" |
| `FieldPermissionDenied` 异常 | U02 | re-export 自 modules/product/exceptions（与 U03/U04 同模式） |
| `legacy_field_permissions` 模式 | U02 P-U02-02 | 适配 PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD |
| **`urge_calculator.get_today`** | U04 (FB8) | daily-summary 时区一致入口 |
| `format_internal_code` 模式 | U04 | format_settlement_no 完全相同模式 |
| `SettlementRequested` 事件 dataclass | U04 | U05 端 listener 直接 import |
| `MissingRequiredHandlerError` | U04 (core/exceptions) | dispatch 时 U04 端抛 |
| `SequenceOverflowError` 模式 | U04 | settlement_sequence > 9999 抛错 |
| `IllegalStateTransitionError` / `StateTransitionConflictError` | U04 | 直接 import + 复用 |
| `AttachmentService` | U01 | ProofAttachmentValidator 内部依赖 |
| `update_state` UPDATE WHERE 旧状态模式 | U04 (FB7) | settlement.update_state 完全一致（**WHERE 不含 is_active，FB3**） |

---

## 2. 组件依赖图（含 U04 ↔ U05 双向 listener）

```mermaid
graph TD
    subgraph "Layer: API"
        SettlementRouter[settlement_router<br/>api.py<br/>含 DELETE 405 端点]
    end
    
    subgraph "Layer: Service"
        SettlementService[SettlementService]
    end
    
    subgraph "Layer: State Machine"
        SSM[SettlementStatusMachine<br/>5 状态 6 转移]
    end
    
    subgraph "Layer: Domain"
        AttachmentValidator[ProofAttachmentValidator<br/>FB4 6 项强校验]
        SettlementDomain[domain.py<br/>compute_changes + audit 脱敏 + format_settlement_no]
    end
    
    subgraph "Layer: Repository"
        SettlementRepo[SettlementRepository<br/>含 next_settlement_sequence + update_state + daily_summary_*]
    end
    
    subgraph "Layer: Models / Schemas"
        Models[Settlement / SettlementExtraItem / SettlementSequence ORM<br/>**无 is_active 字段 (FB3)**]
        Schemas[Pydantic Schemas]
        Enums[SettlementStatus / ExtraItemType]
    end
    
    subgraph "Layer: Listeners"
        FinanceListener[modules/finance/listeners.py<br/>on_settlement_requested<br/>**强一致 + flush (FB1+FB6)**]
        PromotionListener[modules/promotion/listeners.py<br/>on_settlement_paid<br/>**通知类 (FB5)**]
    end
    
    subgraph "Layer: Events"
        SettlementPaidEvent[SettlementPaid<br/>required_handler=False]
    end
    
    subgraph "Cross-cutting (U01)"
        Audit[AuditService + @audit]
        Perms[@require_permission]
        DB[AsyncSession + RLS]
        DBBypass[AsyncSessionBypass<br/>跨租户 audit]
        Tenancy[tenancy.current_tenant_id]
        Errors[core/exceptions]
        Metrics[core/metrics<br/>5 个 U05 新指标]
        StateMachineBase[U01 core/state_machine.py 基类]
        AttachmentSvc[AttachmentService<br/>U01]
        EventBus[core/events.py<br/>subscribe / dispatch / clear_handlers<br/>U04 引入]
    end
    
    subgraph "Reused from U02"
        FieldPermDenied[FieldPermissionDenied 异常]
    end
    
    subgraph "Reused from U04"
        SettlementRequestedEvent[SettlementRequested<br/>required_handler=True]
        MissingRequiredHandlerError[MissingRequiredHandlerError]
        GetToday[urge_calculator.get_today<br/>FB8 时区入口]
        UpdateStatePattern[update_state UPDATE WHERE 旧状态<br/>FB7]
    end
    
    subgraph "Transition (U05 → U09 清理)"
        LegacyPerms[legacy_field_permissions<br/>3 类: PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD]
    end
    
    SettlementRouter --> SettlementService
    SettlementService --> SSM
    SettlementService --> SettlementRepo
    SettlementService --> AttachmentValidator
    SettlementService --> SettlementDomain
    SettlementService --> Audit
    SettlementService --> LegacyPerms
    SettlementService --> FieldPermDenied
    SettlementService --> EventBus
    SettlementService --> GetToday
    SettlementService --> SettlementPaidEvent
    
    AttachmentValidator --> AttachmentSvc
    AttachmentValidator --> DBBypass
    AttachmentValidator --> Metrics
    
    SSM --> StateMachineBase
    SSM --> Errors
    
    SettlementRepo --> DB
    SettlementRepo --> Tenancy
    SettlementRepo --> Metrics
    SettlementRepo --> UpdateStatePattern
    Models --> DB
    
    SettlementRouter --> Perms
    SettlementRouter --> Errors
    SettlementRouter --> Metrics
    
    %% 强一致正向：U04 → U05
    SettlementRequestedEvent -.dispatch SettlementRequested.-> EventBus
    EventBus -.required_handler=True.-> FinanceListener
    FinanceListener -.session.add Settlement.-> Models
    FinanceListener -.同事务 + flush.-> DB
    
    %% 通知类反向：U05 → U04
    SettlementService -.dispatch SettlementPaid.-> EventBus
    EventBus -.required_handler=False.-> PromotionListener
    PromotionListener -.UPDATE promotion.settlement_status.-> DB
```

---

## 3. 4 层架构 + 独立子层

### 3.1 API Layer (`api.py`)
- 8 个业务端点（review / extra-items / payment-amount / payment-proof / list / get / daily-summary × 2）
- **DELETE /settlements/{id} → 405**（防御深度，FB3）
- `@require_permission("settlement:read|review|write|pay")`
- 输入 Pydantic 校验 / 输出 Response Schema
- 错误处理：抛业务异常 → 全局 error handler

### 3.2 Service Layer (`service.py`)
- 协调 Domain + Repository + Audit + State Machine + Events + Validator
- `_check_payment_write_permission`（字段写权限）
- `_to_response`（按角色过滤敏感字段 + attachment 签名 URL）
- `_log_event_dispatch_failure`（**完全复用 U04 模式 + blocking=False 不对称，FB5**）

### 3.3 State Machine Sub-layer
- `SettlementStatusMachine` 单独类
- 5 状态 6 转移（详见 functional-design business-rules §4）
- 不依赖 ORM / Session（保持纯 Python）

### 3.4 Listeners Sub-layer（**新模式：双向 listener 协作**）

| 文件 | 监听事件 | 类型 | 注册位置 |
|---|---|---|---|
| `modules/finance/listeners.py::on_settlement_requested` | SettlementRequested | 强一致（FB1） | main.py register_event_listeners 第 1 步 |
| `modules/promotion/listeners.py::on_settlement_paid` | SettlementPaid | 通知类（FB5） | main.py register_event_listeners 第 2 步 |

**注**：U04 batch 4 已落地 register_event_listeners 框架，含第 1 步加载 finance.listeners 的 ModuleNotFoundError 容错；U05 实施时新增 `modules/promotion/listeners.py` + 在 register 函数追加第 2 步加载（缺失也不阻塞，因 SettlementPaid required_handler=False）。

### 3.5 Domain Layer
- `attachment_validator.py`：ProofAttachmentValidator 6 项校验（FB4）
- `events.py`：SettlementPaid dataclass（required_handler=False）
- `domain.py`：compute_settlement_changes + build_settlement_audit_changes 脱敏 + format_settlement_no（与 U04 format_internal_code 同模式）

### 3.6 Repository Layer (`repository.py`)
- `next_settlement_sequence`（INSERT ON CONFLICT，复用 U04 FB2 模式）
- `find_by_promotion_id`（幂等 SELECT 兜底）
- `update_state`（UPDATE WHERE id+tenant_id+旧 state RETURNING — **不含 is_active，FB3**）
- `daily_summary_as_of`（GROUP BY，FB7）
- `daily_summary_activity`（含 audit_log JOIN，FB7）
- 自动应用 RLS

---

## 4. 关键组件细节

### 4.1 SettlementService（核心 Service）

```python
class SettlementService:
    def __init__(
        self,
        session: AsyncSession,
        attachment_service: AttachmentService,
    ):
        self._session = session
        self._repo = SettlementRepository(session)
        self._validator = ProofAttachmentValidator(attachment_service)
        self._roles = RoleRepository(session)
        self._audit = AuditService(session)
    
    # 状态推进
    async def review(self, id, payload, user) -> SettlementResponse: ...
    async def add_extra_item(self, id, payload, user) -> SettlementResponse: ...
    async def fill_payment_amount(self, id, payload, user) -> SettlementResponse: ...
    async def upload_payment_proof(self, id, payload, user) -> SettlementResponse: ...
    async def resubmit(self, id, user) -> SettlementResponse: ...  # 已驳回 → 待核查
    
    # 读查询
    async def get_settlement(self, id, user) -> SettlementResponse: ...
    async def list_settlements(self, filters, page, page_size, user) -> SettlementPage: ...
    async def get_daily_summary_as_of(self, date, user) -> DailySummaryAsOfResponse: ...
    async def get_daily_summary_activity(self, date, user) -> DailySummaryActivityResponse: ...
    
    # 私有
    async def _check_payment_write_permission(self, payload, user) -> None: ...
    async def _check_extra_item_writable(self, user) -> None: ...
    async def _check_proof_upload_role(self, user) -> None: ...
    async def _to_response(self, s, user, *, include_signed_url=True) -> SettlementResponse: ...
    async def _log_event_dispatch_failure(self, event, exc, user, *, blocking) -> None: ...
```

### 4.2 SettlementRepository

```python
class SettlementRepository:
    async def get_by_id(self, id, *, raise_if_missing=True) -> Settlement | None: ...
    async def get_by_settlement_no(self, settlement_no) -> Settlement | None: ...
    async def find_by_promotion_id(self, promotion_id) -> Settlement | None: ...
    
    async def next_settlement_sequence(
        self, *, tenant_id: UUID, date_key: date,
    ) -> int:
        """原子获取下一序列号（INSERT ON CONFLICT DO UPDATE RETURNING，复用 U04 FB2）。"""
        ...
    
    async def update_state(
        self,
        *,
        settlement_id: UUID,
        tenant_id: UUID,
        from_state_field: str,  # "settlement_status"
        from_state_value: str,
        to_state_value: str,
        extra_fields: dict | None = None,
    ) -> Settlement | None:
        """乐观并发 UPDATE WHERE old_state RETURNING（FB7 模式，无 is_active 字段）。
        
        Returns None if no row matched（并发冲突 / 跨租户）。
        """
        ...
    
    async def list_with_filters(
        self,
        *,
        filters: SettlementListFilters,
        page: int,
        page_size: int,
        current_user_id: UUID | None,  # PR 角色限自己
    ) -> tuple[Sequence[Settlement], int]: ...
    
    async def daily_summary_as_of(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict]:
        """口径 B：截至当日各状态快照（FB7 GROUP BY）。"""
        ...
    
    async def daily_summary_activity(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict]:
        """口径 A：当天发生的动作（FB7 含 audit_log JOIN）。"""
        ...
    
    def add(self, settlement: Settlement) -> None: ...
```

### 4.3 SettlementStatusMachine

```python
# modules/finance/state_machines.py
from typing import ClassVar
from app.core.exceptions import IllegalStateTransitionError
from app.core.state_machine import TransitionRule
from app.modules.finance.enums import SettlementStatus


_ROLE_PR_MANAGER = "pr_manager"
_ROLE_FINANCE = "finance"
_ROLE_ADMIN = "admin"
_ROLE_PR = "pr"
_ROLE_SYSTEM = "system"


class SettlementStatusMachine:
    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="approve",
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("review_reason",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("review_reason",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="fill_payment",
            to_state=SettlementStatus.PENDING_FINANCE.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("payment_amount",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_FINANCE.value,
            action="mark_paid",
            to_state=SettlementStatus.PAID.value,
            actor_roles=(_ROLE_FINANCE, _ROLE_ADMIN),
            required_fields=("payment_date", "payment_proof_attachment_id"),
        ),
        TransitionRule(
            from_state=SettlementStatus.REJECTED.value,
            action="resubmit",
            to_state=SettlementStatus.PENDING_REVIEW.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN, _ROLE_SYSTEM),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls, from_state, to_state, action: str
    ) -> None:
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        to_v = to_state.value if isinstance(to_state, SettlementStatus) else to_state
        for t in cls.transitions:
            if t.from_state == from_v and t.to_state == to_v and t.action == action:
                return
        raise IllegalStateTransitionError(
            f"SettlementStatusMachine: 不允许从 {from_v} 通过 {action} 转移到 {to_v}",
            details={"machine": "settlement_status", "from_state": from_v, "to_state": to_v, "action": action},
        )

    @classmethod
    def get_allowed_transitions(cls, from_state) -> list[tuple[str, str]]:
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]
```

### 4.4 ProofAttachmentValidator（FB4 详细代码见 nfr-design-patterns.md §3.2.1）

### 4.5 events.py（FB5）

```python
# modules/finance/events.py
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True)
class SettlementPaid:
    """U05 → U04 反向通知类事件（FB5：required_handler=False）。"""
    event_type: ClassVar[str] = "SettlementPaid"
    required_handler: ClassVar[bool] = False
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    payment_amount: Decimal
    payment_date: date
    paid_by: UUID
```

### 4.6 listeners.py（双向）

```python
# modules/finance/listeners.py（强一致正向）
from app.core.events import subscribe
from app.modules.promotion.events import SettlementRequested
# ... 详见 nfr-design-patterns.md §5

async def on_settlement_requested(
    event: SettlementRequested, session: AsyncSession,
) -> None:
    """同事务 + flush + 三重幂等（FB1+FB3+FB6）。"""
    ...

def register() -> None:
    subscribe("SettlementRequested", on_settlement_requested)


# modules/promotion/listeners.py（U05 实施时新建，通知类反向）
from app.core.events import subscribe
from app.modules.finance.events import SettlementPaid
# ... 详见 nfr-design-patterns.md §5

async def on_settlement_paid(
    event: SettlementPaid, session: AsyncSession,
) -> None:
    """通知类（FB5）：UPDATE WHERE 0 行不抛错。"""
    ...

def register() -> None:
    subscribe("SettlementPaid", on_settlement_paid)
```

### 4.7 自定义 Prometheus 指标（5 个）

```python
# core/metrics.py（追加，详见 NFR Requirements §10.1）
settlement_state_transitions_total: Counter
settlement_created_via_event_total: Counter        # 含 created/duplicate_skipped/error
settlement_sequence_lock_duration_seconds: Histogram
attachment_validation_failures_total: Counter      # 含 6 类 failure_type
settlement_paid_sync_no_match_total: Counter
```

---

## 5. 错误处理 / 异常映射

### 5.1 U05 新增异常（18 个）

| 异常类 | code | HTTP | 抛出场景 |
|---|---|---|---|
| `SettlementNotFoundError` | `SETTLEMENT_NOT_FOUND` | 404 | 不存在 |
| `SettlementNoConflictError` | `SETTLEMENT_NO_CONFLICT` | 409 | 序列号冲突（理论不应触发） |
| `SequenceOverflowError` | `SETTLEMENT_SEQUENCE_OVERFLOW` | 500 | 当天序号 > 9999 |
| `IllegalStateTransitionError` | `ILLEGAL_STATE_TRANSITION` | 422 | 状态机非法转移 |
| `StateTransitionConflictError` | `SETTLEMENT_STATE_CONFLICT` | 409 | 乐观并发冲突（FB7） |
| `SelfReviewForbiddenError` | `SELF_REVIEW_FORBIDDEN` | 403 | 自审禁止 |
| `ReviewReasonRequiredError` | `REVIEW_REASON_REQUIRED` | 422 | reject 缺 reason |
| `PaymentAmountRequiredError` | `PAYMENT_AMOUNT_REQUIRED` | 422 | fill_payment 缺金额 |
| `PaymentFieldMissingError` | `PAYMENT_FIELD_MISSING` | 422 | mark_paid 缺 payment_date 或 attachment_id |
| `InvalidAttachmentReferenceError` | `INVALID_ATTACHMENT_REFERENCE` | 422 | attachment 不存在 / 跨租户 |
| `InvalidAttachmentBucketError` | `INVALID_ATTACHMENT_BUCKET` | 422 | attachment.bucket != private |
| `InvalidAttachmentPurposeError` | `INVALID_ATTACHMENT_PURPOSE` | 422 | attachment.purpose != settlement_proof |
| `InvalidAttachmentMimeError` | `INVALID_ATTACHMENT_MIME` | 422 | mime 不在白名单 |
| `AttachmentTooLargeError` | `ATTACHMENT_TOO_LARGE` | 422 | size > 10MB |
| `AttachmentNotReadyError` | `ATTACHMENT_NOT_READY` | 422 | attachment.status != ready |
| `ExtraItemNotAllowedError` | `EXTRA_ITEM_NOT_ALLOWED` | 422 | settlement_status != 待付款 |
| `FieldPermissionDenied` | `FIELD_PERMISSION_DENIED` | 403 | 字段写权限拒绝 |
| `MethodNotAllowedError` | `METHOD_NOT_ALLOWED` | 405 | DELETE /api/settlements/{id}（FB3） |

### 5.2 复用 U02/U03/U04 异常
- `FieldPermissionDenied` —— from `modules.product.exceptions`
- `IllegalStateTransitionError` / `StateTransitionConflictError` —— from `core.exceptions`
- `MissingRequiredHandlerError` —— from `core.exceptions`（U04 引入）
- `ResourceConflictError` / `ValidationError` / `PermissionDeniedError` —— from `core.exceptions`

---

## 6. 测试组件

### 6.1 测试 fixtures（追加到 conftest.py）
- `settlement_factory` — 测试数据工厂（与 U04 promotion_factory 同模式）
- `attachment_factory` — 测试 attachment 工厂（mock 6 项校验）
- `cross_unit_event_bus` — 双向 listener 注册 fixture（U04 + U05 listener 全部）
- 复用 U04 已建立的 `_clear_event_handlers`（autouse） + `event_capture` fixture

### 6.2 测试目录结构
```
backend/tests/
├── unit/
│   ├── test_settlement_state_machine.py        # 6 transition 矩阵 + assert_can_transition
│   ├── test_settlement_domain.py               # audit 脱敏 + dict diff + format_settlement_no
│   ├── test_attachment_validator.py            # 6 项校验各 1 + 跨租户 4 层防御
│   ├── test_settlement_field_perms.py          # 3 类 ROLES 矩阵
│   └── test_settlement_paid_event.py           # required_handler=False 验证
├── integration/
│   ├── test_settlement_create_via_event.py     # FB1+FB3+FB6 三重幂等 + flush
│   ├── test_settlement_review.py               # approve/reject + 自审禁止 + UPDATE WHERE
│   ├── test_settlement_extra_item.py           # 增加 + total 重算 + 状态约束
│   ├── test_settlement_fill_payment.py         # 状态推进
│   ├── test_settlement_mark_paid.py            # attachment 6 项 + SettlementPaid 反向
│   ├── test_settlement_state_concurrent.py     # FB7：100 并发 mark_paid
│   ├── test_settlement_cross_tenant.py         # FB7：跨租户 mark_paid 0 行
│   ├── test_settlement_attachment_cross_tenant.py  # FB4：跨租户 attachment 4 层防御
│   ├── test_daily_summary_as_of.py             # FB7 口径 B
│   ├── test_daily_summary_activity.py          # FB7 口径 A 含 audit JOIN
│   ├── test_settlement_paid_listener.py        # U04 端 listener 反向同步
│   ├── test_settlement_paid_no_listener_no_op.py  # FB5 通知类容忍
│   ├── test_settlement_promotion_soft_delete_no_cascade.py  # FB3 零级联
│   ├── test_settlement_method_not_allowed.py   # FB3 DELETE 405
│   └── test_e2e_review_to_paid.py              # 端到端 J4 完整旅程
├── api/
│   └── test_settlement_api.py                  # 鉴权 + OpenAPI
└── performance/
    ├── test_settlement_list_perf.py            # 10K settlement
    └── test_daily_summary_perf.py              # FB7 双口径性能
```

---

## 7. 注册与启动序列

### 7.1 启动序列（U05 实施后）

```
[lifespan startup]
  ├─ configure_logging
  ├─ Sentry.init
  ├─ check_db_health / check_redis_health
  ├─ ensure_initial_admin
  ├─ register_event_listeners()
  │     ├─ clear_handlers()
  │     ├─ from app.modules.finance.listeners import register as register_finance
  │     │     ├─ ModuleNotFoundError → warning + Sentry breadcrumb + 早退（U05 未部署）
  │     │     └─ register_finance()  # subscribe("SettlementRequested", on_settlement_requested)
  │     │           └─ 失败 → fail fast (raise RuntimeError)
  │     └─ from app.modules.promotion.listeners import register as register_promotion_listeners
  │           ├─ ModuleNotFoundError → warning + 早退（不阻塞，FB5 通知类可丢）
  │           └─ register_promotion_listeners()  # subscribe("SettlementPaid", on_settlement_paid)
  │                 └─ 失败 → fail fast
  └─ yield 进入服务循环
```

### 7.2 部署一致性约束

| 层 | 防护 | U05 实施时新增 |
|---|---|---|
| Migration | 007/008 chain | ✅ |
| CI | grep `from app.modules.finance.listeners import register` | ✅ U04 batch 4 已实施 |
| CI（V1） | grep `from app.modules.promotion.listeners import register` | 🟡 U05 实施时追加（可选，因通知类可丢） |
| Smoke | e2e-smoke-after-deploy 跑 review approve → mark_paid 全流程 | ✅ U04 batch 4 已搭框架，U05 实施时启用 |
| Startup | finance 失败 fail fast；promotion 失败也 fail fast（已注册情况下） | ✅ |
| 文档 | U05 deployment-architecture 明确 U04+U05 同批部署 | 🟡 Infrastructure Design 实施 |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 4 层架构与 U01-U04 一致 + State Machine 子层 + Listeners 双向子层 | ✅ |
| 全部新增组件复用 U01-U04 横切组件 | ✅ |
| `legacy_field_permissions` 隔离在模块内不污染 core | ✅ |
| `FieldPermissionDenied` 复用 U02 不重复定义 | ✅ |
| 状态机继承 U01 基类 + U04 模式 | ✅ |
| 事件总线复用 U04 + 反向 listener 注册框架 | ✅ |
| 自定义 5 个 Prometheus 指标加入 core/metrics | ✅ |
| 错误码体系与 U01-U04 完全兼容（18 新增异常） | ✅ |
| 财务记录永久不可替换（FB3）：UNIQUE 永久 + DELETE 405 + 零级联 | ✅ |
| Attachment 6 项强校验（FB4）独立封装 + 4 层跨租户防御 | ✅ |
| 双口径汇总（FB7）独立 endpoint + repository 内嵌 | ✅ |
| SettlementPaid 反向通知（FB5）+ 双向注册 + 失败处理不对称 | ✅ |
| 测试结构含 17 集成测试 + freezegun + 跨单元集成 fixture | ✅ |
| register_event_listeners 启动钩子 fail fast 真实错误 + 通知类容忍 | ✅ |
