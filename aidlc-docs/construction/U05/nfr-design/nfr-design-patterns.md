## U05 NFR 设计模式（NFR Design Patterns）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性 4 个增量模式 + 完全继承 U01-U04 模式  
> 完全继承 U04 8 P1 反馈守护（FB1-FB8），无需重新评估

---

## 1. 与 U01-U04 模式的关系

### 1.1 完全继承
- U01 9 个通用模式（多租户 / 审计 / 状态机基类 / 附件 / 速率限制 / 错误处理 / 监控 / 备份 / 健康检查）
- U02 4 个增量模式（**FB3 不复用 partial UNIQUE，改永久** / 字段权限硬编码 / 数据库原子 upsert（U05 不需要）/ 软删引用检查）
- U03 GIN trgm 单字段（U05 复用：`idx_settlement_no_trgm`）
- **U04 全部 4 个模式 + 8 P1 反馈守护全部继承**

### 1.2 复用方式

| U04 模式 | U05 应用 |
|---|---|
| **P-U04-01 状态机乐观并发** | SettlementStatusMachine + UPDATE WHERE 旧 settlement_status RETURNING（FB7） |
| **P-U04-02 本地同事务事件总线** | 监听 SettlementRequested + 发出 SettlementPaid（FB1+FB5）|
| **P-U04-03 序列号原子分配** | settlement_sequence INSERT ON CONFLICT DO UPDATE RETURNING（FB2） |
| **P-U04-04 CTE 衍生字段双实现** | U05 不需要（settlement 字段全部持久化，无衍生字段）|
| **U02 字段权限硬编码** | PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD（3 类，比 U04 多一类） |
| **U02 audit 脱敏** | amount/total_amount/payment_amount 仅记 `*_changed: true`；attachment_id 仅记 `attachment_id_changed: true`（FB3+FB4） |

### 1.3 U05 增量模式（4 个）

| 模式 | 解决问题 | 章节 |
|---|---|---|
| **P-U05-01** 财务记录永久不可替换 | UNIQUE 永久 + DELETE 405 + 不级联软删 + audit 留痕（FB3） | §2 |
| **P-U05-02** Attachment 6 项强校验封装 | ProofAttachmentValidator 独立 + 跨租户 4 层防御（FB4） | §3 |
| **P-U05-03** 双口径汇总 | activity / as_of 独立 endpoint + repository 内嵌 SQL（FB7） | §4 |
| **P-U05-04** 反向通知事件 + 部署一致性扩展 | SettlementPaid 通知类 + 双向注册 + 失败处理不对称（FB5 + FB10）| §5 |

---

## 2. Pattern P-U05-01 — 财务记录永久不可替换（FB3）

### 2.1 问题
- 财务记录一旦创建必须永久留痕（合规 + 审计）
- 不能通过软删 + 重建绕过唯一约束
- 错误付款修正应通过调整单（V2 order_adjustment），而非删除原 settlement
- 但仍需支持极少场景下 admin 手动作废（带审计）

### 2.2 设计

#### 2.2.1 永久唯一约束（取代 U02 partial UNIQUE 模式）

```sql
-- DDL（对比 U02/U04 partial UNIQUE）
CREATE UNIQUE INDEX uq_settlement_no ON settlement (tenant_id, settlement_no);
-- 注意：无 WHERE is_active=true 子句

CREATE UNIQUE INDEX uq_settlement_promotion ON settlement (tenant_id, promotion_id);
-- 永久唯一：一个 promotion 一辈子只能对应一个 settlement

CREATE UNIQUE INDEX uq_settlement_request_event_id ON settlement (request_event_id);
-- 永久唯一：事件重放兜底
```

#### 2.2.2 模型字段不含 is_active（与 U04 关键差异）

```python
# modules/finance/models.py
class Settlement(TenantScopedModel):
    """财务结算单（永久不可替换）。

    字段表见 functional-design domain-entities.md §3。
    注意：**不含 is_active 字段**（FB3：财务记录不软删）。
    """
    __tablename__ = "settlement"
    
    # ... 字段定义（无 is_active）
    
    __table_args__ = (
        Index("uq_settlement_no", "tenant_id", "settlement_no", unique=True),
        Index("uq_settlement_promotion", "tenant_id", "promotion_id", unique=True),
        Index("uq_settlement_request_event_id", "request_event_id", unique=True),
        # 无 partial WHERE，全部永久唯一
        ...
    )
```

#### 2.2.3 Router 层硬编码 405

```python
# modules/finance/api.py
@router.delete(
    "/settlements/{settlement_id}",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
)
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

实施细节：
- 端点直接抛 405，不下沉到 service 层（防御深度：避免后续误开放）
- error response 与全局格式一致（U01 errors.py register_error_handlers 不覆盖 HTTPException）

#### 2.2.4 promotion 软删时零级联

```python
# modules/finance/listeners.py（不存在 promotion soft_delete listener）
# 设计声明：U05 不监听 promotion soft_delete 事件
# settlement 完全独立于 promotion 的 is_active 字段
```

测试验证（NFR Requirements §12 测试 30）：
- promotion soft_deactivate → settlement 不受影响 + 仍可推进状态

#### 2.2.5 Admin 极少场景手动作废协议

```sql
-- 仅 admin 通过手动 SQL 操作（必须严格 audit）
BEGIN;

-- 1. 操作前快照
INSERT INTO audit_log (action, resource, resource_id, before, after, actor_type, user_id)
SELECT 
    'settlement.admin_void_before',
    'settlement',
    s.id::text,
    row_to_json(s)::jsonb,  -- 完整快照
    NULL,
    'admin_manual',
    :admin_user_id
FROM settlement s WHERE s.id = :settlement_id;

-- 2. 实际作废动作（如更新 review_reason / 添加 voided 标记 V1+ 新增字段）
-- MVP: 暂不提供 voided 字段；admin 仅做 review_reason 备注
UPDATE settlement
SET review_reason = :void_reason || ' [VOIDED BY ADMIN]',
    updated_at = NOW()
WHERE id = :settlement_id;

-- 3. 操作后审计
INSERT INTO audit_log (action, resource, resource_id, before, after, actor_type, user_id)
VALUES ('settlement.admin_void_after', 'settlement', :settlement_id::text, NULL,
        jsonb_build_object('void_reason', :void_reason),
        'admin_manual', :admin_user_id);

COMMIT;
```

V1+ 增强：引入显式 `voided` 状态字段，提供 admin 专用 endpoint（保留 audit 路径）。

### 2.3 监控

```python
# 无新增指标（DELETE 路径直接 405，HTTP instrumentator 自动记录）
# Prometheus alert：rate(http_requests_total{handler="/api/settlements/{settlement_id}", method="DELETE", status="405"}[5m]) > 0
# → Sentry info（前端实施 bug 提示）
```

### 2.4 测试覆盖
- `test_delete_settlement_returns_405`（NFR §12 测试 29）
- `test_settlement_unaffected_by_promotion_soft_delete`（NFR §12 测试 30）
- 单元测试：`UNIQUE(tenant_id, promotion_id)` 永久约束（不带 partial WHERE）

---

## 3. Pattern P-U05-02 — Attachment 6 项强校验封装（FB4）

### 3.1 问题
- 付款截图必须通过 attachment 表引用，不能存裸 R2 key
- 防御跨租户访问（attachment.tenant_id != user.tenant_id）
- 防御 bucket / purpose / mime / size / status 等业务约束被绕过
- 跨租户尝试需要立即报警（潜在越权）

### 3.2 设计

#### 3.2.1 独立封装（不放 AttachmentService 内置）

```python
# modules/finance/attachment_validator.py
from uuid import UUID
import sentry_sdk
from app.core.attachment import AttachmentService
from app.core.audit import AuditService
from app.core.db import AsyncSessionBypass
from app.core.tenancy import bypass_rls_ctx, user_id_ctx
from app.core.metrics import attachment_validation_failures_total
from app.modules.finance.exceptions import (
    InvalidAttachmentReferenceError,
    InvalidAttachmentBucketError,
    InvalidAttachmentPurposeError,
    InvalidAttachmentMimeError,
    AttachmentTooLargeError,
    AttachmentNotReadyError,
)

ALLOWED_MIME = frozenset({
    "image/jpeg", "image/png", "image/webp", "application/pdf"
})
MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
EXPECTED_PURPOSE = "settlement_proof"
EXPECTED_BUCKET = "private"


class ProofAttachmentValidator:
    """付款截图 attachment 6 项强校验（P-U05-02 / FB4）。
    
    校验顺序很重要：
    1. 存在性 → 不暴露 attachment 是否存在
    2. tenant_id（最敏感）→ 跨租户多层报警
    3. bucket / purpose / mime / size / status
    
    任一失败抛对应异常（422）。
    """
    
    def __init__(self, attachment_service: AttachmentService):
        self._service = attachment_service
    
    async def validate(
        self,
        *,
        attachment_id: UUID,
        tenant_id: UUID,
    ) -> "Attachment":
        """6 项校验全部通过返回 Attachment 实例。"""
        attachment = await self._service.get_by_id(attachment_id)
        if attachment is None:
            self._record_failure("not_found")
            raise InvalidAttachmentReferenceError(
                "attachment 不存在或已删除",
                details={"attachment_id": str(attachment_id)},
            )
        
        # 1. tenant_id（防越权 + 4 层防御）
        if attachment.tenant_id != tenant_id:
            await self._handle_cross_tenant_attempt(
                attachment_id=attachment_id,
                expected_tenant_id=tenant_id,
                actual_tenant_id=attachment.tenant_id,
            )
            raise InvalidAttachmentReferenceError(
                "attachment 不属于当前租户",
                details={"attachment_id": str(attachment_id)},
            )
        
        # 2. bucket
        if attachment.bucket != EXPECTED_BUCKET:
            self._record_failure("bucket_invalid")
            raise InvalidAttachmentBucketError(
                f"attachment.bucket={attachment.bucket}，要求 {EXPECTED_BUCKET}",
                details={"actual": attachment.bucket, "expected": EXPECTED_BUCKET},
            )
        
        # 3. purpose
        if attachment.purpose != EXPECTED_PURPOSE:
            self._record_failure("purpose_invalid")
            raise InvalidAttachmentPurposeError(
                f"attachment.purpose={attachment.purpose}，要求 {EXPECTED_PURPOSE}",
                details={"actual": attachment.purpose, "expected": EXPECTED_PURPOSE},
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
                f"attachment.size_bytes={attachment.size_bytes} 超过 {MAX_SIZE_BYTES}",
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
        """所有失败均记 Prometheus 指标。"""
        attachment_validation_failures_total.labels(
            failure_type=failure_type, source_module="finance"
        ).inc()
    
    async def _handle_cross_tenant_attempt(
        self,
        *,
        attachment_id: UUID,
        expected_tenant_id: UUID,
        actual_tenant_id: UUID,
    ) -> None:
        """跨租户尝试 4 层防御：指标 + Sentry + 独立 audit + 抛异常。"""
        # 1. 指标
        self._record_failure("tenant_mismatch")
        
        # 2. Sentry warning（每次都上报，不去重）
        sentry_sdk.capture_message(
            "potential_cross_tenant_attempt",
            level="warning",
            extras={
                "attachment_id": str(attachment_id),
                "expected_tenant_id": str(expected_tenant_id),
                "actual_tenant_id": str(actual_tenant_id),
                "user_id": str(user_id_ctx.get()) if user_id_ctx.get() else None,
                "source_module": "finance",
            },
        )
        
        # 3. 独立 bypass session 写 audit（防被原事务回滚带走）
        bypass_token = bypass_rls_ctx.set(True)
        try:
            try:
                async with AsyncSessionBypass() as audit_session:
                    audit = AuditService(audit_session)
                    await audit.log(
                        action="settlement.attachment_cross_tenant_attempt",
                        resource="settlement",
                        resource_id=None,
                        actor_type="anonymous",
                        user_id=user_id_ctx.get(),
                        after={
                            "attempted_attachment_id": str(attachment_id),
                            "user_tenant_id": str(expected_tenant_id),
                        },
                    )
                    await audit_session.commit()
            except Exception as audit_exc:
                # 兜底：audit 失败仅 log，不阻塞原异常
                import logging
                logging.getLogger(__name__).exception(
                    "audit_for_cross_tenant_failed",
                    extra={"audit_error": type(audit_exc).__name__},
                )
        finally:
            bypass_rls_ctx.reset(bypass_token)
        
        # 4. 抛异常由调用方完成（unique entry point）
```

#### 3.2.2 集成到 service 层

```python
# modules/finance/service.py
class SettlementService:
    def __init__(self, session: AsyncSession, attachment_service: AttachmentService):
        self._session = session
        self._repo = SettlementRepository(session)
        self._validator = ProofAttachmentValidator(attachment_service)
    
    async def upload_payment_proof(
        self,
        settlement_id: UUID,
        payload: SettlementPaymentProofRequest,
        user: User,
    ) -> SettlementResponse:
        # ... 状态机 + 前置校验
        
        # attachment 6 项强校验（P-U05-02）
        attachment = await self._validator.validate(
            attachment_id=payload.payment_proof_attachment_id,
            tenant_id=user.tenant_id,
        )
        # 校验通过后 attachment 已确认安全可用
        
        # 状态推进 (UPDATE WHERE 旧状态)
        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.PENDING_FINANCE.value,
            to_state_value=SettlementStatus.PAID.value,
            extra_fields={
                "payment_date": payload.payment_date,
                "payment_proof_attachment_id": payload.payment_proof_attachment_id,
                "paid_by": user.id,
            },
        )
        # ... 反向事件 + audit + commit
```

### 3.3 监控告警

```python
# core/metrics.py（已在 NFR Requirements §10.1 定义）
attachment_validation_failures_total: Counter = Counter(
    "attachment_validation_failures_total",
    labelnames=("failure_type", "source_module"),
)

# Prometheus alert 配置（NFR §10.3）
- alert: AttachmentCrossTenantAttempt
  expr: rate(attachment_validation_failures_total{failure_type="tenant_mismatch"}[5m]) > 0
  labels:
    severity: warning
  annotations:
    summary: "潜在跨租户 attachment 访问尝试"
```

### 3.4 测试覆盖
- 6 项校验各 1 个测试用例（NFR §12 测试 17-24）
- 跨租户尝试触发 Sentry capture + audit log（mock sentry_sdk + 验证 audit 行）

---

## 4. Pattern P-U05-03 — 双口径汇总（FB7）

### 4.1 问题
- "已付款按 payment_date / 其他按 created_at" 混合口径会误导用户
- 用户既需要"今天发生了什么动作"（activity），也需要"截至今天还差多少"（as_of）
- 需要明确划分两个独立 endpoint
- MVP 不引入 Materialized View，但要预留 V1 升级路径

### 4.2 设计

#### 4.2.1 独立 endpoint（不参数化）

```python
# modules/finance/api.py
@router.get("/settlements/daily-summary/as-of", response_model=DailySummaryAsOfResponse)
async def daily_summary_as_of(
    user: CurrentActiveUser,
    service: SettlementServiceDep,
    date: date | None = None,
):
    """口径 B：截至当日各状态快照。
    
    回答"截至 :date 末，各状态的 settlement 还有多少未处理"。
    """
    return await service.get_daily_summary_as_of(date=date)


@router.get("/settlements/daily-summary/activity", response_model=DailySummaryActivityResponse)
async def daily_summary_activity(
    user: CurrentActiveUser,
    service: SettlementServiceDep,
    date: date | None = None,
):
    """口径 A：当天发生的动作。
    
    回答"当天发生了哪些 settlement 状态推进动作"。
    """
    return await service.get_daily_summary_activity(date=date)
```

#### 4.2.2 SQL 实现位置（repository 内嵌）

```python
# modules/finance/repository.py
class SettlementRepository:
    async def daily_summary_as_of(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict[str, Any]]:
        """口径 B：GROUP BY 简单查询."""
        stmt = text("""
            SELECT
                settlement_status,
                COUNT(*) AS cnt,
                COALESCE(SUM(total_amount), 0) AS sum_amt
            FROM settlement
            WHERE tenant_id = :tid
              AND created_at < :end
            GROUP BY settlement_status
        """).bindparams(tid=tenant_id, end=date + timedelta(days=1))
        
        result = await self._session.execute(stmt)
        return {
            row.settlement_status: {
                "count": row.cnt,
                "total_amount": str(row.sum_amt),
            }
            for row in result.mappings().all()
        }
    
    async def daily_summary_activity(
        self, *, tenant_id: UUID, date: date,
    ) -> dict[str, dict[str, Any]]:
        """口径 A：含 audit_log 跨表 JOIN."""
        stmt = text("""
            WITH
              newly_created AS (
                SELECT COUNT(*) AS cnt, COALESCE(SUM(total_amount), 0) AS sum_amt
                FROM settlement
                WHERE tenant_id = :tid
                  AND created_at >= :start AND created_at < :end
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
        """).bindparams(tid=tenant_id, start=date, end=date + timedelta(days=1), date=date)
        
        row = (await self._session.execute(stmt)).one()
        return {
            "newly_created": {"count": row.created_cnt, "total_amount": str(row.created_amt)},
            "newly_approved": {"count": row.approved_cnt, "total_amount": str(row.approved_amt)},
            "newly_paid": {"count": row.paid_cnt, "total_amount": str(row.paid_amt)},
            "newly_rejected": {"count": row.rejected_cnt, "total_amount": str(row.rejected_amt)},
        }
```

#### 4.2.3 时区一致（继承 U04 FB8）

```python
# modules/finance/service.py
async def get_daily_summary_as_of(
    self, *, date: date | None = None
) -> DailySummaryAsOfResponse:
    """复用 U04 get_today() 入口，时区固定 Asia/Shanghai。"""
    today = date or get_today()  # ← 来自 modules/promotion/urge_calculator.py
    
    # 字段权限校验（PAYMENT_VISIBLE_ROLES）
    role_codes = await self._roles.list_codes_for_user(self._current_user.id)
    if not has_payment_visibility(role_codes):
        raise FieldPermissionDenied(field="settlement_amount")
    
    raw_data = await self._repo.daily_summary_as_of(
        tenant_id=current_tenant_id(),
        date=today,
    )
    
    # 计算 outstanding_total
    outstanding_count = sum(
        v["count"] for k, v in raw_data.items()
        if k in {"待核查", "待付款", "待财务付款"}
    )
    outstanding_amount = sum(
        Decimal(v["total_amount"]) for k, v in raw_data.items()
        if k in {"待核查", "待付款", "待财务付款"}
    )
    
    return DailySummaryAsOfResponse(
        kind="as_of",
        date=today,
        as_of={
            "pending_review": raw_data.get("待核查", {"count": 0, "total_amount": "0"}),
            "pending_payment": raw_data.get("待付款", {"count": 0, "total_amount": "0"}),
            "pending_finance": raw_data.get("待财务付款", {"count": 0, "total_amount": "0"}),
            "paid": raw_data.get("已付款", {"count": 0, "total_amount": "0"}),
            "rejected": raw_data.get("已驳回", {"count": 0, "total_amount": "0"}),
        },
        outstanding_total={
            "count": outstanding_count,
            "total_amount": str(outstanding_amount),
        },
    )
```

### 4.3 V1+ 三层升级路径

| 触发条件 | 升级方案 | Schema 变更 |
|---|---|---|
| activity P95 > 500ms 持续 5min | 物化 daily_settlement_activity Materialized View，每小时 REFRESH CONCURRENTLY | 仅新增视图 |
| audit_log 单日新增 > 1 万 | settlement 新增 last_action_at / last_action_type 冗余字段，避免 JOIN | settlement 加 2 字段 |
| 单租户 settlement > 50 万 | audit_log 1 年归档到 R2 后查询路径不变，活跃数据集减小 | audit_log 归档（已规划 U01）|

### 4.4 监控告警

```python
# Prometheus alerts
- alert: DailySummaryAsOfSlow
  expr: histogram_quantile(0.95, http_request_duration_seconds{handler="/api/settlements/daily-summary/as-of"}) > 0.2
  for: 5m

- alert: DailySummaryActivitySlow
  expr: histogram_quantile(0.95, http_request_duration_seconds{handler="/api/settlements/daily-summary/activity"}) > 0.5
  for: 5m
```

### 4.5 测试覆盖
- `test_daily_summary_as_of_groups_by_status`（NFR §12 测试 26）
- `test_daily_summary_activity_audit_join`（NFR §12 测试 25）
- freezegun 边界日测试（UTC 23:59 vs Asia/Shanghai 次日）

---

## 5. Pattern P-U05-04 — 反向通知事件 + 部署一致性扩展（FB5 + 继承 FB10）

### 5.1 问题
- U05 mark_paid 时需要通知 U04 同步 promotion.settlement_status='已付款'
- 反向事件不应阻塞 U05 主流程（通知类语义）
- 与 U04 SettlementRequested 的强一致语义形成不对称
- 需要 main.py register_event_listeners 双向注册

### 5.2 设计

#### 5.2.1 SettlementPaid 事件（通知类）

```python
# modules/finance/events.py
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True)
class SettlementPaid:
    """U05 → U04 反向通知：mark_paid 时同步 promotion.settlement_status='已付款'.
    
    通知类（required_handler=False）：U04 端 listener 缺失不影响 U05 主流程。
    与 SettlementRequested（required_handler=True）形成不对称。
    """
    event_type: ClassVar[str] = "SettlementPaid"
    required_handler: ClassVar[bool] = False  # ← 关键：通知类
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    payment_amount: Decimal
    payment_date: date
    paid_by: UUID
```

#### 5.2.2 失败处理不对称对比

| 维度 | SettlementRequested（FB1 强一致） | SettlementPaid（FB5 通知类） |
|---|---|---|
| required_handler | True | **False** |
| 无 handler 行为 | 抛 MissingRequiredHandlerError → 5xx | no-op + warning + 指标 |
| handler 抛异常 | 自然冒泡 → 整个事务回滚 | service 层 try/except → log + 指标 + 不阻塞 |
| service 层调用 | `raise` 重新抛出 | **不重新 raise，让 commit 继续** |
| 部署约束 | U05 必须 ≥ U04（CI gate） | 无强制约束 |
| 失败 audit | 写 `promotion.review.event_dispatch_failed`（FB5 脱敏） | 写 `settlement.paid_sync_failed`（同模式脱敏） |
| 监控指标 | `settlement_created_via_event_total{result=error}` | `settlement_paid_sync_no_match_total` |

#### 5.2.3 service 层调用代码

```python
# modules/finance/service.py
class SettlementService:
    async def mark_paid(
        self,
        settlement_id: UUID,
        payload: SettlementPaymentProofRequest,
        user: User,
    ) -> SettlementResponse:
        # ... 状态机 + 前置校验 + UPDATE WHERE 旧状态 + audit
        
        # 反向事件分发（通知类，FB5）
        event = SettlementPaid(
            event_id=uuid4(),
            timestamp=_utcnow(),
            tenant_id=updated.tenant_id,
            settlement_id=updated.id,
            promotion_id=updated.promotion_id,
            payment_amount=updated.payment_amount,
            payment_date=updated.payment_date,
            paid_by=user.id,
        )
        try:
            await event_bus.dispatch(event, session=self._session)
        except Exception as exc:
            # 通知类事件失败不阻塞主流程；与 U04 review approve 不对称
            log.exception("settlement_paid_dispatch_failed")
            sentry_sdk.capture_exception(exc)
            await self._log_event_dispatch_failure(
                event, exc, user, blocking=False
            )
            # 不重新 raise — 让 commit 继续
        
        await self._session.commit()
        return await self._to_response(updated, user)
```

#### 5.2.4 U04 端 listener 实施

```python
# modules/promotion/listeners.py（U05 实施时新建）
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.core.metrics import settlement_paid_sync_no_match_total
from app.modules.finance.events import SettlementPaid
from app.modules.promotion.enums import SettlementStatus
from app.modules.promotion.repository import PromotionRepository

log = logging.getLogger(__name__)


async def on_settlement_paid(
    event: SettlementPaid, session: AsyncSession
) -> None:
    """U05 → U04 反向同步：promotion.settlement_status='已付款'.
    
    通知类（FB5）：
    - 失败不抛错，仅 log + 指标
    - UPDATE WHERE 0 行时（promotion 状态已变更 / 跨租户）记 no_match 指标
    """
    repo = PromotionRepository(session)
    updated = await repo.update_state(
        promotion_id=event.promotion_id,
        tenant_id=event.tenant_id,
        from_state_field="settlement_status",
        from_state_value=SettlementStatus.PENDING_PAYMENT.value,  # U04 端的"待付款"
        to_state_value=SettlementStatus.PAID.value,
    )
    if updated is None:
        # 已被推进 / 跨租户 / 软删；不抛错（FB5 通知类）
        settlement_paid_sync_no_match_total.inc()
        log.warning("settlement_paid_sync_no_match", extra={
            "promotion_id": str(event.promotion_id),
            "settlement_id": str(event.settlement_id),
        })
    else:
        log.info("settlement_paid_synced", extra={
            "promotion_id": str(event.promotion_id),
        })


def register() -> None:
    """U04 端注册 SettlementPaid listener."""
    subscribe("SettlementPaid", on_settlement_paid)
```

#### 5.2.5 main.py 双向注册框架

```python
# app/main.py（U04 batch 4 已落地，U05 实施时扩展）
def register_event_listeners() -> None:
    """注册所有跨单元事件监听器。
    
    策略（继承 U04 FB3+FB6）：
    - clear_handlers() 启动前清空，防热重载累计
    - 仅捕获 ModuleNotFoundError → warning + Sentry breadcrumb
    - 其他 ImportError / Exception → fail fast
    """
    clear_handlers()
    
    # 1. U05 → 监听 SettlementRequested（强一致正向，FB1）
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        log.warning(
            "u05_finance_module_not_found_skipping_listener_registration. "
            "SettlementRequested events will fail with MissingRequiredHandlerError."
        )
        sentry_sdk.add_breadcrumb(message="U05 finance module not found", level="warning")
        return
    
    try:
        register_finance()
    except Exception as exc:
        log.exception("listener_registration_failed", extra={"module": "finance"})
        raise RuntimeError(
            "U05 finance listener registration failed, refusing to start"
        ) from exc
    
    # 2. U04 → 监听 SettlementPaid（通知类反向，FB5）
    try:
        from app.modules.promotion.listeners import register as register_promotion_listeners
    except ModuleNotFoundError:
        # 不阻塞，因 SettlementPaid required_handler=False
        log.warning(
            "u04_promotion_listeners_module_not_found_skipping. "
            "SettlementPaid events will be dropped (acceptable)."
        )
        return
    
    try:
        register_promotion_listeners()
    except Exception as exc:
        log.exception("listener_registration_failed", extra={"module": "promotion"})
        raise RuntimeError(
            "U04 promotion listener registration failed, refusing to start"
        ) from exc
```

### 5.3 部署一致性约束（继承 U04 FB10 + U05 扩展）

| 层 | 防护 | 状态 |
|---|---|---|
| Migration | 007/008 chain：U05 必须与 U04 同批部署 | 🟡 U06 待补 backfill 008 |
| CI | `validate-event-listeners` job 检查 `from app.modules.finance.listeners import register` 必须存在 | ✅ U04 batch 4 已实施 |
| Smoke | staging deploy 后跑 `test_review_approve_creates_settlement_via_event` 端到端 | 🟡 U05 实施时启用 e2e-smoke-after-deploy |
| Startup | register_event_listeners finance 失败 fail fast；promotion 失败也 fail fast | ✅ U04 batch 4 已搭框架 |
| 文档 | U05/infrastructure-design/deployment-architecture.md 明确"U04 + U05 同批部署"和反向 listener 分级容忍 | 🟡 Infrastructure Design 阶段实施 |

### 5.4 V1+ Reconcile 任务（兜底）

```python
# tasks/finance_reconcile.py（V1 实施）
@celery_app.task(bind=True)
def reconcile_promotion_settlement_status(self):
    """每天凌晨 03:00 同步 settlement 与 promotion 状态.
    
    场景：mark_paid 反向事件失败时，promotion.settlement_status 仍是"待付款"
    （settlement 已是"已付款"）。reconcile 任务发现并补齐。
    """
    # 扫描 settlement_status="已付款" 但对应 promotion.settlement_status="待付款" 的 promotion
    # 批量同步推进到"已付款"
    # audit_log 记录 reconcile 数量（actor_type=system）
    ...
```

### 5.5 监控告警

```python
# Prometheus alerts
- alert: SettlementPaidSyncFailed
  expr: rate(settlement_paid_sync_no_match_total[5m]) > 0.083  # 5/min
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "U04 settlement_paid 反向同步频繁 0 行匹配（reconcile 任务激活前指示器）"
```

### 5.6 测试覆盖
- `test_mark_paid_dispatches_settlement_paid_event`（NFR §12 测试 27）
- `test_settlement_paid_listener_syncs_promotion`（NFR §12 测试 27 端到端）
- `test_settlement_paid_no_listener_no_op`（NFR §12 测试 28）
- `test_settlement_paid_handler_failure_does_not_block_mark_paid`（service 层 try/except 验证）

---

## 6. 复用 U04 8 P1 反馈守护清单（直接继承）

| 反馈 | U04 实施位置 | U05 复用方式 |
|---|---|---|
| **FB1** SettlementRequested 强一致 | `modules/promotion/events.py` SettlementRequested + `core/events.py` MissingRequiredHandlerError | U05 端实现 listener 接收（同事务 + flush） |
| **FB2** 序列号原子 | `repository.next_internal_sequence` INSERT ON CONFLICT | settlement_sequence 完全相同模式 |
| **FB3** 永久 UNIQUE | (本单元首次建立) | UNIQUE(tenant_id, promotion_id) 永久 + DELETE 405 |
| **FB4** attachment 强校验 | (本单元首次建立) | ProofAttachmentValidator 6 项 + 4 层防御 |
| **FB5** audit 失败脱敏 + 兜底 | `service._log_event_dispatch_failure` 脱敏（FB5 完整代码 from U04） | U05 完全复用相同函数签名 |
| **FB6** subscribe 防重复 + flush | `core/events.subscribe` 幂等 + clear_handlers + handler 内 flush | U05 listener 复用 + handler flush 强制 |
| **FB7** 状态机 WHERE 强化 | `repository.update_state` UPDATE WHERE id+tenant_id+is_active+旧 state | settlement.update_state（**无 is_active 字段，FB3**），其他 WHERE 完全一致 |
| **FB8** 日期口径一致 | `urge_calculator.get_today` Asia/Shanghai | daily-summary 复用 get_today() |

---

## 7. 监控与 SLO

### 7.1 SLI（与 NFR Requirements §3.1 一致）

| SLI | SLO 目标 |
|---|---|
| 列表 P95 | ≤ 200ms |
| 详情 P95（含 attachment 签名 URL） | ≤ 150ms |
| 状态推进 P95 | ≤ 200ms |
| mark_paid P95（含 attachment 6 项校验 + 反向事件） | ≤ 300ms |
| daily-summary/as-of P95 | ≤ 100ms |
| daily-summary/activity P95 | ≤ 300ms |
| 事件 handler 增量延迟 P95 | ≤ 50ms |

### 7.2 自定义 Prometheus 指标（5 个，已在 NFR Requirements §10.1 定义）

```python
# 已在 core/metrics.py 追加：
settlement_state_transitions_total: Counter
settlement_created_via_event_total: Counter        # 含 created/duplicate_skipped/error 三个 result
settlement_sequence_lock_duration_seconds: Histogram
attachment_validation_failures_total: Counter      # 含 6 类 failure_type
settlement_paid_sync_no_match_total: Counter
```

### 7.3 告警阈值（与 NFR Requirements §10.3 一致）

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/settlements.*"}) > 0.5` 持续 5min | Prometheus alertmanager | SRE |
| `rate(settlement_created_via_event_total{result="error"}[5m]) > 0.01` | Sentry → 即时 | 后端 leader（关键 — FB1 强一致失败） |
| `rate(attachment_validation_failures_total{failure_type="tenant_mismatch"}[5m]) > 0` | Sentry warning | 后端 + 安全 leader（潜在越权） |
| `rate(settlement_paid_sync_no_match_total[5m]) > 5/60` 持续 5min | Sentry warning | 后端 leader（FB5 反向事件丢失） |
| `histogram_quantile(0.95, settlement_sequence_lock_duration_seconds) > 0.5` | Prometheus | SRE |
| `/api/settlements.*` 5xx > 5% 持续 5min | Sentry | 后端 |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 完全继承 U01-U04 通用模式 + 4 个 U04 模式（除 partial UNIQUE） | ✅ |
| **P-U05-01 财务记录永久不可替换** + Router 405 + 零级联 + audit 留痕 | ✅ |
| **P-U05-02 ProofAttachmentValidator 6 项** + 4 层跨租户防御 | ✅ |
| **P-U05-03 双口径汇总** 独立 endpoint + repository 内嵌 SQL + V1 三层升级 | ✅ |
| **P-U05-04 SettlementPaid 通知类** + 失败处理不对称 + 双向注册 + V1 reconcile | ✅ |
| 8 P1 反馈守护全部继承 U04（无重新评估） | ✅ |
| 监控指标 5 个 + 告警阈值 6 类 | ✅ |
| 部署一致性约束（CI grep + smoke + startup + 文档） | ✅ |
| 跨单元集成测试覆盖 J4 完整旅程 | ✅ |
