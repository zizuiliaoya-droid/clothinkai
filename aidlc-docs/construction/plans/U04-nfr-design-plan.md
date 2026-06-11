# U04 NFR 设计计划（NFR Design Plan）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性 NFR 设计模式 + 逻辑组件；通用模式继承 U01 + U02 + U03

---

## 1. 单元上下文

### 1.1 与 U01-U03 NFR Design 的关系

继承全部通用模式 + U02 4 个增量模式（字段权限 / 审计脱敏 / upsert / 软删引用）。

**U04 增量**（新增 4 个模式）：
- P-U04-01：状态机乐观并发（首次实战 U01 core/state_machine.py）
- P-U04-02：本地同事务事件总线（含失败回滚契约）
- P-U04-03：序列号 promotion_sequence + 行级锁（防 race）
- P-U04-04：CTE 衍生字段双实现（SQL + Python 一致性）

### 1.2 输入文档
- U04 functional-design 3 文档
- U04 nfr-requirements 2 文档
- U01 nfr-design（参考通用模式）
- U02/U03 nfr-design（参考已建立的字段权限 / 审计脱敏模式）

### 1.3 输出文档
- `U04/nfr-design/nfr-design-patterns.md`（4 个增量模式）
- `U04/nfr-design/logical-components.md`（U04 新增组件）

---

## 2. 计划步骤

### Step 1 — 分析 NFR 需求
- [x] 1.1 读取 NFR Requirements 2 份文档
- [x] 1.2 与 U02/U03 模式对齐复用边界

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U04 增量模式
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-design-patterns.md
- [x] 3.1 P-U04-01：状态机乐观并发（UPDATE WHERE old_state RETURNING）
- [x] 3.2 P-U04-02：本地同事务事件总线 + 失败回滚契约
- [x] 3.3 P-U04-03：序列号 promotion_sequence + 行级锁
- [x] 3.4 P-U04-04：CTE 衍生字段双实现 + 一致性测试
- [x] 3.5 复用 U02/U03 模式清单

### Step 4 — 生成 logical-components.md
- [x] 4.1 U04 新增组件清单（含 state_machines / events / urge_calculator / metrics_calculator）
- [x] 4.2 组件依赖图（Mermaid，含跨单元 U05 监听器）
- [x] 4.3 4 层架构 + state_machines 独立子层

### Step 5 — 提交完成消息

---

## 3. 澄清问题（请填 [Answer]）

> U04 复杂度高，5 个核心问题需要确认。

### 3.1 状态机基类细化

**Q1**：U01 已建 `core/state_machine.py` 但实际未在业务使用。U04 实战时是否需要扩展基类？

[Answer]: 适度扩展，新增 2 个能力：
1. **`assert_can_transition(from_state, to_state, event)`** classmethod：业务侧调用前置校验，抛 `IllegalStateTransitionError`（带 from/to/event）
2. **`get_allowed_transitions(from_state)` classmethod**：返回从某状态可达的所有 (event, to_state) 列表，供前端展示"可执行操作"按钮

不需要内置 ORM 集成（service 层手动管理 UPDATE）。基类保持纯 Python。

### 3.2 事件总线与 ORM session 的关系

**Q2**：本地事件总线如何把当前 session 传给监听器？

[Answer]: 监听器签名约定：
```python
async def handle_settlement_requested(
    event: SettlementRequested,
    session: AsyncSession,  # ← publisher 传入
) -> None:
    ...
```

dispatch 调用方传 session：
```python
await events.dispatch(event, session=self._session)
```

监听器函数自行用 session 操作 DB。无需引入 contextvars / global 状态（保持简单）。

### 3.3 audit 失败处理

**Q3**：U05 监听器内部抛异常时，audit_log 是否应该写入"事件分发失败"？

[Answer]: 是 — 但要严格脱敏 + 兜底（FB5 修正）：

**audit 字段（仅写入安全字段，不写 str(exc)）**：
```python
async def _log_event_dispatch_failure(
    event: Any,
    exc: Exception,
    user: User,
) -> None:
    """事件分发失败的独立 audit 记录。
    
    严格脱敏：仅记录错误类型、event_id、request_id；
    异常详细信息（含 SQL/金额/路径）只交给 Sentry。
    """
    safe_payload = {
        "event_type": event.event_type,
        "event_id": str(event.event_id),
        "error_type": type(exc).__name__,        # 只记类名
        "error_code": getattr(exc, "code", None), # 业务错误码（若有）
        "promotion_id": str(getattr(event, "promotion_id", None)),
        "request_id": request_id_ctx.get(),
        # 不写 str(exc)、不写 traceback、不写 SQL、不写金额
    }
    
    # 用独立 session 写 audit，绝不影响原异常
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
        # 兜底：独立 audit 写失败不能覆盖原异常，仅 log
        log.exception(
            "audit_for_event_failure_itself_failed",
            extra={"original_error": type(exc).__name__,
                   "audit_error": type(audit_exc).__name__},
        )
        # 不重新抛 audit_exc，让原 exc 继续上抛
```

**Sentry 上报**：原始异常完整信息（含 traceback、SQL、内部上下文）由 service 层 `sentry_sdk.capture_exception(exc)` 上报，不进 audit_log。

**事件总线层调用模式**：
```python
try:
    await events.dispatch(event, session=self._session)
except Exception as exc:
    sentry_sdk.capture_exception(exc)
    await _log_event_dispatch_failure(event, exc, user)
    raise  # 重新抛出原异常，让原事务回滚
```

### 3.4 衍生字段共享 vs U05 直接计算

**Q4**：U05 settlement 表会引用 promotion.quote_amount。U05 是否需要重新实现 cpl 等衍生字段？

[Answer]: 不需要 — U05 settlement 不计算衍生字段（cpl / is_hit 等是 promotion 级别概念）。U05 只关注 settlement 本身的金额、付款、状态。U04 的衍生字段仅在 promotion 列表 / 详情接口出现。

### 3.5 U05 监听器注册时机

**Q5**：U05 阶段实施后，监听器在何处注册？U04 阶段不存在 U05 模块，是否会启动报错？

[Answer]:

**事件分类（FB4 修正：必要 vs 可选事件）**：

事件 dataclass 增加 `required_handler` 类属性区分两类事件：
- **强一致事件**（required_handler=True）：如 `SettlementRequested`，无 handler → dispatch 抛 `MissingRequiredHandlerError`，业务接口返回 5xx
- **通知类事件**（required_handler=False）：如未来 `PromotionPublished`（U07 企微通知），无 handler → no-op + Prometheus 指标 `result="no_handler"` 计数 + Sentry warning

```python
@dataclass(frozen=True)
class SettlementRequested:
    event_type: ClassVar[str] = "SettlementRequested"
    required_handler: ClassVar[bool] = True   # ← 强一致事件
    # ...

@dataclass(frozen=True)
class PromotionPublished:
    event_type: ClassVar[str] = "PromotionPublished"
    required_handler: ClassVar[bool] = False  # ← 通知类，可丢
    # ...
```

dispatch 实现：
```python
async def dispatch(event: Any, *, session: AsyncSession) -> None:
    handlers = _handlers.get(event.event_type, [])
    if not handlers:
        if getattr(event, "required_handler", False):
            raise MissingRequiredHandlerError(
                f"Event {event.event_type} requires a handler but none registered. "
                "Check that U05 listeners are imported and registered."
            )
        # 通知类无 handler：no-op + 指标
        log.warning("event_no_handler", event_type=event.event_type)
        ...
        return
    for h in handlers:
        await h(event, session)
```

**FB1 修正：U04 阶段如何处理 SettlementRequested 强一致约束**：

由于 SettlementRequested 是 `required_handler=True`，**U04 阶段（U05 未实施）会无法 approve**：
- `POST /api/promotions/{id}/review` action=approve → dispatch SettlementRequested → 无 handler → `MissingRequiredHandlerError` → 5xx
- 这是**预期行为**：避免 settlement_status="待付款" 但无 settlement 记录的不一致状态

**MVP 部署顺序约束**：
- U05 必须**与 U04 同时或之前**部署到 production；不能 U04 先上线 + U05 推迟
- 部署校验：staging 环境跑端到端测试 `test_review_approve_creates_settlement_via_event`，必须通过才允许 production migrate
- 文档：在 U04 deployment-architecture.md 明确写"U04 与 U05 必须同批部署或 U05 先部署"

**FB3 修正：监听器注册容错策略**：

```python
# app/main.py 启动钩子
import logging
log = logging.getLogger(__name__)

def register_event_listeners() -> None:
    """注册所有跨单元事件监听器。
    
    关键策略：
    - 仅捕获 ModuleNotFoundError（目标模块整体不存在 - U05 未部署场景）
    - 不捕获 ImportError（模块存在但内部 import 失败 - 真实代码错误，必须暴露）
    - 注册失败 → Sentry 警告 + 启动失败（fail fast）
    """
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        # U05 未部署场景：明确 warning（绝不静默）
        log.warning(
            "u05_finance_module_not_found_skipping_listener_registration. "
            "SettlementRequested events will fail until U05 is deployed.",
        )
        return
    
    try:
        register_finance()
    except Exception as exc:
        # 模块存在但注册失败：fail fast
        log.exception("listener_registration_failed", extra={"module": "finance"})
        raise RuntimeError("U05 listener registration failed, refusing to start") from exc
```

---

## 4. 决策摘要（用户填答后由 AI 整理）


## 5. 用户反馈 P1 修正（FB1-FB8）

### 3.6 FB2 序列号首次创建 race（critical）

**Q6**：原方案 `SELECT FOR UPDATE` 在序列号行尚不存在时不能锁住"未来要插入的行"，并发首次创建有 race condition。如何修正？

[Answer]: 改用 PostgreSQL `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` 原子操作。

**修正方案**：
```sql
INSERT INTO promotion_sequence (id, tenant_id, date_key, last_seq, created_at, updated_at)
VALUES (gen_random_uuid(), :tenant_id, :date_key, 1, NOW(), NOW())
ON CONFLICT (tenant_id, date_key) DO UPDATE
SET last_seq = promotion_sequence.last_seq + 1,
    updated_at = NOW()
RETURNING last_seq;
```

**正确性**：
- UNIQUE (tenant_id, date_key) 是关键 — 并发两个 INSERT 竞争同一索引位置，PostgreSQL 保证只有一个成功（first=1），另一个走 DO UPDATE 路径（last_seq+1=2）
- 单条 SQL 原子，无任何 race window
- 不需要 SELECT FOR UPDATE / SAVEPOINT

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

**测试覆盖**：
- 集成测试 `test_internal_sequence_concurrent_first_create`：100 并发首次创建（同一 date_key 之前不存在 sequence 行）
- 期望：1..100 序列号无重复、无缺失、无异常

### 3.7 FB6 防重复注册

**Q7**：测试/热重载场景下 `subscribe()` 可能重复执行，导致同一事件被处理多次。如何防御？

[Answer]: subscribe 实现幂等 + 启动前清空 registry：

```python
# core/events.py
import logging
log = logging.getLogger(__name__)

_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event_type: str, handler: EventHandler) -> None:
    """注册事件监听器。幂等：同一 (event_type, handler) 重复注册不重复执行。"""
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


# app/main.py 启动钩子
def register_event_listeners() -> None:
    clear_handlers()  # ← 启动前清空，防热重载累计
    # ... 注册各模块监听器
```

**测试**：
- `test_subscribe_idempotent`：同一 handler 注册 3 次，dispatch 一次，handler 只调一次
- `test_clear_handlers_then_resubscribe`：clear 后重新 subscribe，dispatch 正常

### 3.8 FB7 状态机并发更新条件强化

**Q8**：UPDATE WHERE old_state RETURNING 模式应包含哪些 WHERE 条件？

[Answer]: 必须包含完整防护条件：

```python
async def publish(self, promotion_id, payload, user) -> Promotion:
    tenant_id = current_tenant_id()
    
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
        raise StateTransitionConflictError(
            "promotion 状态已变更或已删除，请刷新后重试",
            details={"promotion_id": str(promotion_id)},
        )
```

**publish 同事务自动推进 settlement_status 时**也要带 settlement_status 旧值校验：
```python
stmt2 = (
    update(Promotion)
    .where(
        Promotion.id == promotion_id,
        Promotion.tenant_id == tenant_id,
        Promotion.settlement_status == SettlementStatus.NOT_REVIEWED.value,  # 关键
    )
    .values(settlement_status=SettlementStatus.PENDING_REVIEW.value)
)
# 若 settlement_status 已被并发推进（如 U13 错误地推进），此 UPDATE 不会破坏现有状态
```

**测试覆盖**：
- `test_publish_concurrent_only_one_succeeds`：100 并发 publish 同 promotion_id，只有 1 个成功
- `test_publish_with_other_tenant_fails`：尝试 publish 跨租户的 promotion → 0 行匹配（RLS + tenant_id WHERE 双保险）

### 3.9 FB8 SQL 与 Python 日期口径一致

**Q9**：urge_status 在 SQL 与 Python 双实现时如何避免日期口径漂移？

[Answer]: 严格统一日期源头：

**SQL 不用 CURRENT_DATE，改为传入 :today 参数**：
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

**应用层统一日期获取入口**（考虑租户时区，MVP 阶段全部 Asia/Shanghai 硬编码）：
```python
# modules/promotion/urge_calculator.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

DEFAULT_TENANT_TZ = ZoneInfo("Asia/Shanghai")  # MVP 硬编码

def get_today() -> date:
    """统一日期获取入口。SQL 和 Python 必须使用同一个 today 值。"""
    return datetime.now(DEFAULT_TENANT_TZ).date()
```

service 层调用 SQL 时传入：
```python
today = get_today()
stmt = text("...(SQL CTE 表达式)...").bindparams(
    today=today,
    urge_days=URGE_THRESHOLD_DAYS,
    important_days=IMPORTANT_THRESHOLD_DAYS,
)
```

**测试覆盖**：
- 用 `freezegun.freeze_time('2026-05-26 23:59:00 UTC')` 冻结时间
- 100 mock 数据 Python vs SQL 一致（传同一 `today` 参数）
- 边界用例：scheduled_publish_date == today（应为"重要催发"）/ 跨年边界

### 3.10 FB1 部署一致性补充

**Q10**：SettlementRequested required_handler=True 后，如何在部署层面避免 U04 上线但 U05 未上线导致 review approve 整体 5xx？

[Answer]: 多层防护：

**1. Migration 顺序**：U04+U05 同次部署（005_u04 + 006_u05 在同一 PR / 一次性 alembic upgrade）
**2. CI 检查**：grep 检查 `from app.modules.finance.listeners import register` 调用链存在
**3. Smoke 测试**：staging 部署后必跑 `test_review_approve_creates_settlement_via_event` 端到端测试，失败则不允许 production 部署
**4. 启动检查**：
- 真实生产部署：`assert_listener_registered("SettlementRequested")` 后置脚本，无 handler 则 abort
- 测试场景：允许 ModuleNotFoundError + warning 降级
**5. 业务文档**：U04 README + deployment-architecture.md 明确"U04 必须与 U05 同批或后于 U05 部署"

---

## 6. 更新后决策总览

| 反馈 | 处理 |
|---|---|
| FB1 SettlementRequested 无监听器风险 | 引入事件分类（required_handler=True/False）+ U04 强制 U05 同批部署 |
| FB2 序列号首次创建 race | 改用 INSERT ... ON CONFLICT DO UPDATE RETURNING 原子操作 |
| FB3 ImportError 吞错 | 改 ModuleNotFoundError + warning + Sentry；其他 ImportError fail fast |
| FB4 必要/通知事件区分 | 事件 dataclass 增加 required_handler 类属性 |
| FB5 audit 失败脱敏 + 兜底 | 仅记 error_type/error_code/event_id/request_id；audit 写失败不覆盖原异常 |
| FB6 防重复注册 | subscribe 幂等 + clear_handlers() 启动清空 |
| FB7 状态机 WHERE 条件强化 | 加 tenant_id + is_active + 跨状态机旧值校验 |
| FB8 日期口径一致 | SQL 不用 CURRENT_DATE，统一传 :today 参数 + freezegun 测试 |
