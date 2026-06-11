# U05 业务规则（Business Rules）

> 单元：U05 — 财务结款核心  
> 与 domain-entities.md / business-logic-model.md 配合阅读  
> 复用 U02/U03/U04 已建立模式（partial UNIQUE 除外 — FB3 永久 UNIQUE）

---

## 1. settlement_no 生成规则

### BR-U05-01 — settlement_no 格式
- **格式**：`<tenant_prefix>S<yyMMdd><sequence>`（4 位序列号）
- **tenant_prefix**：tenant.code 前 2 位转大写（不足 2 字符补 `X`），与 U04 internal_code 完全一致
- **字面 `S`**：标识 settlement
- **yyMMdd**：取 SettlementRequested 事件触发时的 `requested_at::date`（不是 promotion.cooperation_date）
- **sequence**：4 位 0 填充（0001..9999）
- **示例**：`DES2605260001`
- **唯一约束**：`UNIQUE (tenant_id, settlement_no)` — **永久**（FB3）


### BR-U05-02 — 序列号生成（FB6 + 复用 U04 FB2 模式）

复用 U04 PromotionSequence 完全相同的原子模式：

```sql
INSERT INTO settlement_sequence (id, tenant_id, date_key, last_seq, created_at, updated_at)
VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
ON CONFLICT (tenant_id, date_key) DO UPDATE
SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
RETURNING last_seq;
```

特性：
- 单条 SQL 原子，无 race window
- 首次创建（行不存在）和后续 UPDATE 走同一路径
- 超过 9999 抛 `SequenceOverflowError(409)`

---

## 2. SettlementRequested 事件处理（核心 — FB1 + FB3 + FB6）

### BR-U05-10 — 事件 handler 总流程

按 `modules/finance/listeners.py::on_settlement_requested(event, session)` 实施：

```python
async def on_settlement_requested(
    event: SettlementRequested, session: AsyncSession
) -> None:
    """同事务 handler：失败抛异常导致 U04 端事务回滚."""
    repo = SettlementRepository(session)

    # 1. 三重幂等检查
    #    DB UNIQUE(tenant_id, promotion_id) 永久（FB3）— DB 层兜底
    #    DB UNIQUE(request_event_id) — 事件重放兜底
    #    service SELECT 检查 — 友好错误 + audit 区分
    existing = await repo.find_by_promotion_id(
        tenant_id=event.tenant_id, promotion_id=event.promotion_id
    )
    if existing is not None:
        # 不抛错，写 audit 标记 duplicate_skipped
        await AuditService(session).log(
            action="settlement.create_skipped_duplicate",
            resource="settlement",
            resource_id=existing.id,
            user_id=event.requested_by,
            after={
                "event_id": str(event.event_id),
                "existing_settlement_id": str(existing.id),
            },
        )
        return

    # 2. 序列号原子分配
    seq = await repo.next_settlement_sequence(
        tenant_id=event.tenant_id,
        date_key=event.requested_at.date(),
    )

    # 3. tenant_code 取数 + 格式化 settlement_no
    tenant_code = await repo.get_tenant_code(event.tenant_id)
    settlement_no = format_settlement_no(
        tenant_code=tenant_code,
        date_key=event.requested_at.date(),
        sequence=seq,
    )

    # 4. 创建实体 — settlement_status 起点 = 待核查（FB1）
    settlement = Settlement(
        id=uuid4(),
        tenant_id=event.tenant_id,
        promotion_id=event.promotion_id,
        blogger_id=event.blogger_id,
        style_id=event.style_id,
        pr_id=event.requested_by,  # 注：U04 端 reviewed_by 即 promotion.pr_id 仅当无自审；这里是 promotion 创建者
        settlement_no=settlement_no,
        amount=event.amount,
        total_amount=event.amount,
        settlement_status=SettlementStatus.PENDING_REVIEW.value,  # FB1: 待核查
        request_event_id=event.event_id,
    )
    session.add(settlement)

    # 5. 立即 flush（FB6：UNIQUE / FK 错误立即暴露，不延迟到外层 commit）
    await session.flush()

    # 6. 写 audit
    await AuditService(session).log(
        action="settlement.create_via_event",
        resource="settlement",
        resource_id=settlement.id,
        user_id=event.requested_by,
        after={
            "settlement_no": settlement_no,
            "promotion_id": str(event.promotion_id),
            "amount_changed": True,
            "total_amount_changed": True,
            "settlement_status": SettlementStatus.PENDING_REVIEW.value,
        },
    )

    # 7. 指标
    settlement_created_via_event_total.labels(result="created").inc()
```

### BR-U05-11 — 三重幂等保证

| 层 | 防护 | 错误处理 |
|---|---|---|
| DB UNIQUE(tenant_id, promotion_id) 永久 | 防 race + 防替换（FB3） | IntegrityError → service 层捕获再 SELECT → 转为 duplicate_skipped 路径 |
| DB UNIQUE(request_event_id) 永久 | 事件重放兜底 | 同上 |
| service 层 SELECT | 友好错误 + audit 区分 | 已存在则 no-op + 写 audit `settlement.create_skipped_duplicate` |

幂等场景：
- 同一 SettlementRequested.event_id 重复投递 → DB UNIQUE(request_event_id) 阻止 + service 层 SELECT 检测 → no-op
- 不同 event_id 但同 promotion_id（不可能，但兜底）→ DB UNIQUE(promotion_id) 阻止

### BR-U05-12 — 同事务执行 + flush（FB6）

- handler 在 U04 service.review approve 的 session 中执行（同事务）
- handler 末尾 `await session.flush()` → UNIQUE / FK 错误在 dispatch 阶段就暴露
- 不调用 `session.commit()` — 由 U04 service 统一 commit
- 失败抛异常 → 自然冒泡 → U04 端 `_log_event_dispatch_failure` + 整个事务回滚（FB1 强一致）

---

## 3. 引用完整性

### BR-U05-13 — 必填字段（创建时由 SettlementRequested handler 填入）

- `promotion_id`（外键校验存在 + 永久 UNIQUE）
- `blogger_id`（来自事件，已校验过）
- `style_id`（来自事件）
- `amount` / `total_amount`（事件 amount 复制）
- `settlement_no`（序列号生成）
- `request_event_id`（事件 event_id）
- `settlement_status` = "待核查"（FB1）
- `pr_id`（来自事件 requested_by — 注意是审核人，不是原 PR；MVP 无原 PR 字段引用）

### BR-U05-14 — 引用完整性（创建时已由 U04 端校验，但仍需 FK 兜底）

- `promotion_id` 必须存在（U04 端已确保）
- `blogger_id` / `style_id` 必须存在
- 缺失（理论不可能，FK 兜底）→ IntegrityError → 事务回滚

---

## 4. 状态机规则（5 状态 — FB1 修正）

### BR-U05-20 — settlement_status 转移表

| from | event | to | 触发者 | 校验 |
|---|---|---|---|---|
| 待核查 | approve | 待付款 | PR 主管 | reviewer != promotion.pr_id（不能自审）|
| 待核查 | reject | 已驳回 | PR 主管 | review_reason 必填 |
| 待付款 | reject | 已驳回 | PR 主管 | review_reason 必填（增加 extra_item 后发现问题） |
| 待付款 | fill_payment | 待财务付款 | PR 主管 | payment_amount 必填 + > 0 |
| 待财务付款 | mark_paid | 已付款 | 财务 | payment_date 必填 + ≤ today + payment_proof_attachment_id 必填 |
| 已驳回 | resubmit | 待核查 | PR 主管 / system | （PR 修改 promotion 后重新提交） |

非法转移 → `IllegalStateTransitionError(422)` + 错误详情包含 `from / to / action`。

并发安全（复用 U04 FB7 模式）：
- service 层 UPDATE WHERE 含 `tenant_id` + 旧 `settlement_status`
- 0 行匹配 → `StateTransitionConflictError(409)`

### BR-U05-21 — 跨实体校验

- `approve` 前不要求 promotion 端状态（U04 已保证 review approve 时 publish_status="已发布"）
- `mark_paid` 前要求 settlement.payment_amount IS NOT NULL（fill_payment 已写入）

### BR-U05-22 — 自审禁止（与 U04 一致）

- approve 时 reviewer.id != promotion.pr_id（注意 promotion.pr_id 是 promotion 创建者，不是 settlement 创建者）
- 即 settlement.reviewed_by 必须 != promotion.pr_id
- 违反 → `SelfReviewForbiddenError(403)`

---

## 5. 付款字段约束（FB4）

### BR-U05-30 — fill_payment 校验（PR 主管动作）

- settlement_status="待付款"
- payment_amount 必填 + > 0 + DECIMAL(12,2)
- payment_amount 可与 total_amount 不同（抹零、汇率）— 不强制相等
- 无需 payment_proof（截图由财务后续上传）

### BR-U05-31 — mark_paid 校验（财务动作）

- settlement_status="待财务付款"
- payment_date 必填 + ≤ today（Asia/Shanghai 时区，复用 `urge_calculator.get_today`）
- **payment_proof_attachment_id 必填**（FB4）
- attachment 6 项强校验（详见 BR-U05-50）
- 写入 settlement.paid_by = current_user.id
- 推进状态后发 `SettlementPaid` 事件（通知类，不阻塞）

### BR-U05-32 — payment 字段缺失返回 422

按 EP06-S07 验收：
- payment_amount / payment_date / payment_proof_attachment_id 任一缺失 → 422 + 错误码 `PAYMENT_FIELD_MISSING` + 写 data_quality_issue (error 级)

---

## 6. SettlementExtraItem 业务规则

### BR-U05-40 — 新增 extra_item 校验

- settlement_status="待付款"（其他状态拒绝，BR-U05-31）
- item_type IN ("运费", "赞奖", "其他")
- amount > 0
- 同事务更新 `settlement.total_amount = settlement.amount + SUM(extra_items.amount)`

### BR-U05-41 — 修改 / 删除 extra_item

- 仅 settlement_status="待付款" 时允许
- 修改 amount 后同事务更新 `settlement.total_amount`
- 删除走硬删除（`DELETE FROM settlement_extra_item`），不软删（极少场景，且 ON DELETE CASCADE 不会触发，因 settlement 不删）

### BR-U05-42 — 权限要求

- 增 / 删 / 改 extra_item 仅 admin / pr_manager 角色（PR / finance 不允许）
- 与 settlement 主表写权限一致

---

## 7. 字段级权限（FB4 + 复用 U02/U03/U04 模式）

### BR-U05-50 — 角色矩阵（U09 后清理）

```python
# modules/finance/legacy_field_permissions.py
PAYMENT_VISIBLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager", "finance"
})
"""可见 payment_amount / payment_date / payment_proof / extra_item.amount / amount / total_amount 的角色."""

PAYMENT_WRITABLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager"  # finance 可见但不可写 payment_amount
})
"""可写 payment_amount 的角色."""

PROOF_UPLOAD_ROLES: frozenset[str] = frozenset({
    "admin", "finance"  # PR 主管不能上传付款截图
})
"""可上传付款截图的角色."""
```

完整权限矩阵：

| 角色 | settlement read | review approve/reject | fill_payment_amount | upload_proof | view_payment | add_extra_item |
|---|---|---|---|---|---|---|
| admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| finance | ✅（限 payment 视图） | ❌ | ❌ | ✅ | ✅ | ❌ |
| pr | ✅（限 promotion.pr_id=自己 + 只读） | ❌ | ❌ | ❌ | ❌ | ❌ |
| 其他 | ❌ | — | — | — | — | — |

### BR-U05-51 — 字段读过滤（service.to_response）

不在 `PAYMENT_VISIBLE_ROLES` 的角色 → 响应字段 `amount` / `total_amount` / `payment_amount` / `extra_items[].amount` / `payment_proof_signed_url` 全部置 None。
其他字段（settlement_no / settlement_status / created_at 等）正常返回。

PR 角色额外限制：
- list endpoint 自动加 `WHERE promotion.pr_id = current_user.id`（service 层注入）
- 不可见非自己提交的 settlement

### BR-U05-52 — 字段写权限抛错时机

- POST `/api/settlements/{id}/payment-amount` — service 入口校验 PAYMENT_WRITABLE_ROLES → 失败抛 `FieldPermissionDenied(field="payment_amount", 403)`
- POST `/api/settlements/{id}/payment-proof` — service 入口校验 PROOF_UPLOAD_ROLES → 失败抛 `FieldPermissionDenied(field="payment_proof_attachment_id", 403)`
- POST `/api/settlements/{id}/extra-items` — service 入口校验 EXTRA_ITEM_WRITABLE_ROLES（即 admin / pr_manager） → 失败抛 `FieldPermissionDenied`

> TODO U09: 改为基于 `Permission.field_writable()` 的统一异常。

---

## 8. 付款截图（FB4：通过 attachment 表引用）

### BR-U05-60 — attachment 6 项强校验（FB4）

service 在 mark_paid / upload_payment_proof 时通过 `AttachmentService.get_by_id(attachment_id)` 取出 attachment，校验：

| # | 校验项 | 失败错误 |
|---|---|---|
| 1 | `attachment.tenant_id == current_user.tenant_id` | InvalidAttachmentReferenceError + 跨租户告警 |
| 2 | `attachment.bucket == "private"` | InvalidAttachmentBucket |
| 3 | `attachment.purpose == "settlement_proof"` | InvalidAttachmentPurpose |
| 4 | `attachment.mime_type IN ("image/jpeg", "image/png", "image/webp", "application/pdf")` | InvalidAttachmentMime |
| 5 | `attachment.size_bytes <= 10 * 1024 * 1024` | AttachmentTooLarge |
| 6 | `attachment.status == "ready"` | AttachmentNotReady |

任一失败 → 422 + 不更新 settlement。

### BR-U05-61 — 付款截图读取（签名 URL）

- 后续读取通过 `AttachmentService.get_signed_url(attachment_id, ttl=900)` 返回签名 URL（15min）
- 在 SettlementResponse 序列化时按角色权限决定是否暴露：
  - `PAYMENT_VISIBLE_ROLES` 角色 → 返回签名 URL
  - 其他角色 → 字段值为 None

### BR-U05-62 — 付款截图替换 / 删除

**MVP 完全不支持替换 / 删除**（FB3 + FB4 一致）：
- payment_proof_attachment_id 一旦写入就不可修改
- 错误付款修正 → V2 通过 order_adjustment 调整单实现
- attachment 表本身的 GC 策略需排除"已被 settlement 引用"的 attachment（V1 attachment 引用计数实施）

### BR-U05-63 — 上传链路

详见 business-logic-model UC-5（financial 上传付款截图）。

---

## 9. 当日汇总（FB7 双口径）

### BR-U05-70 — 口径 A：activity（当天发生的动作）

`GET /api/settlements/daily-summary/activity?date=2026-05-26`

含义：当天发生了哪些 settlement 状态推进动作。

聚合维度：
- 当天 created（新建）的 settlement
- 当天 reviewed（approve / reject）的 settlement
- 当天 fill_payment（推进到待财务付款）的 settlement
- 当天 paid（payment_date == :date）的 settlement

实现策略：
- 扫描 audit_log + settlement 表交叉查询
- 默认 date 取 today（Asia/Shanghai 时区，复用 `urge_calculator.get_today`）

返回结构（详见 BR-U05-71）。

### BR-U05-71 — 口径 B：as_of（截至当日的快照）

`GET /api/settlements/daily-summary/as-of?date=2026-05-26`

含义：截至 :date 末，各状态的 settlement 还有多少未处理。

聚合维度：
- GROUP BY settlement_status
- WHERE created_at <= :date 23:59 + tenant_id = :tenant_id

返回结构：

```json
{
  "kind": "as_of",
  "date": "2026-05-26",
  "as_of": {
    "pending_review": { "count": 12, "total_amount": "12000.00" },
    "pending_payment": { "count": 8, "total_amount": "8500.00" },
    "pending_finance": { "count": 5, "total_amount": "5300.00" },
    "paid": { "count": 80, "total_amount": "84000.00" },
    "rejected": { "count": 7, "total_amount": "5500.00" }
  },
  "outstanding_total": { "count": 25, "total_amount": "25800.00" }
}
```

`outstanding_total` = pending_review + pending_payment + pending_finance（不含 rejected / paid）。

### BR-U05-72 — 双口径 endpoint 实现

- 两个独立 endpoint 而非合并参数化：避免混合语义误导用户
- 前端默认调 as_of（用户最常关心"还差多少没付"）
- activity 用于专项报表查询 / 月底对账

### BR-U05-73 — 时区与日期边界（FB8 风格 + 与 U04 一致）

- 默认 :date = `get_today()`（Asia/Shanghai）
- SQL 不用 CURRENT_DATE，统一传 `:today` 参数
- as_of 的"截至当日 23:59:59"通过 `:date + INTERVAL '1 day'` 排他比较

---

## 10. 错误码矩阵

| 错误类 | code | HTTP | 抛出场景 |
|---|---|---|---|
| `SettlementNotFoundError` | `SETTLEMENT_NOT_FOUND` | 404 | 不存在 |
| `SettlementNoConflictError` | `SETTLEMENT_NO_CONFLICT` | 409 | 序列号冲突（理论不应触发） |
| `SequenceOverflowError` | `SETTLEMENT_SEQUENCE_OVERFLOW` | 500 | 当天序号 > 9999 |
| `IllegalStateTransitionError` | `ILLEGAL_STATE_TRANSITION` | 422 | 状态机非法转移 |
| `StateTransitionConflictError` | `SETTLEMENT_STATE_CONFLICT` | 409 | 乐观并发冲突（FB7 模式） |
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
| `FieldPermissionDenied` | `FIELD_PERMISSION_DENIED` | 403 | 字段写权限拒绝（payment_amount / proof / extra_item） |
| `MethodNotAllowedError` | `METHOD_NOT_ALLOWED` | 405 | DELETE /api/settlements/{id}（FB3 财务记录不可删） |

---

## 11. SettlementPaid 反向事件契约（FB5）

### BR-U05-80 — 触发时机
仅 `mark_paid` 动作（settlement_status: 待财务付款 → 已付款）后发出。其他状态推进**不**发反向事件。

### BR-U05-81 — 事件分发

```python
# modules/finance/service.py mark_paid 末尾
await event_bus.dispatch(
    SettlementPaid(
        event_id=uuid4(),
        timestamp=now,
        tenant_id=settlement.tenant_id,
        settlement_id=settlement.id,
        promotion_id=settlement.promotion_id,
        payment_amount=settlement.payment_amount,
        payment_date=settlement.payment_date,
        paid_by=user.id,
    ),
    session=self._session,
)
# required_handler=False → 即使 U04 listener 缺失也不阻塞
# 失败仅记 audit + Sentry breadcrumb（FB5 通知类）
```

### BR-U05-82 — U04 端 listener 实施

`modules/promotion/listeners.py`（U05 实施时新建）：

```python
async def on_settlement_paid(event: SettlementPaid, session: AsyncSession) -> None:
    """U05 → U04 反向同步：同步 promotion.settlement_status='已付款'."""
    repo = PromotionRepository(session)
    updated = await repo.update_state(
        promotion_id=event.promotion_id,
        tenant_id=event.tenant_id,
        from_state_field="settlement_status",
        from_state_value=SettlementStatus.PENDING_PAYMENT.value,  # U04 端的"待付款"
        to_state_value=SettlementStatus.PAID.value,
    )
    if updated is None:
        # 已被推进或已不存在；不抛错（FB5 通知类）
        log.warning("settlement_paid_sync_no_match", extra={
            "promotion_id": str(event.promotion_id),
        })
    else:
        log.info("settlement_paid_synced", extra={
            "promotion_id": str(event.promotion_id),
        })


def register() -> None:
    from app.core.events import subscribe
    subscribe("SettlementPaid", on_settlement_paid)
```

### BR-U05-83 — main.py register_event_listeners 注册

在 U04 已落地的 `register_event_listeners` 函数中追加 U04 端 listener 注册：

```python
def register_event_listeners() -> None:
    clear_handlers()

    # U05 finance listener（FB1 强一致）
    try:
        from app.modules.finance.listeners import register as register_finance
    except ModuleNotFoundError:
        log.warning("u05_finance_module_not_found_skipping_listener_registration. ...")
        return
    register_finance()

    # U04 promotion 反向 listener（FB5 通知类）
    try:
        from app.modules.promotion.listeners import register as register_promotion_listeners
    except ModuleNotFoundError:
        log.warning("u04_promotion_listeners_module_not_found_skipping. SettlementPaid events will be dropped.")
        return
    register_promotion_listeners()
```

---

## 12. 删除策略（FB3）

### BR-U05-90 — DELETE /api/settlements/{id} 返回 405

- MVP 不提供删除接口
- 服务端 router 直接返回 `405 Method Not Allowed`
- 错误付款修正 → V2 通过 order_adjustment 调整单实现

### BR-U05-91 — promotion 软删时不级联

- 当 U04 promotion.is_active=false（soft_deactivate） → 不级联软删 settlement
- settlement 仍然有效，可以继续走完结算流程
- 财务记录保留完整审计追溯

### BR-U05-92 — 极少场景手动修正

- 若发现 settlement 数据严重错误（如 amount 写错、关联错 promotion）：
  1. admin 通过手动 SQL（必写 audit + Sentry 告警）
  2. 不通过 service 接口
  3. V1 评估是否提供"作废 settlement"专门接口（保留行 + 标记 voided 状态）

---

## 13. 性能 / 并发约束

### BR-U05-100 — 单事件处理性能

- 单租户单日 SettlementRequested 事件估算 ≤ 100 个（与 U04 review approve 同频次）
- handler 同事务执行（FB1）
- INSERT settlement + INSERT settlement_sequence ON CONFLICT 原子操作 → 单事件 P95 ≤ 50ms 增量
- 不影响 U04 review approve P95 ≤ 500ms 的总 SLA

### BR-U05-101 — 状态推进并发安全

- 复用 U04 FB7 模式：UPDATE WHERE id + tenant_id + 旧 settlement_status RETURNING
- 0 行 → StateTransitionConflictError(409)
- 100 并发 mark_paid 同 settlement → 1 成功 99 冲突

### BR-U05-102 — 列表查询性能

- 列表 P95 ≤ 200ms（10K settlement 单租户）
- 关键路径走 `idx_settlement_tenant_status` + `idx_settlement_payment_date` + `idx_settlement_no_trgm`
- 不需要 CTE 衍生字段（与 U04 不同）

---

## 14. 一致性校验

| 校验 | 结果 |
|---|---|
| settlement_no 永久 UNIQUE per tenant（FB3） | ✅ |
| promotion_id 永久 UNIQUE per tenant（FB3） | ✅ |
| 不设 is_active 字段（FB3） | ✅ |
| settlement_status 起点 = 待核查（FB1） | ✅ |
| handler flush 立即暴露错误（FB6） | ✅ |
| 三重幂等（DB UNIQUE × 2 + service SELECT） | ✅ |
| 自审禁止与 U04 一致 | ✅ |
| 双口径汇总 endpoint（FB7） | ✅ |
| attachment 6 项强校验（FB4） | ✅ |
| 付款截图通过 attachment 表（FB4） | ✅ |
| 反向事件仅 SettlementPaid（FB5） | ✅ |
| FB7 状态机 WHERE 模式（tenant_id + 旧状态） | ✅ |
| 错误码与 U04 风格一致 | ✅ |
| audit 字段白名单 + 敏感值脱敏（amount / total_amount / payment_amount） | ✅ |
| MVP 不提供 DELETE 接口（FB3） | ✅ |
| 不级联 promotion 软删（FB3） | ✅ |
