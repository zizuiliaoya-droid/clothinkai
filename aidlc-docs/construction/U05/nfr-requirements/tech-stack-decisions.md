# U05 技术栈决策（Tech Stack Decisions）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性技术选型；通用技术栈见 U01 + U02 + U03 + U04

---

## 1. 与 U01-U04 技术栈的关系

### 1.1 完全继承

| 类别 | 来源 | 版本 | U05 沿用 |
|---|---|---|---|
| Web Framework | U01 | FastAPI 0.115.x | ✓ |
| ORM | U01 | SQLAlchemy 2.0 + asyncpg | ✓ |
| Migration | U01 | Alembic 1.13.x | ✓ |
| 缓存 | U01 | Redis 7 + redis-py 5 | ✓ |
| 任务调度 | U01 | Celery 5.x | ✓ |
| JWT | U01 | PyJWT 2.x | ✓ |
| Password hash | U01 | passlib[bcrypt] | ✓ |
| 日志 | U01 | structlog | ✓ |
| 监控 | U01 | prometheus-fastapi-instrumentator + prometheus_client | ✓ |
| 错误追踪 | U01 | sentry-sdk[fastapi] | ✓ |
| 测试 | U01 | pytest + pytest-asyncio + pytest-cov | ✓ |
| 模糊搜索 | U02 | pg_trgm GIN | ✓（settlement_no） |
| 状态机基类 | U01 / U04 | core/state_machine.py | ✓（SettlementStatusMachine） |
| 事件总线 | U04 | core/events.py | ✓（监听 + 发出） |
| 时区入口 | U04 | urge_calculator.get_today | ✓（daily-summary 复用 FB8） |
| Attachment | U01 | core/attachment.py + AttachmentService | ✓（首次使用 private 桶 + FB4 强校验） |

### 1.2 复用 U02/U03/U04 设计模式

| 模式 | 来源 | U05 应用 |
|---|---|---|
| 字段权限硬编码过渡 | U02 P-U02-02 | `legacy_field_permissions.py` 含 PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD（3 类，比 U04 多一类） |
| 审计敏感值脱敏 | U02/U03/U04 | `*_changed: true` 标记策略（amount / total_amount / payment_amount / payment_proof_attachment_id） |
| 状态机乐观并发 WHERE | U04 FB7 | settlement.update_state 完全相同模式（含 tenant_id + 旧 status 防护） |
| 序列号原子分配 | U04 FB2 | settlement_sequence INSERT ON CONFLICT DO UPDATE RETURNING |
| 同事务事件总线 | U04 FB1 | SettlementRequested handler 复用 + 内部 flush（FB6） |
| 防重复注册 | U04 FB6 | clear_handlers + subscribe 幂等 |
| 时区一致 | U04 FB8 | daily-summary 用 get_today 入口 |

### 1.3 U05 增量决策（5 个）

| 决策项 | 选项 | 理由 |
|---|---|---|
| 反向事件 listener 注册位置 | modules/promotion/listeners.py 双向注册 | U05 → U04 通知类反向，与 U04 → U05 强一致正向并存 |
| attachment 6 项校验封装 | service 层独立 helper（不放 AttachmentService 内置） | 通用性差（每模块校验项不同）；U05 独立封装 ProofAttachmentValidator |
| 双口径汇总 SQL 实现位置 | repository.py 内部 | 不抽 service 层（无业务编排），直接 SQL + Pydantic 包装 |
| backfill migration | 独立 008（非 007 downgrade 后追加） | FB8 修正；复用 settlement_sequence 与 format_settlement_no 与正常路径完全一致 |
| 财务记录不可替换实现 | Router 层硬编码 405 | 防御深度：DELETE 接口直接 405，不下沉到 service 层 |

---

## 2. 状态机实现（复用 U04 模式）

### 2.1 决策
SettlementStatusMachine 完全复用 U04 PublishStatusMachine / RecallStatusMachine / SettlementStatusMachine（U04 端）的实施模式：

```python
# modules/finance/state_machines.py
from typing import ClassVar
from app.core.exceptions import IllegalStateTransitionError
from app.core.state_machine import TransitionRule
from app.modules.finance.enums import SettlementStatus


class SettlementStatusMachine:
    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="approve",
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            actor_roles=("pr_manager", "admin"),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=("pr_manager", "admin"),
            required_fields=("review_reason",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=("pr_manager", "admin"),
            required_fields=("review_reason",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="fill_payment",
            to_state=SettlementStatus.PENDING_FINANCE.value,
            actor_roles=("pr_manager", "admin"),
            required_fields=("payment_amount",),
        ),
        TransitionRule(
            from_state=SettlementStatus.PENDING_FINANCE.value,
            action="mark_paid",
            to_state=SettlementStatus.PAID.value,
            actor_roles=("finance", "admin"),
            required_fields=("payment_date", "payment_proof_attachment_id"),
        ),
        TransitionRule(
            from_state=SettlementStatus.REJECTED.value,
            action="resubmit",
            to_state=SettlementStatus.PENDING_REVIEW.value,
            actor_roles=("pr_manager", "admin", "system"),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls,
        from_state: str | SettlementStatus,
        to_state: str | SettlementStatus,
        action: str,
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
    def get_allowed_transitions(cls, from_state: str | SettlementStatus) -> list[tuple[str, str]]:
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]
```

---

## 3. 事件总线实现（复用 U04 + 扩展反向 listener）

### 3.1 决策
完全复用 U04 引入的 `core/events.py`（subscribe / dispatch / clear_handlers / required_handler 分类）。U05 仅新增：

1. **新事件类型**：`modules/finance/events.py::SettlementPaid`（required_handler=False）
2. **新 listener 模块**：
   - `modules/finance/listeners.py::on_settlement_requested` — 监听 U04 的 SettlementRequested（强一致）
   - `modules/promotion/listeners.py::on_settlement_paid` — 监听 U05 的 SettlementPaid（通知类）
3. **register_event_listeners 扩展**：U04 已实施的 `main.py::register_event_listeners` 函数已含 finance 模块加载入口，U05 实施时新建 `modules/promotion/listeners.py` + 在 register 函数追加 `register_promotion_listeners()` 调用

### 3.2 反向 listener 注册框架

```python
# main.py register_event_listeners 扩展（U04 已落地基础）
def register_event_listeners() -> None:
    clear_handlers()

    # 1. U05 finance listener（强一致正向）
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        log.warning("u05_finance_module_not_found_skipping_listener_registration. ...")
        return
    register_finance()

    # 2. U04 promotion 反向 listener（通知类，FB5）
    try:
        from app.modules.promotion.listeners import register as register_promotion_listeners
    except ModuleNotFoundError:
        log.warning("promotion_listeners_module_not_found. SettlementPaid will be dropped (acceptable).")
        return  # 注：U04 listener 缺失不阻塞，因 SettlementPaid required_handler=False
    register_promotion_listeners()
```

### 3.3 V1+ 升级路径
继承 U04 §6.4 决策：
- 触发条件：单事件触发 ≥ 5 个监听器 / 监听器涉及外部 API
- 升级方案：Outbox 模式
- MVP（U04+U05）双向各 1 个 listener，同事务足够

---

## 4. attachment 6 项强校验（FB4）

### 4.1 决策
- 不放 AttachmentService 内置（通用性差）
- U05 独立封装 `ProofAttachmentValidator` helper

### 4.2 实施代码

```python
# modules/finance/attachment_validator.py
from app.core.attachment import AttachmentService
from app.modules.finance.exceptions import (
    InvalidAttachmentReferenceError,
    InvalidAttachmentBucketError,
    InvalidAttachmentPurposeError,
    InvalidAttachmentMimeError,
    AttachmentTooLargeError,
    AttachmentNotReadyError,
)
from app.core.metrics import attachment_validation_failures_total

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


class ProofAttachmentValidator:
    """付款截图 attachment 6 项强校验（FB4）。"""
    
    def __init__(self, attachment_service: AttachmentService):
        self._service = attachment_service
    
    async def validate(self, *, attachment_id: UUID, tenant_id: UUID) -> Attachment:
        """6 项校验全部通过返回 Attachment 实例；任一失败抛对应异常."""
        attachment = await self._service.get_by_id(attachment_id)
        if attachment is None:
            self._record_failure("not_found")
            raise InvalidAttachmentReferenceError(
                "attachment 不存在", details={"attachment_id": str(attachment_id)}
            )
        
        # 1. tenant_id（防越权 + Sentry warning）
        if attachment.tenant_id != tenant_id:
            self._record_failure("tenant_mismatch")
            sentry_sdk.capture_message(
                "potential_cross_tenant_attempt",
                level="warning",
                extras={"attachment_id": str(attachment_id), "expected_tenant_id": str(tenant_id)},
            )
            raise InvalidAttachmentReferenceError(
                "attachment 不属于当前租户",
                details={"attachment_id": str(attachment_id)},
            )
        
        # 2. bucket
        if attachment.bucket != "private":
            self._record_failure("bucket_invalid")
            raise InvalidAttachmentBucketError(
                f"attachment.bucket={attachment.bucket}，要求 private",
                details={"actual": attachment.bucket, "expected": "private"},
            )
        
        # 3. purpose
        if attachment.purpose != "settlement_proof":
            self._record_failure("purpose_invalid")
            raise InvalidAttachmentPurposeError(
                f"attachment.purpose={attachment.purpose}，要求 settlement_proof",
                details={"actual": attachment.purpose, "expected": "settlement_proof"},
            )
        
        # 4. mime
        if attachment.mime_type not in ALLOWED_MIME:
            self._record_failure("mime_invalid")
            raise InvalidAttachmentMimeError(
                f"attachment.mime_type={attachment.mime_type} 不在白名单",
                details={"actual": attachment.mime_type, "allowed": sorted(ALLOWED_MIME)},
            )
        
        # 5. size
        if attachment.size_bytes > MAX_SIZE_BYTES:
            self._record_failure("size_too_large")
            raise AttachmentTooLargeError(
                f"attachment.size_bytes={attachment.size_bytes}，最大 {MAX_SIZE_BYTES}",
                details={"actual_bytes": attachment.size_bytes, "max_bytes": MAX_SIZE_BYTES},
            )
        
        # 6. status
        if attachment.status != "ready":
            self._record_failure("status_not_ready")
            raise AttachmentNotReadyError(
                f"attachment.status={attachment.status}，要求 ready",
                details={"actual": attachment.status, "expected": "ready"},
            )
        
        return attachment
    
    def _record_failure(self, failure_type: str) -> None:
        attachment_validation_failures_total.labels(
            failure_type=failure_type, source_module="finance"
        ).inc()
```

### 4.3 集成方式
service.upload_payment_proof 入口调 `validator.validate(attachment_id=..., tenant_id=...)` 取出已校验的 attachment 实例。

---

## 5. 双口径汇总实现（FB7）

### 5.1 决策
- 直接 SQL 实现（不抽 Materialized View）
- 实现位置：`repository.py` 内部 `daily_summary_as_of` / `daily_summary_activity` 方法
- 不抽 service 层（无业务编排，仅数据查询）

### 5.2 实施

```python
# modules/finance/repository.py
class SettlementRepository:
    async def daily_summary_as_of(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict]:
        """口径 B：截至当日各状态快照（FB7）."""
        stmt = text("""
            SELECT settlement_status, COUNT(*) AS cnt, SUM(total_amount) AS sum_amt
            FROM settlement
            WHERE tenant_id = :tid
              AND created_at < :end
            GROUP BY settlement_status
        """).bindparams(tid=tenant_id, end=date + timedelta(days=1))
        result = await self._session.execute(stmt)
        return {row.settlement_status: {"count": row.cnt, "total_amount": str(row.sum_amt or 0)} for row in result}
    
    async def daily_summary_activity(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict]:
        """口径 A：当天发生的动作（FB7）."""
        stmt = text("""
            WITH
              newly_created AS (
                SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS sum_amt
                FROM settlement
                WHERE tenant_id = :tid AND created_at >= :start AND created_at < :end
              ),
              newly_paid AS (
                SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS sum_amt
                FROM settlement
                WHERE tenant_id = :tid AND payment_date = :date
              ),
              newly_approved AS (
                SELECT COUNT(DISTINCT al.resource_id) AS cnt,
                       COALESCE(SUM(s.total_amount), 0) AS sum_amt
                FROM audit_log al
                JOIN settlement s ON s.id::text = al.resource_id
                WHERE al.tenant_id = :tid
                  AND al.action = 'settlement.review.approve'
                  AND al.created_at >= :start AND al.created_at < :end
              ),
              newly_rejected AS (
                SELECT COUNT(DISTINCT al.resource_id) AS cnt,
                       COALESCE(SUM(s.total_amount), 0) AS sum_amt
                FROM audit_log al
                JOIN settlement s ON s.id::text = al.resource_id
                WHERE al.tenant_id = :tid
                  AND al.action = 'settlement.review.reject'
                  AND al.created_at >= :start AND al.created_at < :end
              )
            SELECT
              (SELECT cnt FROM newly_created) AS created_cnt,
              (SELECT sum_amt FROM newly_created) AS created_amt,
              (SELECT cnt FROM newly_approved) AS approved_cnt,
              (SELECT sum_amt FROM newly_approved) AS approved_amt,
              (SELECT cnt FROM newly_paid) AS paid_cnt,
              (SELECT sum_amt FROM newly_paid) AS paid_amt,
              (SELECT cnt FROM newly_rejected) AS rejected_cnt,
              (SELECT sum_amt FROM newly_rejected) AS rejected_amt
        """).bindparams(tid=tenant_id, start=date, end=date + timedelta(days=1))
        row = (await self._session.execute(stmt)).one()
        return {
            "newly_created": {"count": row.created_cnt, "total_amount": str(row.created_amt)},
            "newly_approved": {"count": row.approved_cnt, "total_amount": str(row.approved_amt)},
            "newly_paid": {"count": row.paid_cnt, "total_amount": str(row.paid_amt)},
            "newly_rejected": {"count": row.rejected_cnt, "total_amount": str(row.rejected_amt)},
        }
```

### 5.3 V1+ Materialized View 升级
触发条件 + 升级方案见 nfr-requirements.md §9.3。

---

## 6. 字段权限实施（复用 U04 模式 — 3 类）

### 6.1 legacy_field_permissions.py

```python
# modules/finance/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U05.

REMOVE AFTER U09 (字段级权限) is implemented.

To find all usage::

    grep -rn "legacy_field_permissions" backend/

按 NFR Requirements §5：U05 阶段 service 层硬编码角色判断，
U09 阶段统一切换为 ``Permission.field_filter()`` / ``Permission.field_writable()``。
"""

from __future__ import annotations

PAYMENT_VISIBLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager", "finance"
})

PAYMENT_WRITABLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager"
})

PROOF_UPLOAD_ROLES: frozenset[str] = frozenset({
    "admin", "finance"
})


def has_payment_visibility(role_codes) -> bool: ...
def has_payment_writable(role_codes) -> bool: ...
def has_proof_upload(role_codes) -> bool: ...
def has_extra_item_writable(role_codes) -> bool:
    return has_payment_writable(role_codes)  # 与 PAYMENT_WRITABLE_ROLES 一致

# 全部函数实现与 U04 has_amount_visibility 同模式（set 交集）
```

### 6.2 演进路径
V1 实施 U09 字段级权限：
- 替换 `legacy_field_permissions.py` 为 `Permission.field_filter()` / `Permission.field_writable()`
- 服务端 BR-U05-50/51 统一改为装饰器形式
- 现有 audit 脱敏策略保留（FB3 + FB4 已强化的脱敏标记）

---

## 7. 财务记录不可替换实施（FB3）

### 7.1 决策
- Router 层硬编码 DELETE 接口 → 405 Method Not Allowed
- 防御深度：不下沉到 service 层

### 7.2 实施代码

```python
# modules/finance/api.py
from fastapi import APIRouter, status

router = APIRouter(prefix="/api", tags=["finance"])

# 显式声明 DELETE → 405（FB3：财务记录永久不可替换）
@router.delete("/settlements/{settlement_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED)
async def delete_settlement_not_allowed(settlement_id: UUID) -> None:
    """财务记录永久不可删除（FB3）。
    
    若需取消未审核的 settlement → 走 reject 路径到"已驳回"。
    若需修正已付款的 settlement → V2 通过 order_adjustment 调整单。
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail={
            "code": "METHOD_NOT_ALLOWED",
            "message": "财务记录不可删除；请走 reject 或 V2 调整单流程",
        },
    )
```

### 7.3 promotion 软删时不级联实施
- U04 promotion.soft_deactivate 不调任何 settlement 相关接口
- U05 端无任何 listener 监听 promotion 软删事件
- audit_log 自动留痕（U04 端 audit）

---

## 8. settlement_no 生成（复用 U04 FB2 模式）

### 8.1 决策
完全复用 U04 PromotionSequence INSERT ON CONFLICT DO UPDATE 模式：

```python
# modules/finance/repository.py
class SettlementRepository:
    async def next_settlement_sequence(
        self, *, tenant_id: UUID, date_key: date,
    ) -> int:
        start = time.perf_counter()
        try:
            stmt = text("""
                INSERT INTO settlement_sequence
                    (id, tenant_id, date_key, last_seq, created_at, updated_at)
                VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
                ON CONFLICT (tenant_id, date_key) DO UPDATE
                SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
                RETURNING last_seq
            """)
            result = await self._session.execute(stmt, {"tid": tenant_id, "dk": date_key})
            next_seq = int(result.scalar_one())
        finally:
            settlement_sequence_lock_duration_seconds.observe(time.perf_counter() - start)
        
        if next_seq > 9999:
            raise SequenceOverflowError(
                f"当天序号已达 {next_seq}，超出 9999 上限",
                details={"tenant_id": str(tenant_id), "date_key": str(date_key)},
            )
        return next_seq
```

### 8.2 backfill migration（FB8 独立 008）
008 migration 通过 PL/pgSQL 复用同一序列号体系（详见 functional design plan §3.12 Q16）：

```sql
-- alembic/versions/008_u05_backfill_settlements.py upgrade()
DO $$
DECLARE
    r RECORD;
    v_seq INTEGER;
    ...
BEGIN
    FOR r IN SELECT * FROM _backfill_promotions ORDER BY tenant_id, requested_at LOOP
        -- 通过 settlement_sequence 分配（与 next_settlement_sequence 完全一致）
        INSERT INTO settlement_sequence (id, tenant_id, date_key, last_seq, ...)
        VALUES (gen_random_uuid(), r.tenant_id, r.requested_at::date, 1, NOW(), NOW())
        ON CONFLICT (tenant_id, date_key) DO UPDATE
        SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
        RETURNING last_seq INTO v_seq;
        
        -- format_settlement_no（与 service 层完全一致）
        v_no := UPPER(LEFT(COALESCE(t.code, ''), 2)) || 'S' ||
                TO_CHAR(r.requested_at::date, 'YYMMDD') ||
                LPAD(v_seq::TEXT, 4, '0');
        
        INSERT INTO settlement (..., settlement_status, request_event_id, ...)
        VALUES (..., '待核查', gen_random_uuid(), ...);
    END LOOP;
END $$;
```

---

## 9. 测试栈 / freezegun 一致

### 9.1 复用 U04 fixtures
- conftest.py 已含 `_clear_event_handlers` (autouse) + `event_capture` fixture
- U05 测试新增 `settlement_factory` + `attachment_factory`（V1 attachment 框架完善后）

### 9.2 freezegun 用于 daily-summary 边界测试

```python
# tests/integration/test_settlement_daily_summary.py
import pytest
from freezegun import freeze_time

@freeze_time("2026-05-26 12:00:00")  # UTC 12:00 = Asia/Shanghai 20:00
async def test_daily_summary_as_of_at_local_today(...):
    """as_of 截至当地时间今天 23:59:59."""
    ...

@freeze_time("2026-05-26 23:59:00")  # UTC 23:59 = Asia/Shanghai 次日 07:59
async def test_daily_summary_activity_at_utc_boundary(...):
    """边界日：UTC 23:59 时 get_today() 应返回次日（Asia/Shanghai）."""
    ...
```

---

## 10. 部署一致性（继承 U04 多层防护）

### 10.1 U05 deploy 必须与 U04 同批

- Migration chain：006_u04_create_promotion_tables → 007_u05_create_settlement_tables → 008_u05_backfill_settlements
- 一次 alembic upgrade 完整执行
- staging 先验证 → production

### 10.2 CI gate

`.github/workflows/ci.yml::validate-event-listeners` job（U04 batch 4 已实施）：
```yaml
- name: Verify U05 finance listener registration in main.py
  run: |
    grep -rn "from app.modules.finance.listeners import register" backend/app/main.py || exit 1
```

U05 实施时此 grep 必须命中。

### 10.3 staging smoke

deploy-staging.yml::e2e-smoke-after-deploy（U04 batch 4 已搭框架）：
```bash
# 端到端：U04 review approve → U05 settlement 创建 → settlement.review approve → fill_payment → mark_paid
curl -X POST $STAGING_API/api/promotions/$PROMOTION_ID/review \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"action":"approve"}' --fail
# 后续断言 settlement 已创建 + status="待核查"
```

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 完全继承 U01-U04 全部技术栈 | ✅ |
| 状态机复用 U04 模式 | ✅ |
| 事件总线复用 U04 + 反向 listener 注册框架 | ✅ |
| attachment 6 项校验独立封装（FB4） | ✅ |
| 双口径汇总直接 SQL（FB7）+ V1 Materialized View 路径 | ✅ |
| 字段权限 3 类（PAYMENT_VISIBLE / WRITABLE / PROOF_UPLOAD） | ✅ |
| 财务记录不可替换 Router 层 405（FB3） | ✅ |
| settlement_no 生成复用 U04 FB2 模式 | ✅ |
| backfill migration 008 独立 + 复用 sequence + format（FB8） | ✅ |
| freezegun + 部署一致性继承 U04 多层防护 | ✅ |
