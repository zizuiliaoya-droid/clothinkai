## U04 逻辑组件（Logical Components）

> 单元：U04 — 推广合作核心  
> 范围：U04 新增组件 + 复用 U01-U03 组件清单

---

## 1. 组件总览

### 1.1 U04 新增组件清单

| # | 组件 | 类型 | 文件位置 | 复用来源 |
|---|---|---|---|---|
| 1 | `Promotion` ORM | Domain | `modules/promotion/models.py` | TenantScopedModel (U01) |
| 2 | `PromotionSequence` ORM | Domain | `modules/promotion/models.py` | TenantScopedModel |
| 3 | `PublishStatus` / `RecallStatus` / `SettlementStatus` / `ReviewAction` Enum | Domain | `modules/promotion/enums.py` | — |
| 4 | `Platform` Enum | Domain | re-export from `modules/blogger/enums` | U03 |
| 5 | `PromotionCreate/Update/Response/Page` Pydantic | Schema | `modules/promotion/schemas.py` | — |
| 6 | `PromotionPublish/Cancel/Recall/Review` Pydantic | Schema | 同上 | — |
| 7 | `PromotionListFilters` dataclass | Schema | `modules/promotion/repository.py` | — |
| 8 | `PromotionRepository` | Repository | `modules/promotion/repository.py` | TenantScopedModel + Session |
| 9 | `PromotionService` | Service | `modules/promotion/service.py` | @audit / @require_permission |
| 10 | **`PublishStatusMachine` / `RecallStatusMachine` / `SettlementStatusMachine`** | State Machine | `modules/promotion/state_machines.py` | **U01 core/state_machine.py 首次实战** |
| 11 | **`SettlementRequested` / `PromotionPublished` Event** | Event | `modules/promotion/events.py` | core/events.py |
| 12 | `UrgeStatusCalculator` | Domain | `modules/promotion/urge_calculator.py` | — |
| 13 | `MetricsCalculator`（cpl / is_hit / effective_like_count） | Domain | `modules/promotion/metrics_calculator.py` | — |
| 14 | `AMOUNT_VISIBLE_ROLES` + `AMOUNT_WRITABLE_ROLES` 常量 | Legacy | `modules/promotion/legacy_field_permissions.py` | 待 U09 清理 |
| 15 | `PLATFORM_LIKE_COEFFICIENT` 等系统设置常量 | Legacy | `modules/promotion/legacy_settings.py` | 待 V1 system_setting 单元清理 |
| 16 | 业务异常 9 个 | Exception | `modules/promotion/exceptions.py` | 继承 U01 base + 复用 U02 FieldPermissionDenied |
| 17 | `promotion_state_transitions_total` / `settlement_requested_events_total` / `promotion_sequence_lock_duration_seconds` / `promotion_search_results_count` Metric | Metric | `core/metrics.py`（修改） | prometheus-client |
| 18 | `promotion_router` | API | `modules/promotion/api.py` | FastAPI Router |
| 19 | `get_promotion_service` | Dependency | `modules/promotion/deps.py` | U01 Session 注入 |
| 20 | `register_event_listeners` | Bootstrap | `app/main.py`（修改 lifespan） | core/events.py |

### 1.2 复用 U01-U03 组件（不重复定义）

| 组件 | 来源 | U04 复用方式 |
|---|---|---|
| `TenantScopedModel` | U01 | Promotion / PromotionSequence 继承 |
| `AsyncSession` 依赖注入 | U01 | PromotionRepository 注入 |
| `AsyncSessionBypass` | U01 | 失败 audit 独立 session |
| `AuditService` + `@audit` | U01 | service 层方法装饰 |
| `Permission` + `@require_permission` | U01 | API 端点装饰 |
| `core/errors.register_error_handlers` | U01 | 全局异常映射 |
| `tenancy.current_tenant_id()` | U01 | service / repository 取租户 |
| **`StateMachine` 基类** | U01 | **首次实战使用**（U04 扩展 2 个 classmethod） |
| **`core/events.py` 事件总线** | core 新建（本单元发起） | 由 U04 创建，U05/U07 等监听 |
| `RateLimiter` | U01 | API 默认应用 |
| `RequestIdMiddleware` / `TenancyMiddleware` | U01 | 透明继承 |
| `prometheus-fastapi-instrumentator` | U01 | API 指标自动暴露 |
| `Sentry SDK` | U01 | 异常自动捕获 + tag |
| `FieldPermissionDenied` 异常 | **U02** | re-export 自 modules/product/exceptions（U03 同样模式） |
| `legacy_field_permissions` 模式 | **U02 P-U02-02** | 适配字段名：AMOUNT_VISIBLE_ROLES |
| `update WHERE old_state RETURNING` 模式 | **U04 新建（受 U02 upsert 启发）** | 应用到状态机转移 |

---

## 2. 组件依赖图（含跨单元 U05 监听器）

```mermaid
graph TD
    subgraph "Layer: API"
        PromotionRouter[promotion_router<br/>api.py]
    end
    
    subgraph "Layer: Service"
        PromotionService[PromotionService]
    end
    
    subgraph "Layer: State Machine"
        PSM[PublishStatusMachine]
        RSM[RecallStatusMachine]
        SSM[SettlementStatusMachine]
    end
    
    subgraph "Layer: Domain"
        Calculators[UrgeStatusCalculator<br/>MetricsCalculator]
        Events[SettlementRequested<br/>PromotionPublished]
    end
    
    subgraph "Layer: Repository"
        PromotionRepo[PromotionRepository<br/>含 next_internal_sequence + UPDATE WHERE old_state]
    end
    
    subgraph "Layer: Models / Schemas"
        Models[Promotion / PromotionSequence ORM]
        Schemas[Pydantic Schemas]
        Enums[PublishStatus/RecallStatus/SettlementStatus]
    end
    
    subgraph "Cross-cutting (U01)"
        Audit[AuditService + @audit]
        Perms[@require_permission]
        DB[AsyncSession + RLS]
        Tenancy[tenancy.current_tenant_id]
        Errors[core/exceptions]
        Metrics[core/metrics]
        StateMachineBase[U01 core/state_machine.py 基类<br/>首次实战]
    end
    
    subgraph "Reused from U02/U03"
        FieldPermDenied[FieldPermissionDenied 异常]
    end
    
    subgraph "Transition (U04 → U09 清理)"
        LegacyPerms[legacy_field_permissions<br/>AMOUNT_VISIBLE_ROLES]
        LegacySettings[legacy_settings<br/>PLATFORM_LIKE_COEFFICIENT]
    end
    
    subgraph "Event Bus (新建)"
        EventBus[core/events.py<br/>subscribe / dispatch / clear_handlers]
    end
    
    subgraph "Cross-unit Listeners"
        U05Listener[U05 SettlementService.handle_settlement_requested]
        U07Listener[U07 WeComService.notify_promotion_published 占位]
    end
    
    PromotionRouter --> PromotionService
    PromotionService --> PSM
    PromotionService --> RSM
    PromotionService --> SSM
    PromotionService --> PromotionRepo
    PromotionService --> Calculators
    PromotionService --> Events
    PromotionService --> Audit
    PromotionService --> LegacyPerms
    PromotionService --> LegacySettings
    PromotionService --> FieldPermDenied
    PromotionService --> EventBus
    
    PSM --> StateMachineBase
    RSM --> StateMachineBase
    SSM --> StateMachineBase
    
    PromotionRepo --> DB
    PromotionRepo --> Tenancy
    Models --> DB
    
    PromotionRouter --> Perms
    PromotionRouter --> Errors
    PromotionRouter --> Metrics
    
    EventBus -.SettlementRequested.-> U05Listener
    EventBus -.PromotionPublished.-> U07Listener
```

---

## 3. 4 层架构 + State Machine 子层

### 3.1 API Layer (`api.py`)
- 11 个端点（CRUD 5 + publish/cancel + 3 召回 + review）
- `@require_permission("promotion:read|write|delete|review")`
- 输入 Pydantic 校验 / 输出 Response Schema
- 错误处理：抛业务异常 → 全局 error handler

### 3.2 Service Layer (`service.py`)
- 协调 Domain + Repository + Audit + State Machine + Events
- `_check_amount_write_permission`（字段写权限）
- `_to_response`（按角色过滤敏感字段 + 注入计算字段）
- `_log_event_dispatch_failure`（脱敏 audit + 兜底）

### 3.3 State Machine Sub-layer（独立子层）
- 3 个状态机类（PublishStatusMachine / RecallStatusMachine / SettlementStatusMachine）
- 继承 `core/state_machine.py:StateMachine`
- 仅含 transitions: list 类属性 + assert_can_transition / get_allowed_transitions classmethod
- 不依赖 ORM / Session（保持纯 Python）

### 3.4 Domain Layer
- `urge_calculator.py`：calculate_urge_status + get_today（统一日期入口）+ SQL 表达式生成
- `metrics_calculator.py`：calculate_effective_like_count / calculate_is_hit / calculate_cpl
- `events.py`：3 个 dataclass + MissingRequiredHandlerError
- `domain.py`：业务规则验证 + dict diff + audit_safe_changes 脱敏（与 U02/U03 同模式）

### 3.5 Repository Layer (`repository.py`)
- `next_internal_sequence`（INSERT ON CONFLICT 原子操作）
- `list`（CTE 注入 urge_status / dual_platform）
- `update_state_machine`（UPDATE WHERE old_state + tenant_id + is_active RETURNING）
- 自动应用 RLS

---

## 4. 关键组件细节

### 4.1 PromotionService（核心 Service）

```python
class PromotionService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = PromotionRepository(session)
        self._style_repo = StyleRepository(session)
        self._sku_repo = SkuRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._tenant_repo = TenantRepository(session)  # 取 tenant.code
        self._roles = RoleRepository(session)
        self._audit = AuditService(session)
    
    # CRUD
    async def create_promotion(self, payload, user) -> PromotionCreateResponse: ...
    async def update_promotion(self, id, payload, user) -> PromotionResponse: ...
    async def get_promotion(self, id, user) -> PromotionResponse: ...
    async def list_promotions(self, filters, page, page_size, user) -> PromotionPage: ...
    
    # 状态推进
    async def publish(self, id, payload, user) -> PromotionResponse: ...
    async def cancel(self, id, payload, user) -> PromotionResponse: ...
    async def start_recall(self, id, payload, user) -> PromotionResponse: ...
    async def recall_success(self, id, user) -> PromotionResponse: ...
    async def recall_failure(self, id, user) -> PromotionResponse: ...
    async def review(self, id, payload, user) -> PromotionResponse: ...
    
    # 内部 API（U13 用）
    async def update_like_count(self, id, like_count, source, user_id=None) -> Promotion:
        """U13 数据采集 Worker 内部调用，不暴露 HTTP."""
        ...
    
    # 私有
    async def _check_amount_write_permission(self, payload, user) -> None: ...
    async def _to_response(self, p, user, urge_status=None, dual_platform=None) -> PromotionResponse: ...
    async def _log_event_dispatch_failure(self, event, exc, user) -> None: ...
```

### 4.2 PromotionRepository

```python
class PromotionRepository:
    async def get_by_id(self, id) -> Promotion | None: ...
    async def get_by_internal_code(self, code) -> Promotion | None: ...
    
    async def next_internal_sequence(
        self, *, tenant_id: UUID, date_key: date
    ) -> int:
        """原子获取下一序列号（INSERT ON CONFLICT DO UPDATE RETURNING）."""
        ...
    
    async def list(
        self,
        *,
        filters: PromotionListFilters,
        page: int,
        page_size: int,
        today: date,  # 由 service 层 get_today() 注入
    ) -> tuple[Sequence[dict], int]:
        """CTE 注入 urge_status / dual_platform 计算列。"""
        ...
    
    async def update_state(
        self,
        *,
        promotion_id: UUID,
        tenant_id: UUID,
        from_state_field: str,  # "publish_status" / "recall_status" / "settlement_status"
        from_state_value: str,
        to_state_value: str,
        extra_fields: dict | None = None,
    ) -> Promotion | None:
        """乐观并发 UPDATE WHERE old_state RETURNING.
        
        Returns None if no row matched (并发冲突 / 软删 / 跨租户)。
        """
        ...
    
    async def find_active_duplicate(
        self, *, tenant_id: UUID, style_id: UUID, blogger_id: UUID
    ) -> Promotion | None:
        """重复检测：同 style_id + blogger_id 的活跃 promotion."""
        ...
    
    async def find_other_platforms_for_style(
        self, *, tenant_id: UUID, style_id: UUID, exclude_promotion_id: UUID
    ) -> bool:
        """dual_platform 计算：同 style_id 是否有其他平台的活跃 promotion."""
        ...
    
    def add(self, promotion: Promotion) -> None: ...
```

### 4.3 状态机定义

```python
# modules/promotion/state_machines.py
from app.core.state_machine import StateMachine, Transition
from app.modules.promotion.enums import PublishStatus, RecallStatus, SettlementStatus


class PublishStatusMachine(StateMachine[PublishStatus]):
    transitions: ClassVar[list[Transition[PublishStatus]]] = [
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.PUBLISHED, "publish"),
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.CANCELLED, "cancel"),
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
        Transition(PublishStatus.PUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
        Transition(PublishStatus.ABNORMAL, PublishStatus.UNPUBLISHED, "restore"),
    ]


class RecallStatusMachine(StateMachine[RecallStatus]):
    transitions: ClassVar[list[Transition[RecallStatus]]] = [
        Transition(RecallStatus.NOT_RECALLED, RecallStatus.RECALLING, "start_recall"),
        Transition(RecallStatus.RECALLING, RecallStatus.RECALLED_SUCCESS, "recall_success"),
        Transition(RecallStatus.RECALLING, RecallStatus.RECALLED_FAILURE, "recall_failure"),
        Transition(RecallStatus.RECALLED_FAILURE, RecallStatus.RECALLING, "start_recall"),
    ]


class SettlementStatusMachine(StateMachine[SettlementStatus]):
    transitions: ClassVar[list[Transition[SettlementStatus]]] = [
        Transition(SettlementStatus.NOT_REVIEWED, SettlementStatus.PENDING_REVIEW, "auto_advance"),
        Transition(SettlementStatus.PENDING_REVIEW, SettlementStatus.PENDING_PAYMENT, "approve"),
        Transition(SettlementStatus.PENDING_REVIEW, SettlementStatus.REJECTED, "reject"),
        Transition(SettlementStatus.REJECTED, SettlementStatus.PENDING_REVIEW, "request_review"),
        Transition(SettlementStatus.PENDING_PAYMENT, SettlementStatus.PAID, "mark_paid"),
    ]
```

### 4.4 events.py（事件分类）

```python
# modules/promotion/events.py
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID, uuid4


@dataclass(frozen=True)
class SettlementRequested:
    """强一致业务事件。U05 必须监听并创建 settlement。"""
    event_type: ClassVar[str] = "SettlementRequested"
    required_handler: ClassVar[bool] = True
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal
    requested_by: UUID
    requested_at: datetime


@dataclass(frozen=True)
class PromotionPublished:
    """通知类事件。U07 监听发企微通知（U04 阶段无 listener）."""
    event_type: ClassVar[str] = "PromotionPublished"
    required_handler: ClassVar[bool] = False
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    publish_url: str
    publish_date: object  # date
    pr_id: UUID
```

### 4.5 UrgeStatusCalculator

```python
# modules/promotion/urge_calculator.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TENANT_TZ = ZoneInfo("Asia/Shanghai")


def get_today() -> date:
    """统一日期获取入口。SQL 和 Python 必须使用同一个 today 值。"""
    return datetime.now(DEFAULT_TENANT_TZ).date()


def calculate_urge_status(
    publish_status: str,
    scheduled_publish_date: date | None,
    today: date,
    urge_threshold_days: int,
    important_threshold_days: int,
) -> str:
    if publish_status == "已取消":
        return "已取消"
    if publish_status == "已发布":
        return "已发布"
    if publish_status not in {"未发布", "异常"}:
        return "已删除"
    if scheduled_publish_date is None:
        return "未排期"
    diff = (scheduled_publish_date - today).days
    if diff > urge_threshold_days:
        return "档期内"
    if diff > important_threshold_days:
        return "催发"
    if diff >= 0:
        return "重要催发"
    return "超时"


URGE_STATUS_SQL_EXPR = """
CASE
  WHEN publish_status = '已取消' THEN '已取消'
  WHEN publish_status = '已发布' THEN '已发布'
  WHEN scheduled_publish_date IS NULL THEN '未排期'
  WHEN (scheduled_publish_date - :today) > :urge_days THEN '档期内'
  WHEN (scheduled_publish_date - :today) > :important_days THEN '催发'
  WHEN (scheduled_publish_date - :today) >= 0 THEN '重要催发'
  ELSE '超时'
END
"""
```

### 4.6 自定义 Prometheus 指标（4 个）

```python
# core/metrics.py（追加）

promotion_state_transitions_total: Counter = Counter(
    "promotion_state_transitions_total",
    "Total promotion state machine transitions",
    labelnames=("from_state", "to_state", "status_field"),
)

settlement_requested_events_total: Counter = Counter(
    "settlement_requested_events_total",
    "Total SettlementRequested events dispatched",
    labelnames=("result",),  # dispatched / handler_failed / no_handler / missing_handler
)

promotion_sequence_lock_duration_seconds: Histogram = Histogram(
    "promotion_sequence_lock_duration_seconds",
    "Duration of promotion_sequence INSERT ON CONFLICT operation",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

promotion_search_results_count: Histogram = Histogram(
    "promotion_search_results_count",
    "Distribution of promotion list result counts",
    buckets=(0, 1, 10, 100, 1000),
)
```

### 4.7 register_event_listeners（启动钩子，FB3 修正）

```python
# app/main.py
import logging
import sentry_sdk
from app.core.events import clear_handlers

log = logging.getLogger(__name__)


def register_event_listeners() -> None:
    """注册所有跨单元事件监听器。
    
    策略：
    - clear_handlers() 启动前清空，防热重载累计
    - ModuleNotFoundError → warning + Sentry breadcrumb（U05 未部署场景）
    - 其他 ImportError / Exception → fail fast，refuse to start
    """
    clear_handlers()
    
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        log.warning(
            "u05_finance_module_not_found_skipping_listener_registration. "
            "SettlementRequested events will fail with MissingRequiredHandlerError."
        )
        sentry_sdk.add_breadcrumb(
            message="U05 finance module not found",
            level="warning",
        )
        return
    
    try:
        register_finance()
    except Exception as exc:
        log.exception("listener_registration_failed", extra={"module": "finance"})
        raise RuntimeError(
            "U05 finance listener registration failed, refusing to start"
        ) from exc
```

---

## 5. 错误处理 / 异常映射

### 5.1 U04 新增异常

| 异常类 | code | HTTP | 抛出场景 |
|---|---|---|---|
| `PromotionNotFoundError` | `PROMOTION_NOT_FOUND` | 404 | 不存在 |
| `InternalCodeConflictError` | `INTERNAL_CODE_CONFLICT` | 409 | 序列号冲突（理论不应触发） |
| `SequenceOverflowError` | `SEQUENCE_OVERFLOW` | 409 | 当天序号 > 9999 |
| `InvalidStyleReferenceError` | `INVALID_STYLE_REFERENCE` | 422 | 不存在 |
| `InvalidBloggerReferenceError` | `INVALID_BLOGGER_REFERENCE` | 422 | 不存在 |
| `InvalidSkuReferenceError` | `INVALID_SKU_REFERENCE` | 422 | 不存在或跨 style |
| `InvalidPublishUrlError` | `INVALID_PUBLISH_URL` | 422 | URL 格式错误 |
| `CancelNotAllowedForPublishedError` | `CANCEL_NOT_ALLOWED_FOR_PUBLISHED` | 422 | 已发布的不能取消 |
| `IllegalStateTransitionError` | `ILLEGAL_STATE_TRANSITION` | 422 | 状态机非法转移 |
| `StateTransitionConflictError` | `STATE_TRANSITION_CONFLICT` | 409 | 乐观并发冲突 |
| `SelfReviewNotAllowedError` | `SELF_REVIEW_NOT_ALLOWED` | 422 | 自审禁止 |
| `RecallNotAllowedError` | `RECALL_NOT_ALLOWED` | 422 | publish_status 不在允许集 |
| `MissingRequiredHandlerError` | `MISSING_REQUIRED_HANDLER` | 500 | 强一致事件无监听器 |

### 5.2 复用 U02/U03 异常
- `FieldPermissionDenied` —— from `modules.product.exceptions`
- `ResourceConflictError` / `ValidationError` / `PermissionDeniedError` —— from `core.exceptions`

---

## 6. 测试组件

### 6.1 测试 fixtures（追加到 conftest.py）
- `promotion_factory` — 测试数据工厂
- `subscribe_test_handler` — 订阅测试用 handler 并返回收到的 events list

### 6.2 测试目录结构
```
backend/tests/
├── unit/
│   ├── test_state_machines.py            # 3 状态机 transition 矩阵 + assert_can_transition
│   ├── test_promotion_domain.py          # audit_safe_changes 脱敏 + dict diff
│   ├── test_urge_calculator.py           # Python 实现 + freezegun 边界日
│   ├── test_metrics_calculator.py        # cpl 零分母 / is_hit 阈值 / effective_like_count
│   ├── test_event_bus.py                 # subscribe 幂等 + clear / required vs optional
│   └── test_promotion_field_perms.py     # AMOUNT_VISIBLE_ROLES 矩阵
├── integration/
│   ├── test_promotion_crud.py            # EP05-S02 + 重复检测 + 序列号
│   ├── test_promotion_publish.py         # EP05-S07 + 同事务自动推进 settlement_status
│   ├── test_promotion_cancel.py          # EP05-S08 + 已发布拒绝
│   ├── test_promotion_recall.py          # EP05-S09 + 跨状态机
│   ├── test_promotion_review.py          # EP05-S13 + SettlementRequested 事件 + 自审禁止
│   ├── test_promotion_search.py          # 列表 + CTE
│   ├── test_promotion_state_concurrent.py  # 100 并发 publish 只一个成功
│   ├── test_promotion_sequence_concurrent.py  # 100 并发首次创建无重复
│   ├── test_urge_calculator_python_vs_sql.py  # 100 mock 数据双实现一致 + freezegun
│   └── test_event_bus_failure_rollback.py  # handler 异常 + audit 脱敏 + 兜底
├── api/
│   └── test_promotion_api.py             # 鉴权 + OpenAPI
└── performance/
    ├── test_promotion_list_perf.py       # 10K promotion + CTE P95 ≤ 300ms
    └── test_promotion_sequence_perf.py   # 序列号生成性能
```

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 4 层架构与 U01-U03 一致 + State Machine 独立子层 | ✅ |
| 全部新增组件复用 U01-U03 横切组件 | ✅ |
| `legacy_field_permissions` 隔离在模块内不污染 core | ✅ |
| `legacy_settings.py` 同模式（V1 system_setting 单元清理） | ✅ |
| `FieldPermissionDenied` 复用 U02 不重复定义 | ✅ |
| 状态机 3 个独立类继承 U01 基类 | ✅ |
| 事件总线 core/events.py 由 U04 创建（首次） | ✅ |
| 自定义 4 个 Prometheus 指标加入 core/metrics | ✅ |
| 错误码体系与 U01-U03 完全兼容（13 新增异常） | ✅ |
| 测试结构含状态机/事件/序列号并发 + freezegun 一致性测试 | ✅ |
| register_event_listeners 启动钩子 fail fast 真实错误 | ✅ |
