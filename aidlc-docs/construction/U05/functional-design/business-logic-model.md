# U05 业务逻辑模型（Business Logic Model）

> 单元：U05 — 财务结款核心  
> 与 domain-entities.md / business-rules.md 配合阅读  
> 包含 8 个用例的端到端流程

---

## UC 清单

| # | 用例 | 端点 / 触发 | 核心步骤 |
|---|---|---|---|
| 1 | 监听 SettlementRequested → 创建 settlement | event_bus（U04 review approve 同事务） | 三重幂等检查 → 序列号原子 → 创建（"待核查"） → flush → audit |
| 2 | PR 主管 approve / reject settlement | PUT `/api/settlements/{id}/review` | 状态机 + UPDATE WHERE 旧状态 + audit |
| 3 | PR 主管增加结算项 | POST `/api/settlements/{id}/extra-items` | 状态校验（仅"待付款"） + 维护 total_amount |
| 4 | PR 主管填写付款金额 | PUT `/api/settlements/{id}/payment-amount` | 状态机 → "待财务付款" |
| 5 | 财务上传付款截图 | PUT `/api/settlements/{id}/payment-proof` | attachment 6 项强校验 → 状态机 → "已付款" → 发 SettlementPaid 事件 |
| 6 | 当日活动汇总 | GET `/api/settlements/daily-summary/activity` | 扫描 audit_log + settlement 交叉聚合 |
| 7 | 截至当日快照汇总 | GET `/api/settlements/daily-summary/as-of` | GROUP BY settlement_status |
| 8 | 列表查询 | GET `/api/settlements/` | 多筛选 + 分页 + 字段权限过滤 |

---

## UC-1：监听 SettlementRequested → 创建 settlement

### 触发
U04 端 `service.review` action="approve" 同事务调用 `event_bus.dispatch(SettlementRequested(...), session=session)`。

### 流程

```
[U04 service.review approve]
  │
  ├─ assert_can_transition(待核查 → 待付款, action='approve')
  ├─ repo.update_state(promotion.settlement_status: 待核查 → 待付款) [UPDATE WHERE FB7]
  ├─ promotion_state_transitions_total++
  ├─ AuditService.log("promotion.review.approve", ...)
  │
  └─ event_bus.dispatch(SettlementRequested, session=self._session)  ← 同事务
        │
        ├─ [核心 listener: modules/finance/listeners.py::on_settlement_requested]
        │
        ├─ 1. 三重幂等检查
        │      a. SELECT settlement WHERE tenant_id=:t AND promotion_id=:p
        │      b. 已存在 → 写 audit("settlement.create_skipped_duplicate") → return（不抛错）
        │
        ├─ 2. SELECT tenant.code 取 tenant_prefix
        │
        ├─ 3. INSERT settlement_sequence ON CONFLICT DO UPDATE RETURNING last_seq [FB2 复用 U04]
        │      └─ 单事件 P95 ≤ 50ms 增量
        │
        ├─ 4. format_settlement_no("DE", date(2026,5,26), seq=1) → "DES2605260001"
        │
        ├─ 5. 创建 Settlement 实体（settlement_status="待核查", FB1）
        │      session.add(settlement)
        │
        ├─ 6. await session.flush() [FB6 立即暴露 UNIQUE / FK 错误]
        │
        ├─ 7. AuditService.log("settlement.create_via_event", resource_id=settlement.id, ...)
        │
        ├─ 8. settlement_created_via_event_total.labels(result='created').inc()
        │
        └─ return（不抛错；handler 成功）

  [U04 service 继续]
  ├─ event_bus.dispatch 返回 → 无异常
  ├─ session.commit() ← 同事务原子提交（promotion 推进 + settlement 创建）
  └─ return promotion response
```

### 异常路径

#### EP-1.1：handler 抛异常
- 任意环节抛异常（如 IntegrityError 重复 promotion_id）
- 异常自然冒泡 → U04 service 捕获 → `_log_event_dispatch_failure` 脱敏 audit（独立 bypass session）+ 重新 raise
- session 回滚 → promotion 推进 + settlement 创建全部回滚（FB1 强一致）

#### EP-1.2：U05 模块未部署
- main.py register_event_listeners 启动时已发现 ModuleNotFoundError → 仅 warning，未注册 handler
- U04 dispatch SettlementRequested → handlers list 为空 + required_handler=True
- → MissingRequiredHandlerError(500) 抛出 → review approve 失败 → 事务回滚（FB1 强一致优先）

#### EP-1.3：重复事件投递
- 同一 event_id 重投 → service 层 SELECT 检测 → no-op 返回 + audit `create_skipped_duplicate`
- 不抛错，U04 端 review approve 可"幂等成功"（适合 Celery 重试场景）

### 验收
- EP06-S02.given1：审核通过 → settlement 创建（amount = promotion.quote_amount，settlement_status="待核查"）
- EP06-S02.given2：同 promotion 再次触发 → 不重复创建（按 promotion_id + event_id 双重幂等）

---

## UC-2：PR 主管 approve / reject settlement

### 端点
PUT `/api/settlements/{id}/review`

### 入参
```json
{
  "action": "approve" | "reject",
  "review_reason": "..."   // reject 时必填
}
```

### 流程（approve）

```
[POST /api/settlements/{id}/review]
  │
  ├─ Auth: require_permission("settlement", "review")
  ├─ Service.review(settlement_id, payload, user)
  │
  ├─ 1. SELECT settlement WHERE id + tenant_id (RLS + ORM 钩子)
  │      └─ 不存在 → 404 SETTLEMENT_NOT_FOUND
  │
  ├─ 2. 自审禁止校验（BR-U05-22）
  │      reviewer.id != settlement.pr_id（即 promotion 创建者）
  │      → 失败抛 SelfReviewForbiddenError(403)
  │
  ├─ 3. assert_can_transition(待核查 → 待付款, action='approve')
  │      → 失败抛 IllegalStateTransitionError(422)
  │
  ├─ 4. UPDATE WHERE 旧状态 RETURNING（复用 U04 FB7 模式）
  │      UPDATE settlement
  │         SET settlement_status='待付款',
  │             reviewed_by=:user_id,
  │             reviewed_at=NOW(),
  │             review_action='approve',
  │             updated_at=NOW()
  │      WHERE id=:sid
  │        AND tenant_id=:tid
  │        AND settlement_status='待核查'
  │      RETURNING *;
  │      └─ 0 行 → StateTransitionConflictError(409)
  │
  ├─ 5. settlement_state_transitions_total.labels(待核查 → 待付款, "approve").inc()
  │
  ├─ 6. AuditService.log("settlement.review.approve", resource_id, after={review_action: 'approve', ...})
  │
  ├─ 7. session.commit()
  │
  └─ return SettlementResponse
```

### 流程（reject）

类似 approve，但：
- 校验 review_reason（不为空）→ 失败 422 REVIEW_REASON_REQUIRED
- to_state="已驳回"
- UPDATE WHERE settlement_status IN ("待核查", "待付款")（reject 可从两个状态发起）

### 验收
- EP06-S03 待核查 + approve → 待付款
- EP06-S04 待核查 / 待付款 + reject + reason → 已驳回
- 自审禁止：reviewer == promotion.pr_id → 403

---

## UC-3：PR 主管增加结算项

### 端点
POST `/api/settlements/{id}/extra-items`

### 入参
```json
{
  "item_type": "运费" | "赞奖" | "其他",
  "amount": "20.00",
  "remark": "顺丰邮费"
}
```

### 流程

```
[POST /api/settlements/{id}/extra-items]
  │
  ├─ Auth: require_permission("settlement", "write") + 字段权限 EXTRA_ITEM_WRITABLE_ROLES
  │       (admin / pr_manager only) → 失败抛 FieldPermissionDenied(403)
  │
  ├─ 1. SELECT settlement
  │      不存在 → 404
  │
  ├─ 2. 校验 settlement_status="待付款"
  │      → 失败抛 ExtraItemNotAllowedError(422)
  │
  ├─ 3. INSERT SettlementExtraItem
  │      session.add(extra_item)
  │
  ├─ 4. 重算 settlement.total_amount（service 层）
  │      total = settlement.amount + (SELECT SUM(amount) FROM settlement_extra_item WHERE settlement_id=...)
  │      UPDATE settlement SET total_amount=:total, updated_at=NOW() WHERE id=...
  │
  ├─ 5. session.flush()
  │
  ├─ 6. AuditService.log("settlement.add_extra_item", resource_id=settlement.id, after={
  │        "item_type": ..., "total_amount_changed": True
  │      })
  │
  └─ return SettlementResponse（含 updated extra_items）
```

### 验收
- EP06-S05.given1：写入 extra_item + total 重算
- EP06-S05.given2：非 PR 主管 → 403

---

## UC-4：PR 主管填写付款金额

### 端点
PUT `/api/settlements/{id}/payment-amount`

### 入参
```json
{
  "payment_amount": "1080.00"
}
```

### 流程

```
[PUT /api/settlements/{id}/payment-amount]
  │
  ├─ Auth: require_permission("settlement", "write") + 字段 PAYMENT_WRITABLE_ROLES
  │
  ├─ 1. SELECT settlement → 不存在 404
  │
  ├─ 2. 校验 payment_amount > 0 + DECIMAL(12,2)（Pydantic 已校验，service 二次防线）
  │      → 失败抛 PaymentAmountRequiredError(422)
  │
  ├─ 3. assert_can_transition(待付款 → 待财务付款, action='fill_payment')
  │
  ├─ 4. UPDATE WHERE 旧状态（FB7）
  │      UPDATE settlement
  │         SET settlement_status='待财务付款',
  │             payment_amount=:amount,
  │             updated_at=NOW()
  │      WHERE id=:sid AND tenant_id=:tid AND settlement_status='待付款'
  │      RETURNING *;
  │      └─ 0 行 → StateTransitionConflictError(409)
  │
  ├─ 5. settlement_state_transitions_total++
  │
  ├─ 6. AuditService.log("settlement.fill_payment", after={
  │        "payment_amount_changed": True,
  │        "settlement_status": "待财务付款"
  │      })
  │
  ├─ 7. session.commit()
  │
  └─ return SettlementResponse
```

### 验收
- EP06-S06：待付款 + payment_amount → 待财务付款，通知财务（V1 加企微通知）

---

## UC-5：财务上传付款截图（FB4 attachment 强校验）

### 端点
PUT `/api/settlements/{id}/payment-proof`

### 入参
```json
{
  "payment_date": "2026-05-26",
  "payment_proof_attachment_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 流程

```
[POST /api/attachments/upload]  （前置：U01 attachment 框架）
  ├─ 财务前端选择文件
  ├─ 后端创建 attachment 行（status='uploading', purpose='settlement_proof', bucket='private', tenant_id=current）
  ├─ 返回 attachment_id + 临时上传 URL
  ├─ 前端直传 R2 private bucket
  └─ 前端调 POST /api/attachments/{id}/complete → status='ready'
↓
[PUT /api/settlements/{id}/payment-proof]
  │
  ├─ Auth: require_permission("settlement", "pay") + 字段 PROOF_UPLOAD_ROLES
  │       (admin / finance only) → 失败抛 FieldPermissionDenied(403)
  │
  ├─ 1. SELECT settlement → 不存在 404
  │
  ├─ 2. attachment 6 项强校验（FB4 BR-U05-60）
  │      attachment = AttachmentService(session).get_by_id(payment_proof_attachment_id)
  │      a. attachment.tenant_id == user.tenant_id  → 否 InvalidAttachmentReferenceError + 跨租户告警
  │      b. attachment.bucket == 'private'           → 否 InvalidAttachmentBucketError
  │      c. attachment.purpose == 'settlement_proof' → 否 InvalidAttachmentPurposeError
  │      d. attachment.mime_type IN allowed          → 否 InvalidAttachmentMimeError
  │      e. attachment.size_bytes <= 10MB            → 否 AttachmentTooLargeError
  │      f. attachment.status == 'ready'             → 否 AttachmentNotReadyError
  │
  ├─ 3. 校验 payment_date <= today (Asia/Shanghai)
  │      → 失败抛 PaymentFieldMissingError(422)
  │
  ├─ 4. assert_can_transition(待财务付款 → 已付款, action='mark_paid')
  │
  ├─ 5. UPDATE WHERE 旧状态（FB7）
  │      UPDATE settlement
  │         SET settlement_status='已付款',
  │             payment_date=:pd,
  │             payment_proof_attachment_id=:aid,
  │             paid_by=:user_id,
  │             updated_at=NOW()
  │      WHERE id=:sid AND tenant_id=:tid AND settlement_status='待财务付款'
  │      RETURNING *;
  │      └─ 0 行 → StateTransitionConflictError(409)
  │
  ├─ 6. settlement_state_transitions_total++
  │
  ├─ 7. AuditService.log("settlement.mark_paid", after={
  │        "payment_date": "2026-05-26",
  │        "payment_amount_changed": True,
  │        "attachment_id_changed": True,
  │        "settlement_status": "已付款"
  │      })
  │
  ├─ 8. event_bus.dispatch(SettlementPaid(...), session=self._session) [FB5]
  │      └─ U04 端 listener 监听 → UPDATE promotion.settlement_status='已付款'（通知类，失败不抛）
  │
  ├─ 9. session.commit()
  │
  └─ return SettlementResponse（含签名 URL 字段）
```

### 异常路径

#### EP-5.1：缺失字段
- payment_date 或 attachment_id 任一缺失 → 422 PAYMENT_FIELD_MISSING + 写 data_quality_issue (error)

#### EP-5.2：已付款重复上传
- settlement_status="已付款" → UPDATE WHERE 0 行 → StateTransitionConflictError(409) "已付款不可重复"

#### EP-5.3：attachment 跨租户
- attachment.tenant_id != user.tenant_id → InvalidAttachmentReferenceError + 立即 Sentry 告警（潜在越权）

### 验收
- EP06-S07.given1：截图存 R2 private + settlement_status="已付款"
- EP06-S07.given2：付款金额 / 日期 / 截图任一缺失 → 422
- EP06-S07.given3：已付款重复上传 → 422

---

## UC-6：当日活动汇总（FB7 口径 A）

### 端点
GET `/api/settlements/daily-summary/activity?date=2026-05-26`

### 流程

```
[GET /api/settlements/daily-summary/activity?date=...]
  │
  ├─ Auth: require_permission("settlement", "read")
  │
  ├─ 1. date = query 或 default = get_today() (Asia/Shanghai)
  │
  ├─ 2. SQL 多 CTE 聚合
  │      WITH
  │        newly_created AS (
  │          SELECT COUNT(*) AS cnt, SUM(total_amount) AS sum_amt
  │          FROM settlement
  │          WHERE tenant_id = :tid
  │            AND created_at >= :date AND created_at < :date + INTERVAL '1 day'
  │        ),
  │        newly_paid AS (
  │          SELECT COUNT(*) AS cnt, SUM(total_amount) AS sum_amt
  │          FROM settlement
  │          WHERE tenant_id = :tid
  │            AND payment_date = :date
  │        ),
  │        newly_approved AS (
  │          SELECT COUNT(DISTINCT al.resource_id) AS cnt,
  │                 SUM(s.total_amount) AS sum_amt
  │          FROM audit_log al
  │          JOIN settlement s ON s.id::text = al.resource_id
  │          WHERE al.tenant_id = :tid
  │            AND al.action = 'settlement.review.approve'
  │            AND al.created_at >= :date AND al.created_at < :date + INTERVAL '1 day'
  │        ),
  │        newly_rejected AS (
  │          ... 类似 ...
  │        )
  │      SELECT * FROM newly_created, newly_approved, newly_paid, newly_rejected;
  │
  └─ return JSON {kind: "activity", date, activity: {...}}
```

### 性能注意
- 关键索引：`idx_settlement_payment_date` + `idx_settlement_tenant_status` + audit_log.created_at
- P95 ≤ 200ms（10K settlement 单租户）

### 验收
- EP06-S08.given1（部分）：当日动作计数与金额聚合正确

---

## UC-7：截至当日快照汇总（FB7 口径 B）

### 端点
GET `/api/settlements/daily-summary/as-of?date=2026-05-26`

### 流程

```
[GET /api/settlements/daily-summary/as-of?date=...]
  │
  ├─ Auth: require_permission("settlement", "read")
  │
  ├─ 1. date = query 或 default = get_today() (Asia/Shanghai)
  │
  ├─ 2. SQL GROUP BY 聚合
  │      SELECT
  │        settlement_status,
  │        COUNT(*) AS cnt,
  │        SUM(total_amount) AS sum_amt
  │      FROM settlement
  │      WHERE tenant_id = :tid
  │        AND created_at < :date + INTERVAL '1 day'   -- 截至当日 23:59:59
  │      GROUP BY settlement_status;
  │
  ├─ 3. 计算 outstanding_total = pending_review + pending_payment + pending_finance
  │
  └─ return JSON {kind: "as_of", date, as_of: {...}, outstanding_total: {...}}
```

### 性能注意
- 走 `idx_settlement_tenant_status`
- P95 ≤ 100ms（GROUP BY 简单查询）

### 验收
- EP06-S08.given1：截至当日各状态 count + total_amount 聚合正确
- 前端默认调此 endpoint（用户最常关心"还差多少没付"）

---

## UC-8：列表查询（多筛选 + 分页）

### 端点
GET `/api/settlements/`

### 入参（query）
```
page=1&page_size=20
keyword=DES2605
settlement_status=待付款
created_at_from=2026-05-01&created_at_to=2026-05-26
payment_date_from=...&payment_date_to=...
promotion_id=... | blogger_id=... | style_id=... | pr_id=... | reviewed_by=... | paid_by=...
amount_from=...&amount_to=...
payment_amount_from=...&payment_amount_to=...
is_my=true   # PR 角色限自己提交
```

### 流程

```
[GET /api/settlements/?...]
  │
  ├─ Auth: require_permission("settlement", "read")
  │
  ├─ 1. role_codes = await roles.list_codes_for_user(user.id)
  │
  ├─ 2. PR 角色自动加 WHERE pr_id=user.id（service 层注入）
  │      其他角色按 query 参数自由筛选
  │
  ├─ 3. SQL 列表查询
  │      SELECT * FROM settlement
  │      WHERE tenant_id = :tid
  │        AND <过滤条件>
  │      ORDER BY created_at DESC
  │      LIMIT :page_size OFFSET :offset;
  │
  ├─ 4. service.to_response 字段过滤（BR-U05-51）
  │      非 PAYMENT_VISIBLE_ROLES 角色 → amount/total/payment_amount/cpl 字段置 None
  │      payment_proof_attachment_id 存在时 → AttachmentService.get_signed_url(15min)
  │
  ├─ 5. settlement_search_results_count.observe(total)
  │
  └─ return SettlementPage {items, total, page, page_size}
```

### 关键字段索引命中
- keyword → `idx_settlement_no_trgm` (GIN trgm)
- settlement_status → `idx_settlement_tenant_status`
- payment_date_* → `idx_settlement_payment_date`
- blogger_id/style_id/pr_id → 各自 idx

### 性能 SLA
- P95 ≤ 200ms（10K settlement 单租户）

---

## 端到端时序：U04 review approve → U05 settlement 创建 → 财务付款（J4 旅程）

```
[PR 主管点击 review approve（U04）]
  ↓
PUT /api/promotions/{id}/review {action: "approve"}
  ↓
[U04 service.review approve]
  ├─ 业务前置校验
  ├─ UPDATE promotion.settlement_status: 待核查 → 待付款 [FB7]
  ├─ AuditService.log("promotion.review.approve")
  ├─ event_bus.dispatch(SettlementRequested, session=self._session)  ← 同事务
  │     └─ [U05 listener.on_settlement_requested]
  │            ├─ 三重幂等检查
  │            ├─ INSERT settlement_sequence ON CONFLICT
  │            ├─ INSERT settlement (settlement_status="待核查", FB1)
  │            ├─ session.flush() [FB6]
  │            ├─ AuditService.log("settlement.create_via_event")
  │            └─ return
  └─ session.commit() ← 原子提交（promotion + settlement）

[PR 主管在 settlement 列表看到新 settlement，状态=待核查]
  ↓
[PR 主管点击 review approve（U05）]
  ↓
PUT /api/settlements/{id}/review {action: "approve"}
  ↓
[U05 service.review approve]
  ├─ 自审禁止 + 状态机校验
  ├─ UPDATE settlement.settlement_status: 待核查 → 待付款 [FB7]
  └─ commit

[PR 主管增加 extra_item（运费 20 元）]（可选）
  ↓
POST /api/settlements/{id}/extra-items
  ↓
[U05 service.add_extra_item]
  ├─ INSERT extra_item
  ├─ UPDATE settlement.total_amount = amount + SUM(extra_items)
  └─ commit

[PR 主管填写付款金额]
  ↓
PUT /api/settlements/{id}/payment-amount {payment_amount: "1020.00"}
  ↓
[U05 service.fill_payment]
  ├─ UPDATE settlement.settlement_status: 待付款 → 待财务付款 [FB7]
  ├─ UPDATE settlement.payment_amount=1020.00
  └─ commit

[财务收到通知（V1 企微集成）]
[财务前端选择文件 → POST /api/attachments/upload → 直传 R2 private]
  ↓
PUT /api/settlements/{id}/payment-proof {payment_date: "2026-05-26", payment_proof_attachment_id: "..."}
  ↓
[U05 service.mark_paid]
  ├─ attachment 6 项强校验 [FB4]
  ├─ UPDATE settlement.settlement_status: 待财务付款 → 已付款 [FB7]
  ├─ UPDATE settlement.payment_date / proof_attachment_id / paid_by
  ├─ event_bus.dispatch(SettlementPaid, session=self._session)
  │     └─ [U04 listener.on_settlement_paid] (通知类，FB5)
  │            └─ UPDATE promotion.settlement_status: 待付款 → 已付款 [FB7]
  └─ commit

[财务流程结束，settlement 永久不可替换 (FB3)]
```

---

## 错误处理矩阵（含字段权限）

| 端点 | 触发场景 | 错误码 | HTTP |
|---|---|---|---|
| POST /api/settlements/ | （无创建端点；仅事件创建） | — | — |
| GET /api/settlements/{id} | 不存在 | SETTLEMENT_NOT_FOUND | 404 |
| PUT /api/settlements/{id}/review | 自审 | SELF_REVIEW_FORBIDDEN | 403 |
|  | reject 缺 reason | REVIEW_REASON_REQUIRED | 422 |
|  | 状态机非法 | ILLEGAL_STATE_TRANSITION | 422 |
|  | 并发 | SETTLEMENT_STATE_CONFLICT | 409 |
| POST /api/settlements/{id}/extra-items | 非 PR 主管 | FIELD_PERMISSION_DENIED | 403 |
|  | settlement_status != 待付款 | EXTRA_ITEM_NOT_ALLOWED | 422 |
| PUT /api/settlements/{id}/payment-amount | 非 PR 主管 | FIELD_PERMISSION_DENIED | 403 |
|  | 缺 payment_amount | PAYMENT_AMOUNT_REQUIRED | 422 |
|  | 状态机非法 | ILLEGAL_STATE_TRANSITION | 422 |
| PUT /api/settlements/{id}/payment-proof | 非 admin/finance | FIELD_PERMISSION_DENIED | 403 |
|  | attachment 跨租户 | INVALID_ATTACHMENT_REFERENCE | 422 |
|  | attachment.bucket != private | INVALID_ATTACHMENT_BUCKET | 422 |
|  | mime 不在白名单 | INVALID_ATTACHMENT_MIME | 422 |
|  | size > 10MB | ATTACHMENT_TOO_LARGE | 422 |
|  | attachment.status != ready | ATTACHMENT_NOT_READY | 422 |
|  | 缺 payment_date | PAYMENT_FIELD_MISSING | 422 |
|  | 已付款 | SETTLEMENT_STATE_CONFLICT | 409 |
| DELETE /api/settlements/{id} | （任何场景） | METHOD_NOT_ALLOWED | 405 |

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| UC-1：handler 同事务 + flush + 三重幂等 | ✅ |
| UC-2：自审禁止 + UPDATE WHERE 旧状态（FB7） | ✅ |
| UC-3：仅"待付款"允许 + 维护 total_amount | ✅ |
| UC-4：fill_payment 状态推进 | ✅ |
| UC-5：attachment 6 项强校验（FB4） + 反向事件（FB5） | ✅ |
| UC-6 / UC-7：双口径汇总（FB7） | ✅ |
| UC-8：PR 角色自动 WHERE pr_id 过滤 + 字段权限过滤 | ✅ |
| 端到端时序：U04 → U05 → 财务付款全闭环 | ✅ |
| 错误码与 U04 风格一致 | ✅ |
| MVP 不提供 DELETE 接口（FB3） | ✅ |
| 8 P1 反馈全部体现 | ✅ |
