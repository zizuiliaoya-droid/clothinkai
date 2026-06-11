## U04 NFR 设计模式（NFR Design Patterns）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性 NFR 模式 + 复用 U02/U03 已建立模式  
> 已应用 8 条用户反馈修正（FB1-FB8）

---

## 1. 与 U01-U03 模式的关系

### 1.1 完全继承
- U01 9 个通用模式（多租户 / 审计 / 状态机基类 / 附件 / 速率限制 / 错误处理 / 监控 / 备份 / 健康检查）
- U02 4 个增量模式（GIN trgm 模糊搜索 / 字段权限硬编码 / 数据库原子 upsert / 软删引用检查）
- U03 2 个增量模式（GIN JSONB / 防侧信道）

### 1.2 复用方式
| U02/U03 模式 | U04 应用 |
|---|---|
| 字段权限硬编码（U02 P-U02-02）| AMOUNT_VISIBLE_ROLES + AMOUNT_WRITABLE_ROLES |
| 审计敏感值脱敏（U02 BR-U02-31）| quote_amount / cost_snapshot 仅记 *_changed: true |
| match 降级语义（U02 P-U02-01）| service.list_promotions 不 try/except DB 异常 |

### 1.3 U04 增量模式（4 个）

| 模式 | 解决问题 | 章节 |
|---|---|---|
| **P-U04-01** 状态机乐观并发 | 3 个并行状态机的转移在并发下保持原子 + 无悲观锁 | §2 |
| **P-U04-02** 本地同事务事件总线 + 类型分类 | 跨单元事件（U05/U07）+ 强一致 vs 通知类区分 | §3 |
| **P-U04-03** 序列号原子操作 | internal_code 防 race（含首次创建场景）| §4 |
| **P-U04-04** CTE 衍生字段双实现 + 日期一致性 | 5 个衍生字段不持久化 + Python/SQL 一致性 | §5 |

---

## 2. Pattern P-U04-01 — 状态机乐观并发

### 2.1 问题
- 3 个并行状态机（publish_status / recall_status / settlement_status）的转移在并发下需要原子保护
- 不引入悲观锁（避免热点 promotion 转移路径串行化）
- 防止跨租户操作 / 软删行被推进 / 跨状态机旧值被破坏

### 2.2 设计

#### 2.2.1 状态机基类扩展
```python
# core/state_machine.py（U01 已建，本单元扩展 2 个 classmethod）
class StateMachine(Generic[StateT]):
    transitions: ClassVar[list[Transition]] = []
    
    @classmethod
    def assert_can_transition(
        cls,
        from_state: StateT,
        to_state: StateT,
        event: str,
    ) -> None:
        """业务前置校验：抛 IllegalStateTransitionError 含 from/to/event."""
        for t in cls.transitions:
            if t.from_state == from_state and t.to_state == to_state and t.event == event:
                return
        raise IllegalStateTransitionError(
            f"{cls.__name__}: 不允许从 {from_state} 通过 {event} 转移到 {to_state}",
            details={"from_state": from_state, "to_state": to_state, "event": event},
        )
    
    @classmethod
    def get_allowed_transitions(cls, from_state: StateT) -> list[tuple[str, StateT]]:
        """返回从某状态可达的所有 (event, to_state)，供前端展示按钮."""
        return [(t.event, t.to_state) for t in cls.transitions if t.from_state == from_state]
```

#### 2.2.2 service 层乐观并发 UPDATE（FB7 修正）
```python
async def publish(self, promotion_id, payload, user) -> Promotion:
    tenant_id = current_tenant_id()
    
    # 1. 业务前置校验（友好错误）
    promotion = await self._repo.get_by_id(promotion_id)
    if not promotion:
        raise PromotionNotFoundError()
    PublishStatusMachine.assert_can_transition(
        from_state=PublishStatus(promotion.publish_status),
        to_state=PublishStatus.PUBLISHED,
        event="publish",
    )
    
    # 2. 乐观并发 UPDATE（FB7 强化条件：tenant_id + is_active + 旧状态）
    stmt = (
        update(Promotion)
        .where(
            Promotion.id == promotion_id,
            Promotion.tenant_id == tenant_id,                            # 多租户防护
            Promotion.publish_status == PublishStatus.UNPUBLISHED.value, # 旧状态
            Promotion.is_active.is_(True),                               # 软删除防护
        )
        .values(
            publish_status=PublishStatus.PUBLISHED.value,
            publish_url=payload.publish_url,
            actual_publish_date=payload.actual_publish_date,
            updated_at=func.now(),
        )
        .returning(Promotion)
    )
    result = await self._session.execute(stmt)
    updated = result.scalar_one_or_none()
    if updated is None:
        # 并发竞争：另一会话已推进或行已被软删
        raise StateTransitionConflictError(
            "promotion 状态已变更或已删除，请刷新后重试",
            details={"promotion_id": str(promotion_id)},
        )
    
    # 3. 同事务自动推进 settlement_status（FB7 加旧值校验）
    stmt2 = (
        update(Promotion)
        .where(
            Promotion.id == promotion_id,
            Promotion.tenant_id == tenant_id,
            Promotion.settlement_status == SettlementStatus.NOT_REVIEWED.value,
        )
        .values(settlement_status=SettlementStatus.PENDING_REVIEW.value)
    )
    await self._session.execute(stmt2)
    
    # 4. audit + 事件分发 + commit
    ...
```

### 2.3 监控
```python
promotion_state_transitions_total.labels(
    from_state="未发布", to_state="已发布", status_field="publish"
).inc()
```

### 2.4 测试覆盖
- `test_publish_concurrent_only_one_succeeds`：100 并发 publish 同 promotion_id，1 成功 / 99 抛 StateTransitionConflictError
- `test_publish_other_tenant_returns_no_match`：跨租户 publish → 0 行（RLS + tenant_id WHERE 双保险）
- `test_publish_softdeleted_blocked`：is_active=false 的 promotion 不能被 publish

---

## 3. Pattern P-U04-02 — 本地同事务事件总线 + 类型分类

### 3.1 问题
- 跨单元事件（U05 监听 SettlementRequested / U07 监听 PromotionPublished）
- 强一致事件（settlement 创建必须同事务）vs 通知类事件（企微通知可丢可重试）需要不同处理
- 启动时监听器缺失：U04 部署但 U05 未部署不应允许 approve 流程产生不一致状态

### 3.2 设计

#### 3.2.1 事件分类（FB4 修正）

```python
# core/events.py
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

EventHandler = Callable[[Any, Any], Awaitable[None]]  # (event, session)


@dataclass(frozen=True)
class SettlementRequested:
    """强一致业务事件：必须有 handler，否则 dispatch 抛错。"""
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
    """通知类事件：可丢可重试，无 handler 不报错。"""
    event_type: ClassVar[str] = "PromotionPublished"
    required_handler: ClassVar[bool] = False
    
    event_id: UUID
    # ... 字段同上


class MissingRequiredHandlerError(AppException):
    code = "MISSING_REQUIRED_HANDLER"
    status_code = 500
```

#### 3.2.2 总线实现（FB6 防重复注册）

```python
# core/events.py
import logging

log = logging.getLogger(__name__)

_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event_type: str, handler: EventHandler) -> None:
    """注册事件监听器。幂等：同一 (event_type, handler) 重复不重复执行。"""
    handlers = _handlers.setdefault(event_type, [])
    if handler in handlers:
        log.warning(
            "event_handler_already_registered",
            extra={"event_type": event_type,
                   "handler": getattr(handler, "__qualname__", str(handler))},
        )
        return
    handlers.append(handler)


def clear_handlers() -> None:
    """清空所有 handlers。仅供测试和应用启动时调用。"""
    _handlers.clear()


async def dispatch(event: Any, *, session: Any) -> None:
    """同事务同步触发，监听器异常自然冒泡导致事务回滚。"""
    handlers = _handlers.get(event.event_type, [])
    if not handlers:
        if getattr(event, "required_handler", False):
            settlement_requested_events_total.labels(result="missing_handler").inc()
            raise MissingRequiredHandlerError(
                f"Event {event.event_type} requires a handler but none registered. "
                "Check that downstream module (e.g. U05 finance) is deployed."
            )
        # 通知类无 handler：no-op + 指标
        settlement_requested_events_total.labels(result="no_handler").inc()
        log.warning("event_no_handler", extra={"event_type": event.event_type})
        return
    
    for handler in handlers:
        await handler(event, session)
    settlement_requested_events_total.labels(result="dispatched").inc()
```

#### 3.2.3 service 层 dispatch + 失败处理（FB5 脱敏修正）

```python
# modules/promotion/service.py
async def review(self, promotion_id, payload, user) -> Promotion:
    # ... 状态推进
    
    if payload.action == ReviewAction.APPROVE:
        event = SettlementRequested(
            event_id=uuid4(),
            timestamp=now(),
            tenant_id=user.tenant_id,
            promotion_id=promotion.id,
            promotion_internal_code=promotion.internal_code,
            blogger_id=promotion.blogger_id,
            style_id=promotion.style_id,
            amount=promotion.quote_amount,
            requested_by=user.id,
            requested_at=promotion.reviewed_at,
        )
        try:
            await events.dispatch(event, session=self._session)
        except Exception as exc:
            sentry_sdk.capture_exception(exc)  # 完整异常信息只给 Sentry
            await self._log_event_dispatch_failure(event, exc, user)  # 脱敏 audit
            raise  # 重新抛出，让原事务回滚
    
    await self._session.commit()
    return promotion
```

#### 3.2.4 失败 audit 脱敏 + 兜底（FB5）

```python
async def _log_event_dispatch_failure(
    self, event: Any, exc: Exception, user: User
) -> None:
    """事件分发失败的独立 audit。
    
    严格脱敏 — 不写 str(exc) / SQL / 金额 / 内部路径。
    audit 自身写入失败不能覆盖原异常。
    """
    safe_payload = {
        "event_type": event.event_type,
        "event_id": str(event.event_id),
        "error_type": type(exc).__name__,           # 仅类名
        "error_code": getattr(exc, "code", None),    # 业务错误码（若有）
        "promotion_id": str(getattr(event, "promotion_id", None)),
        "request_id": request_id_ctx.get(),
    }
    
    try:
        async with AsyncSessionBypass() as audit_session:
            audit_session.add(AuditLog(
                action="promotion.review.event_dispatch_failed",
                resource="promotion",
                resource_id=event.promotion_id,
                user_id=user.id,
                after=safe_payload,
            ))
            await audit_session.commit()
    except Exception as audit_exc:
        # 兜底：audit 写失败仅 log，不覆盖原异常
        log.exception(
            "audit_for_event_failure_itself_failed",
            extra={"original_error": type(exc).__name__,
                   "audit_error": type(audit_exc).__name__},
        )
        # 不重新抛 audit_exc，让原 exc 继续上抛
```

#### 3.2.5 监听器注册（FB3 修正）

```python
# app/main.py 启动钩子
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
            "u05_finance_module_not_found_skipping_listener_registration",
            extra={
                "consequence": (
                    "SettlementRequested events will fail with "
                    "MissingRequiredHandlerError until U05 is deployed"
                ),
            },
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

### 3.3 部署一致性约束（FB10 多层防护）

| 层 | 防护 |
|---|---|
| Migration | U04+U05 同批部署（005_u04 + 006_u05 一次 alembic upgrade） |
| CI | grep `from app.modules.finance.listeners import register` 必须存在 |
| Smoke | staging 跑 `test_review_approve_creates_settlement_via_event` 端到端，失败禁 production |
| Startup | 启动时 register_finance 失败 fail fast |
| 文档 | U04/infrastructure-design/deployment-architecture.md 明确"U04 必须 ≥ U05 部署" |

### 3.4 测试覆盖
- `test_subscribe_idempotent`：同一 handler 注册 3 次，dispatch 一次 handler 只调用一次
- `test_clear_handlers_then_resubscribe`：clear 后重新 subscribe，dispatch 正常
- `test_required_event_no_handler_raises`：无 handler 时 dispatch SettlementRequested 抛 MissingRequiredHandlerError
- `test_optional_event_no_handler_noop`：无 handler 时 dispatch PromotionPublished 不抛错
- `test_event_handler_failure_rollback`：mock handler 抛异常，整个事务回滚
- `test_event_failure_audit_sanitized`：handler 抛 ValueError("敏感金额 100.00") → audit 仅记 error_type="ValueError"，不含 "敏感金额"
- `test_audit_failure_does_not_mask_original_error`：mock audit 写入也失败，原异常仍冒泡

---

## 4. Pattern P-U04-03 — 序列号原子操作

### 4.1 问题
- internal_code 序列号生成必须原子（防 race）
- **首次创建场景**（FB2）：sequence 行尚不存在时，并发两个请求同时 INSERT 第一条，旧方案 SELECT FOR UPDATE 无法锁定不存在的行

### 4.2 设计（FB2 修正）

```sql
INSERT INTO promotion_sequence (id, tenant_id, date_key, last_seq, created_at, updated_at)
VALUES (gen_random_uuid(), :tenant_id, :date_key, 1, NOW(), NOW())
ON CONFLICT (tenant_id, date_key) DO UPDATE
SET last_seq = promotion_sequence.last_seq + 1,
    updated_at = NOW()
RETURNING last_seq;
```

**正确性证明**：
- UNIQUE (tenant_id, date_key) 索引强制唯一
- 并发两个 INSERT 竞争同一索引位置 → PostgreSQL 保证只一个成功（first INSERT, last_seq=1）
- 另一个走 DO UPDATE 路径（last_seq = 1 + 1 = 2）
- 第三个走 DO UPDATE（last_seq = 2 + 1 = 3）...
- 单条 SQL 语句保证原子性（无任何 race window）

**实施代码**：
```python
class PromotionRepository:
    async def next_internal_sequence(
        self, *, tenant_id: UUID, date_key: date
    ) -> int:
        stmt = text("""
            INSERT INTO promotion_sequence
                (id, tenant_id, date_key, last_seq, created_at, updated_at)
            VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
            ON CONFLICT (tenant_id, date_key) DO UPDATE
            SET last_seq = promotion_sequence.last_seq + 1,
                updated_at = NOW()
            RETURNING last_seq
        """)
        result = await self._session.execute(
            stmt, {"tid": tenant_id, "dk": date_key}
        )
        next_seq = int(result.scalar_one())
        if next_seq > 9999:
            raise SequenceOverflowError()
        return next_seq
```

### 4.3 监控
`promotion_sequence_lock_duration_seconds` Histogram（虽然不再 SELECT FOR UPDATE，但 INSERT ON CONFLICT 仍可能在高并发下等待索引锁，监控有效）。

### 4.4 测试覆盖
- `test_internal_sequence_concurrent_first_create`：100 并发首次创建（同 date_key 之前不存在 sequence 行）→ 序列号 1..100 无重复无缺失
- `test_internal_sequence_continues_after_existing`：先创建 5 个，再并发 100 个 → 序列号 6..105
- `test_internal_sequence_different_dates_independent`：同一租户不同 date_key 序列号独立计数
- `test_internal_sequence_different_tenants_independent`：不同 tenant 同 date_key 序列号独立

---

## 5. Pattern P-U04-04 — CTE 衍生字段双实现 + 日期一致性

### 5.1 问题
- 5 个衍生字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）不持久化
- 必须 SQL（列表查询 CTE）+ Python（service 层单条响应）双实现一致
- **日期口径漂移**（FB8）：SQL CURRENT_DATE 与 Python datetime.now().date() 在跨时区或边界日不一致

### 5.2 设计

#### 5.2.1 统一日期入口（FB8）

```python
# modules/promotion/urge_calculator.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TENANT_TZ = ZoneInfo("Asia/Shanghai")  # MVP 阶段全部硬编码

def get_today() -> date:
    """统一日期获取入口。SQL 和 Python 必须使用同一个 today 值。"""
    return datetime.now(DEFAULT_TENANT_TZ).date()
```

#### 5.2.2 SQL 表达式（不用 CURRENT_DATE）

```sql
CASE
  WHEN publish_status = '已取消' THEN '已取消'
  WHEN publish_status = '已发布' THEN '已发布'
  WHEN scheduled_publish_date IS NULL THEN '未排期'
  WHEN (scheduled_publish_date - :today) > :urge_days THEN '档期内'
  WHEN (scheduled_publish_date - :today) > :important_days THEN '催发'
  WHEN (scheduled_publish_date - :today) >= 0 THEN '重要催发'
  ELSE '超时'
END AS urge_status
```

#### 5.2.3 service 层调用

```python
async def list_promotions(self, filters, page, page_size, user) -> PromotionPage:
    today = get_today()
    
    stmt = text("""
        WITH base AS (
            SELECT p.*,
                   CASE ... :today ... END AS urge_status,
                   EXISTS (...) AS dual_platform
            FROM promotion p
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
        )
        SELECT * FROM base
        WHERE 1=1 ...
        ORDER BY ... LIMIT :ps OFFSET :off
    """).bindparams(
        today=today,
        urge_days=URGE_THRESHOLD_DAYS,
        important_days=IMPORTANT_THRESHOLD_DAYS,
        ...
    )
```

### 5.3 测试覆盖（FB8 边界 / freezegun）

```python
from freezegun import freeze_time

@freeze_time("2026-05-26 23:59:00", tz_offset=8)  # Asia/Shanghai 当时 2026-05-27 07:59
@pytest.mark.parametrize("scenario", generate_100_urge_scenarios())
async def test_urge_calculator_python_vs_sql_consistency(
    scenario, session, tenant_a
):
    today = get_today()
    
    # Python 实现
    py_result = calculate_urge_status(
        publish_status=scenario.publish_status,
        scheduled_publish_date=scenario.scheduled_publish_date,
        today=today,
        urge_threshold_days=10,
        important_threshold_days=3,
    )
    
    # SQL 实现（同一 today 参数）
    sql_result = await session.execute(
        text("SELECT CASE ... :today ... END AS r"),
        {"today": today, "urge_days": 10, "important_days": 3,
         "publish_status": scenario.publish_status,
         "scheduled_publish_date": scenario.scheduled_publish_date},
    )
    
    assert py_result == sql_result.scalar_one()


@freeze_time("2026-05-26")  # 边界日
def test_urge_status_at_scheduled_date():
    """边界用例：scheduled_publish_date == today → 重要催发."""
    today = get_today()
    result = calculate_urge_status(
        publish_status="未发布",
        scheduled_publish_date=today,
        today=today,
        urge_threshold_days=10,
        important_threshold_days=3,
    )
    assert result == "重要催发"
```

---

## 6. 监控与 SLO

### 6.1 SLI（与 NFR §3.1 一致）

| SLI | SLO 目标 |
|---|---|
| 列表 + CTE P95 | ≤ 300ms |
| 详情 P95 | ≤ 100ms |
| 创建（含序列号锁）P95 | ≤ 300ms |
| 状态推进 P95 | ≤ 300ms |
| 审核（含跨单元事务）P95 | ≤ 500ms |

### 6.2 自定义 Prometheus 指标（4 个）

```python
# core/metrics.py
promotion_state_transitions_total = Counter(
    "promotion_state_transitions_total",
    "Total promotion state machine transitions",
    labelnames=("from_state", "to_state", "status_field"),
)

settlement_requested_events_total = Counter(
    "settlement_requested_events_total",
    "Total SettlementRequested events dispatched",
    labelnames=("result",),  # dispatched / handler_failed / no_handler / missing_handler
)

promotion_sequence_lock_duration_seconds = Histogram(
    "promotion_sequence_lock_duration_seconds",
    "Duration of promotion_sequence INSERT ON CONFLICT operation",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

promotion_search_results_count = Histogram(
    "promotion_search_results_count",
    "Distribution of promotion list result counts",
    buckets=(0, 1, 10, 100, 1000),
)
```

### 6.3 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/promotions.*"}) > 1` 持续 5min | Prometheus alertmanager | SRE |
| `rate(settlement_requested_events_total{result=~"handler_failed\|missing_handler"}[5m]) > 0` | Sentry → 即时 | 后端 leader（关键） |
| `histogram_quantile(0.95, promotion_sequence_lock_duration_seconds) > 0.5` | Prometheus | SRE |
| `/api/promotions.*` 5xx > 5% 持续 5min | Sentry | 后端 |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 状态机乐观并发 + WHERE 条件含 tenant_id+is_active+旧值 | ✅ |
| 序列号原子 INSERT ON CONFLICT（防首次创建 race） | ✅ |
| 事件分类（required_handler）+ 多层部署防护 | ✅ |
| 启动注册容错（ModuleNotFoundError vs 其他 ImportError） | ✅ |
| 失败 audit 脱敏 + 兜底（不写 str(exc)，audit 失败不覆盖原异常） | ✅ |
| 防重复注册（subscribe 幂等 + clear_handlers） | ✅ |
| 日期口径统一（SQL 不用 CURRENT_DATE，传 :today 参数） | ✅ |
| freezegun 边界日测试覆盖 | ✅ |
| 跨单元（U05）部署一致性约束（CI / smoke / migration / 启动）| ✅ |
