# U04 技术栈决策（Tech Stack Decisions）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性技术选型；通用技术栈见 U01 + U02 + U03

---

## 1. 与 U01-U03 技术栈的关系

### 1.1 完全继承

| 类别 | 来源 | 版本 | U04 沿用 |
|---|---|---|---|
| Web Framework | U01 | FastAPI 0.115.x | ✓ |
| ORM | U01 | SQLAlchemy 2.0.x async | ✓ |
| DB Driver | U01 | asyncpg 0.30.x | ✓ |
| 数据库 | U01 | PostgreSQL 16.x | ✓ |
| pg_trgm 扩展 | U02 启用 | — | ✓ |
| Schema 验证 | U01 | Pydantic 2.x（strict） | ✓ |
| 状态机基类 | U01 `core/state_machine.py` | — | **首次实战使用** |
| 类型检查 | U01 | mypy strict | ✓ |
| Linter | U01 | ruff | ✓ |
| 测试 | U01 | pytest + pytest-asyncio | ✓ |
| 监控 | U01 | Prometheus + Sentry + Loki | ✓ |
| Migration | U01 | Alembic | ✓ |

### 1.2 复用 U02/U03 设计模式

| 模式 | 来源 | U04 应用 |
|---|---|---|
| 字段权限硬编码过渡 | U02 P-U02-02 | `legacy_field_permissions.py` 含 AMOUNT_VISIBLE_ROLES + AMOUNT_WRITABLE_ROLES |
| 审计敏感值脱敏 | U02 BR-U02-31 / U03 BR-U03-30 | quote_amount / cost_snapshot 仅记 `*_changed: true` |
| match 降级语义 | U02 P-U02-01 | list service 不 try/except DB 异常 |

### 1.3 U04 增量决策

| 决策项 | 选项 | 理由 |
|---|---|---|
| 状态机实现 | **U01 core/state_machine.py 基类 + transition table** | §2 |
| 衍生字段实现 | **SQL 表达式 + Python 双实现** | §3 |
| 序列号生成 | **promotion_sequence 表 + 行级锁** | §4 |
| CTE 性能保证 | **复合索引 + nightly 基准测试** | §5 |
| 事件总线实现 | **本地 asyncio + dict registry（同事务）** | §6 |

---

## 2. 状态机实现：U01 core/state_machine.py 首次实战

### 2.1 决策
使用 U01 阶段已建立的 `core/state_machine.py` `StateMachine` 基类（之前未在业务代码使用）。

### 2.2 实施模式

```python
# modules/promotion/state_machines.py
from app.core.state_machine import StateMachine, Transition
from app.modules.promotion.enums import PublishStatus, RecallStatus, SettlementStatus


class PublishStatusMachine(StateMachine[PublishStatus]):
    """publish_status 状态机（5 状态）。"""
    
    transitions: list[Transition[PublishStatus]] = [
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.PUBLISHED, "publish"),
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.CANCELLED, "cancel"),
        Transition(PublishStatus.UNPUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
        Transition(PublishStatus.PUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
        Transition(PublishStatus.ABNORMAL, PublishStatus.UNPUBLISHED, "restore"),
    ]


class RecallStatusMachine(StateMachine[RecallStatus]):
    """recall_status 状态机（4 状态）。"""
    
    transitions: list[Transition[RecallStatus]] = [
        Transition(RecallStatus.NOT_RECALLED, RecallStatus.RECALLING, "start_recall"),
        Transition(RecallStatus.RECALLING, RecallStatus.RECALLED_SUCCESS, "recall_success"),
        Transition(RecallStatus.RECALLING, RecallStatus.RECALLED_FAILURE, "recall_failure"),
        Transition(RecallStatus.RECALLED_FAILURE, RecallStatus.RECALLING, "start_recall"),
    ]


class SettlementStatusMachine(StateMachine[SettlementStatus]):
    """settlement_status 状态机（5 状态）。"""
    
    transitions: list[Transition[SettlementStatus]] = [
        Transition(SettlementStatus.NOT_REVIEWED, SettlementStatus.PENDING_REVIEW, "auto_advance"),
        Transition(SettlementStatus.PENDING_REVIEW, SettlementStatus.PENDING_PAYMENT, "approve"),
        Transition(SettlementStatus.PENDING_REVIEW, SettlementStatus.REJECTED, "reject"),
        Transition(SettlementStatus.REJECTED, SettlementStatus.PENDING_REVIEW, "request_review"),
        Transition(SettlementStatus.PENDING_PAYMENT, SettlementStatus.PAID, "mark_paid"),
    ]
```

### 2.3 service 层乐观并发实现

```python
# modules/promotion/service.py
async def publish(self, promotion_id, payload, user) -> Promotion:
    promotion = await self._repo.get_by_id(promotion_id)
    if not promotion:
        raise PromotionNotFoundError()
    
    # 校验状态机转移
    PublishStatusMachine.assert_can_transition(
        from_state=PublishStatus(promotion.publish_status),
        to_state=PublishStatus.PUBLISHED,
        event="publish",
    )
    
    # 乐观并发 UPDATE
    stmt = (
        update(Promotion)
        .where(
            Promotion.id == promotion_id,
            Promotion.publish_status == PublishStatus.UNPUBLISHED.value,  # ← 旧状态
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
            "另一会话已修改状态，请重试"
        )
    
    # 同事务推进 settlement_status + 发事件 + audit
    ...
```

### 2.4 转移指标
service 层每次成功转移调用：
```python
promotion_state_transitions_total.labels(
    from_state="未发布", to_state="已发布", status_field="publish"
).inc()
```

---

## 3. 衍生字段：SQL 表达式 + Python 双实现

### 3.1 决策
不持久化 5 个衍生字段（urge_status / dual_platform / effective_like_count / is_hit / cpl），通过 SQL CTE 表达式 + Python service 层双实现。

### 3.2 候选方案对比

| 方案 | 优势 | 劣势 | 选用 |
|---|---|---|---|
| 持久化字段 + 触发器 | 查询快 | 数据漂移风险，阈值/系数变更需全表更新 | ❌ |
| 持久化字段 + 应用层维护 | 查询快 | 同上 + 应用层维护成本高 | ❌ |
| **SQL 表达式 + Python 双实现** | 阈值/系数实时变更生效 | CTE 计算开销 | ✅ |
| Materialized View | 查询快，独立刷新 | 数据延迟，刷新成本 | 备选（V2+ 评估） |

### 3.3 选用双实现的理由
- 阈值（HIT_THRESHOLD_LIKE_COUNT）/ 系数（PLATFORM_LIKE_COEFFICIENT）调整后**实时生效**，无需重算历史数据
- 单租户 ≤ 10 万行 + 复合索引下 P95 ≤ 300ms 可控
- 双实现保证一致性（单元测试 100 mock 数据 Python vs SQL）
- 升级路径：突破 10 万行 → V1+ 评估 stored generated columns

### 3.4 双实现一致性测试

```python
# tests/unit/test_urge_calculator.py
@pytest.mark.parametrize("scenario", generate_100_urge_scenarios())
async def test_urge_calculator_python_vs_sql(scenario, session, tenant_a):
    """100 个 mock 数据，对比 Python 计算与 SQL 计算结果."""
    # Python 实现
    py_result = calculate_urge_status(
        publish_status=scenario.publish_status,
        scheduled_publish_date=scenario.scheduled_publish_date,
        today=scenario.today,
        urge_threshold_days=10,
        important_threshold_days=3,
    )
    
    # SQL 实现（在 PG 上执行 CASE 表达式）
    sql_result = await session.execute(text("SELECT CASE ... END FROM ..."))
    
    assert py_result == sql_result.scalar_one()
```

---

## 4. 序列号生成：promotion_sequence 表 + 行级锁

### 4.1 候选方案对比

| 方案 | 性能 | 复杂度 | 选用 |
|---|---|---|---|
| **promotion_sequence 表 + 行级锁** | 中（1 QPS 下完全足够） | 低 | ✅ |
| PostgreSQL Sequence 对象（每天每租户一个） | 高 | 高（需建表登记 + 动态创建） | 备选（V2+ 多租户超大并发） |
| Redis INCR | 高 | 中（Redis 单点 / 持久化复杂） | 排除（U04 不引入新依赖） |
| 雪花算法（不需序列号 4 位） | 高 | 中 | 排除（不符合业务格式） |

### 4.2 选用 promotion_sequence + 行级锁的理由
- 业务峰值：单租户单天 50 promotion/min ~ 1 QPS，行级锁完全可接受
- 实施简单（一张表 + `SELECT FOR UPDATE` + INSERT/UPDATE）
- 同事务保证 promotion + sequence 一致性
- V2+ 评估升级 PostgreSQL Sequence

### 4.3 实施代码

详见 `domain-entities.md` §4。

### 4.4 监控
`promotion_sequence_lock_duration_seconds` Histogram 监控锁等待，超 500ms 告警。

---

## 5. CTE 性能：复合索引 + nightly 基准测试

### 5.1 关键索引

```sql
CREATE INDEX idx_promotion_publish_dates ON promotion 
  (tenant_id, publish_status, scheduled_publish_date);
```

支撑 urge_status CTE 计算（PostgreSQL 14+ inlined CTE optimization）。

### 5.2 EXPLAIN ANALYZE 验证
nightly 性能测试包含：
```sql
EXPLAIN ANALYZE
WITH base AS (
  SELECT p.*, CASE ... END AS urge_status FROM promotion p
  WHERE p.tenant_id = $1 AND p.is_active = true
)
SELECT * FROM base WHERE urge_status = '催发' LIMIT 20;
```

期望：`Bitmap Index Scan on idx_promotion_publish_dates`。

### 5.3 升级路径
- 突破 10 万行 + P95 > 500ms → 评估 stored generated columns
- 突破 50 万行 → 评估 Materialized View（每 5 分钟刷新）

---

## 6. 事件总线：本地 asyncio + dict registry

### 6.1 决策
最小实现 — 本地内存 dict + asyncio：

```python
# core/events.py
from collections.abc import Awaitable, Callable
from typing import Any

EventHandler = Callable[[Any], Awaitable[None]]

_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event_type: str, handler: EventHandler) -> None:
    _handlers.setdefault(event_type, []).append(handler)


async def dispatch(event: Any) -> None:
    """同事务同步触发，监听器异常自然冒泡导致事务回滚。"""
    for handler in _handlers.get(event.event_type, []):
        await handler(event)
```

### 6.2 监听器注册位置

```python
# modules/finance/__init__.py（U05 单元）
from app.core.events import subscribe
from app.modules.finance.service import SettlementService

def register_listeners() -> None:
    subscribe(
        "SettlementRequested",
        SettlementService.handle_settlement_requested,
    )
```

在 `app/main.py` 启动钩子中调用 `register_listeners()`。

### 6.3 升级路径（V1+）
- 触发条件：监听器数量 ≥ 5 / 监听器涉及外部 API
- 升级方案：
  1. 引入 Outbox 表（同事务写）
  2. Celery worker 异步消费 Outbox 投递到目标系统
  3. 失败重试 + 死信队列
- 当前 MVP 不实施

---

## 7. 字段级权限（U02/U03 模式延续）

### 7.1 临时常量文件

```python
# modules/promotion/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U04.

REMOVE AFTER U09 (字段级权限) is implemented.
"""

AMOUNT_VISIBLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager", "finance"}
)
"""可见 quote_amount / cost_snapshot 字段的角色。"""

AMOUNT_WRITABLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager"}
)
"""可写 quote_amount / cost_snapshot 字段的角色（finance 仅读）。"""


def has_amount_visibility(role_codes) -> bool: ...
def has_amount_writable(role_codes) -> bool: ...
```

### 7.2 实施位置
- `service.PromotionService._to_response`：按角色过滤字段
- `service.PromotionService._check_amount_write_permission`：写权限校验

---

## 8. legacy_settings.py（衍生字段配置硬编码）

### 8.1 决策
U04 阶段不引入 system_setting 表（避免范围蔓延），用硬编码 + TODO V1 注释：

```python
# modules/promotion/legacy_settings.py
"""TEMPORARY: System settings hardcoded for U04.

REMOVE AFTER V1 system_setting 单元 is implemented.
"""

from decimal import Decimal

PLATFORM_LIKE_COEFFICIENT: dict[str, Decimal] = {
    "小红书": Decimal("1.0"),
    "抖音": Decimal("0.1"),
    "快手": Decimal("0.1"),
    "B站": Decimal("1.0"),
}

HIT_THRESHOLD_LIKE_COUNT: int = 1000
URGE_THRESHOLD_DAYS: int = 10
IMPORTANT_THRESHOLD_DAYS: int = 3
```

### 8.2 V1 升级路径
V1+ system_setting 单元实施后：
- 新建 `system_setting` 表（key-value，tenant_id 分租户）
- 重写 `legacy_settings.py` 为 `SystemSettingsService.get_promotion_settings()`
- grep `legacy_settings` 替换全部引用
- 删除 `legacy_settings.py`

---

## 9. 依赖项变更

### 9.1 Python 依赖
**无新增**。U01-U03 已涵盖全部依赖。

### 9.2 PostgreSQL 扩展
**无新增**。`pg_trgm` 已由 U02 启用。

### 9.3 其他
**无**。

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 所有技术选型与 U01-U03 兼容 | ✅ |
| pg_trgm 扩展复用 U02 | ✅ |
| 状态机基类首次实战使用 U01 core/state_machine.py | ✅ |
| 衍生字段 SQL + Python 双实现 + 一致性测试 | ✅ |
| 序列号 promotion_sequence + 行级锁（V2+ 升级路径） | ✅ |
| 事件总线本地 asyncio + dict（V1+ Outbox 升级路径） | ✅ |
| 字段权限模式与 U02/U03 一致 | ✅ |
| legacy_settings.py 硬编码 + TODO V1 清理路径 | ✅ |
| 无新依赖 / 无版本冲突 | ✅ |
