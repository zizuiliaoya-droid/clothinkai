# U05 功能设计计划（Functional Design Plan）

> 单元：U05 — 财务结款核心  
> 阶段：MVP 第 5 个单元（关键路径核心 — 与 U04 同批部署激活 SettlementRequested 事件链路）  
> 依赖：U04 全部就绪（含 SettlementRequested 事件契约）

---

## 0. 已应用 P1 反馈修正（8 条）

| # | 反馈 | 修正内容 |
|---|---|---|
| FB1 | settlement 初始状态口径不一致 | settlement.settlement_status 起点统一 = "待核查"；U04 的 promotion.settlement_status 与 U05 的 settlement.settlement_status **语义脱钩**（详见 §1.2）；EP06-S02 故事文本"待付款"为旧口径需在 stories.md 同步修订 |
| FB2 | backfill 状态与正常路径不一致 | backfill 也写"待核查"（与正常 SettlementRequested 路径完全一致）|
| FB3 | is_active=false 释放唯一约束不安全 | **删除 is_active 字段**；UNIQUE(tenant_id, promotion_id) **永久不可替换**；不级联软删；MVP 不提供删除接口（DELETE 返回 405）；V2 通过 order_adjustment 调整单实现金额修正 |
| FB4 | 付款截图应存 attachment_id 不是裸 R2 key | `payment_proof_attachment_id` (FK to attachment) 替代裸 key；service 校验 tenant / bucket / purpose / mime / size / status |
| FB5 | 反向同步前后冲突 | 删除完整对照表；MVP **仅 mark_paid → SettlementPaid 反向同步** "已付款"；其他状态以 settlement 为 source of truth |
| FB6 | handler 不 flush 错误延迟 | handler 内 `await session.flush()` 让 UNIQUE / FK 错误立即暴露 |
| FB7 | 当日汇总日期口径混合 | 拆双口径：`/daily-summary/activity` 与 `/daily-summary/as-of` |
| FB8 | backfill 写法松散 | 独立 `008_u05_backfill_settlements.py` migration；复用 settlement_sequence 与 format_settlement_no 与正常路径一致；upgrade（不是 downgrade 后追加）|

---

## 1. 单元上下文

### 1.1 覆盖故事

| 故事 | 阶段 | 说明 |
|---|---|---|
| EP06-S02 | MVP | 自动生成结算单（监听 SettlementRequested → 创建 settlement，settlement_status="**待核查**"，按 promotion_id 永久幂等）|
| EP06-S03 | MVP | PR 主管核查结算单（settlement_status: 待核查 → 待付款）|
| EP06-S04 | MVP | PR 主管驳回结算（含 reason） |
| EP06-S05 | MVP | PR 主管增加结算项（运费 / 赞奖等额外费用） |
| EP06-S06 | MVP | PR 主管填写付款金额 → 状态推进到"待财务付款"|
| EP06-S07 | MVP | 财务上传付款截图（R2 private 桶） + 标记已付款 |
| EP06-S08 | MVP | 当日结算汇总（双口径：activity / as_of） |

> **故事文本修正提醒（FB1）**：EP06-S02 验收原文写"settlement_status='待付款'"是旧口径，与 INCEPTION 决策不符。
> 实际口径：SettlementRequested → settlement.settlement_status="**待核查**"（U05 起点）；PR 主管 approve 后才到"待付款"。
> stories.md 在 U05 实施时同步修订（U04 functional-design plan 已记同类提醒）。

### 1.2 职责边界（关键决策）

> INCEPTION 阶段已锁定（U04 + U05 同批部署，FB1 强一致）

**U05 的职责**：
- 监听 U04 发出的 `SettlementRequested` 事件 → **创建 settlement 记录**（按 promotion_id 永久幂等）
- settlement 全生命周期管理（5 主状态：**待核查** → 待付款 → 待财务付款 → 已付款；支线 → 已驳回）
- settlement_extra_item 子表(运费 / 赞奖等额外项)
- 付款金额字段 + 付款截图(R2 private 桶 + 签名 URL，通过 attachment 表引用)
- 当日结算汇总查询(activity 与 as-of 双口径)

**U05 不做（其他单元）**：
- ❌ promotion 状态管理（U04 已实现）
- ❌ order_adjustment / 拍单刷单 / 余额（U16 V2）
- ❌ 投产报表 ROI 计算（U14 V1）
- ❌ 字段级权限改造（U09，本单元用 legacy_field_permissions 过渡）
- ❌ 已付款 settlement 替换/作废（V2 通过调整单 order_adjustment 实现，财务记录 MVP 永久不可替换）

**关键语义脱钩（来自 P1 反馈 FB1）**：
- `promotion.settlement_status`（U04 字段）= "U04 端的指示器"，标记 settlement 流程是否已启动 / 是否已最终付款；U04 review approve 时推进到"待付款"，含义为"已交给 U05 流程"
- `settlement.settlement_status`（U05 字段）= "U05 内部独立状态机"，起点 = "待核查"
- 两个字段语义独立，不直接对齐
- 仅 mark_paid 终态通过 SettlementPaid 反向事件同步 U04 端 → "已付款"

### 1.3 与 U04 的契约

```python
# 来自 modules/promotion/events.py
@dataclass(frozen=True)
class SettlementRequested:
    event_type: ClassVar[str] = "SettlementRequested"
    required_handler: ClassVar[bool] = True
    
    event_id: UUID                  # 幂等键（每次审核新生成）
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal                 # = promotion.quote_amount
    requested_by: UUID              # 审核人
    requested_at: datetime
```

**幂等保证**（**永久 + 三重防护，FB3 修正**）：
- DB 层：`UNIQUE(tenant_id, promotion_id)` — **不带 partial WHERE**，永久不可替换
- DB 层：`UNIQUE(request_event_id)` — 事件重放防护
- service 层：`SELECT` 检查 + 已存在则 no-op + 写 audit "duplicate_skipped"

财务记录不可替换原则（FB3）：
- 已创建的 settlement 不允许通过软删 + 重建绕过唯一性
- 错误付款 / 金额修正在 V2 通过 `order_adjustment` 调整单实现（U16）
- MVP 阶段若需修正：admin 通过 audit 留痕的手动 SQL（极少场景）+ 不通过 service 接口

### 1.4 覆盖代码

```
backend/app/modules/finance/         # U05 新建模块
├── __init__.py
├── enums.py                         # SettlementStatus / ExtraItemType
├── permissions.py                   # settlement:read/write/review/pay
├── legacy_field_permissions.py      # PAYMENT_VISIBLE_ROLES / EXTRA_ITEM_WRITABLE_ROLES（U09 后清理）
├── exceptions.py                    # 8 业务异常
├── models.py                        # Settlement + SettlementExtraItem
├── schemas.py                       # 12+ Pydantic
├── state_machines.py                # SettlementStatusMachine（4 状态 + 已驳回支线）
├── domain.py                        # compute_settlement_changes + audit 脱敏 + format_settlement_no
├── repository.py                    # SettlementRepository + ExtraItemRepository + next_settlement_sequence
├── service.py                       # SettlementService（含 on_settlement_requested handler）
├── listeners.py                     # register() — 注册到 core/events 事件总线（被 main.py register_event_listeners 调用）
├── deps.py                          # FastAPI 依赖
└── api.py                           # 8 端点

backend/app/core/attachment.py       # 修改：U01 已搭好基础设施，本单元首次使用 R2 private bucket
backend/app/core/metrics.py          # 修改：追加 3 个 settlement 指标
backend/app/main.py                  # 已就绪：U04 batch 4 已含 register_event_listeners 框架（仅需 finance 模块存在即激活）

backend/alembic/versions/007_u05_create_settlement_tables.py
backend/alembic/versions/008_u05_backfill_settlements.py    # FB8: 独立 migration，复用 settlement_sequence
```

### 1.5 设计阶段产出文档

- `aidlc-docs/construction/U05/functional-design/domain-entities.md`
- `aidlc-docs/construction/U05/functional-design/business-rules.md`
- `aidlc-docs/construction/U05/functional-design/business-logic-model.md`

---

## 2. 计划步骤

### Step 1 — 确认范围
- [x] 1.1 读取 unit-of-work U05 定义
- [x] 1.2 读取 stories EP06-S02~S08 完整验收
- [x] 1.3 标记 U04/U05 职责边界（事件驱动 + 幂等）
- [x] 1.4 标记 U06e (settlement 导入) / U16 (拍单刷单) 范围排除

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出覆盖故事与职责边界
- [x] 2.2 列出问题分类
- [ ] 2.3 等待用户填答 [Answer]

### Step 3 — 生成 domain-entities.md
- [x] 3.1 Settlement 实体字段表
- [x] 3.2 SettlementExtraItem 子表
- [x] 3.3 SettlementSequence 序列号表（settlement_no 生成）
- [x] 3.4 3 个 Python Enum（SettlementStatus / ExtraItemType / Platform 复用）
- [x] 3.5 ER 图（Mermaid）
- [x] 3.6 索引清单
- [x] 3.7 演化路线（U06e / U09 / U14 / U16 引用）

### Step 4 — 生成 business-rules.md
- [x] 4.1 SettlementRequested 事件处理（**FB6 flush + FB1 起点=待核查 + FB3 永久 UNIQUE**）
- [x] 4.2 settlement_no 生成规则
- [x] 4.3 SettlementStatusMachine 转移表（5 状态：待核查→待付款→待财务付款→已付款 / 已驳回支线）
- [x] 4.4 必填 + 引用完整性
- [x] 4.5 付款字段约束（payment_amount / payment_date / **payment_proof_attachment_id** 同时校验，FB4）
- [x] 4.6 SettlementExtraItem 业务规则（金额合计、权限）
- [x] 4.7 字段级权限（PAYMENT_VISIBLE / PAYMENT_WRITABLE / PROOF_UPLOAD）
- [x] 4.8 付款截图 R2 private 桶 + **attachment 表强校验**（tenant / bucket / purpose / mime / size / status，FB4）
- [x] 4.9 当日汇总查询（**双口径：activity / as_of**，FB7）
- [x] 4.10 财务记录不可替换原则（**FB3 + 删除接口 405**）
- [x] 4.11 错误码矩阵
- [x] 4.12 SettlementPaid 反向事件契约（**仅 mark_paid，FB5**）

### Step 5 — 生成 business-logic-model.md
- [x] 5.1 监听 SettlementRequested → 幂等创建 settlement UC（**FB6 flush + FB1 起点=待核查**）
- [x] 5.2 review approve / reject UC（settlement 端）
- [x] 5.3 add_extra_item UC（PR 主管增加运费 / 赞奖）
- [x] 5.4 fill_payment_amount UC（PR 主管 → "待财务付款"）
- [x] 5.5 upload_payment_proof UC（财务 → "已付款" + **attachment 强校验**，FB4）
- [x] 5.6 daily_summary UC（**双口径**，FB7）
- [x] 5.7 列表查询 + 多筛选
- [x] 5.8 backfill 流程（独立 migration 008，**FB8**）

### Step 6 — 提交完成消息 + 等待审批

---

## 3. 澄清问题（请填 [Answer]）

> 18 个澄清问题，每问预填合理默认值。

### 3.1 settlement_no 生成

**Q1**：`settlement_no` 生成规则？

[Answer]: 格式 `<tenant_prefix>S<yyMMdd><sequence>`：
- `tenant_prefix`：tenant.code 前 2 位大写（同 U04 internal_code 风格）
- 字面 `S` 标识 settlement
- `yyMMdd`：取 SettlementRequested 事件触发时的 `requested_at`（不是 promotion.cooperation_date）
- `sequence`：4 位 0 填充
- 示例：`DES2605260001`
- **唯一约束**：`(tenant_id, settlement_no)`（不软删，标准 UNIQUE）

实施方式（复用 U04 FB2 模式）：
```sql
INSERT INTO settlement_sequence (id, tenant_id, date_key, last_seq, ...)
VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
ON CONFLICT (tenant_id, date_key) DO UPDATE
SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
RETURNING last_seq;
```

### 3.2 Settlement 实体字段范围

**Q2**：Settlement 表 MVP 字段范围？

[Answer]: 完整字段表（约 22 字段）：

**关联**：
- `id` (UUID PK) / `tenant_id` (UUID FK)
- `promotion_id` (UUID FK to promotion, **UNIQUE per tenant 永久** — 一个 promotion 仅一条 settlement，FB3)
- `blogger_id` (UUID FK to blogger) — 冗余存便于查询
- `style_id` (UUID FK to style) — 冗余存便于按款式聚合
- `pr_id` (UUID FK to user) — 创建该 promotion 的 PR
- `reviewed_by` (UUID FK to user, 可选) — settlement 端核查人
- `paid_by` (UUID FK to user, 可选) — 财务付款上传人

**业务键 + 业务字段**：
- `settlement_no` (VARCHAR(64), UNIQUE per tenant)
- `amount` (DECIMAL(12,2)) — 基础金额（来自 SettlementRequested.amount = promotion.quote_amount）
- `total_amount` (DECIMAL(12,2)) — 包含 extra_items 后的合计金额（service 层维护）
- `payment_amount` (DECIMAL(12,2), 可选) — PR 主管确认的付款金额（可能与 total_amount 不同：抹零、汇率等）
- `payment_date` (DATE, 可选) — 财务填写
- `payment_proof_attachment_id` (UUID FK to attachment, 可选) — **引用 attachment 表，不是裸 R2 key（FB4）**；service 层校验：tenant_id 一致 / bucket="private" / purpose="settlement_proof" / mime in (image/*, application/pdf) / size <= 10MB / status="ready"
- `note_title` (VARCHAR(255), 可选) — 笔记标题（冗余 from promotion）
- `remark` (TEXT, 可选)

**状态**（FB1 修正：起点 = 待核查）：
- `settlement_status` (VARCHAR(16))，5 个值：
  - `待核查`（**默认值，由 SettlementRequested handler 创建时写入**）
  - `待付款`（PR 主管 approve 后）
  - `待财务付款`（PR 主管 fill_payment 后）
  - `已付款`（财务 mark_paid 后，终态）
  - `已驳回`（PR 主管 reject 后，可 resubmit 回到"待核查"）

**核查 / 驳回**：
- `reviewed_at` (TIMESTAMPTZ, 可选)
- `review_action` (VARCHAR(16), 可选)：approve / reject
- `review_reason` (TEXT, 可选)

**事件溯源**：
- `request_event_id` (UUID, **NOT NULL** + UNIQUE) — 来自 SettlementRequested.event_id（幂等键）

**通用**：
- `created_at` / `updated_at` (TIMESTAMPTZ)

> **删除 `is_active` 字段（FB3 修正）**：财务记录永久不可软删；
> 对应 promotion 软删时不级联软删 settlement（settlement 是独立财务实体，必须留痕）；
> 若极少数场景需"取消已创建 settlement"，admin 通过手动 SQL 操作并必写 audit。

不含的字段（V2 / 其他单元）：
- `exclude_from_roi`（U16 V2）
- `order_type`（U16 V2）
- `sku_id`（settlement 是按 promotion 而非 sku 维度，但 promotion.sku_id 已快照 → settlement 不存）
- `is_active`（FB3：财务记录不软删）

**Q3**：是否设 `SettlementExtraItem` 子表（多个 extra item per settlement）？

[Answer]: **是，独立子表**：

```sql
settlement_extra_item:
  id UUID PK
  tenant_id UUID FK
  settlement_id UUID FK ON DELETE CASCADE
  item_type VARCHAR(16)   -- 运费 / 赞奖 / 其他
  amount DECIMAL(12,2)
  remark VARCHAR(255) NULL
  created_at TIMESTAMPTZ
  created_by UUID FK to user  -- 哪个 PR 主管添加的
```

约束：
- `amount > 0`（CHECK 约束）
- service 层维护 `settlement.total_amount = settlement.amount + SUM(extra_items.amount)`
- 仅 settlement_status="待付款" 时允许新增 extra_item（BR-U05-31）

### 3.3 SettlementStatusMachine 定义

**Q4**：SettlementStatusMachine 转移表？

[Answer]: 4 主状态 + 1 支线 — 共 6 条转移：

| from | event | to | 触发者 | 校验 |
|---|---|---|---|---|
| 待核查 | approve | 待付款 | PR 主管 | 不能自审 (reviewer != promotion.pr_id) |
| 待核查 | reject | 已驳回 | PR 主管 | review_reason 必填 |
| 待付款 | reject | 已驳回 | PR 主管 | review_reason 必填（增加 extra_item 后发现问题） |
| 待付款 | fill_payment | 待财务付款 | PR 主管 | payment_amount 必填 + > 0 |
| 待财务付款 | mark_paid | 已付款 | 财务 | payment_date + payment_proof_attachment_key 同时必填 |
| 已驳回 | resubmit | 待核查 | PR 主管 | （PR 修改后重新提交） |

**注**：
- "待财务付款"是 U05 新增状态（与 U04 promotion.settlement_status 的"待付款"不同；U05 自己内部维护）
- promotion.settlement_status 在 settlement 推进到"已付款"时由反向事件 `SettlementPaid` 通知 U04 同步推进（mark_paid action）— 这是 U05 → U04 的反向通知

**Q5**：是否需要 `SettlementPaid` 反向事件通知 U04？

[Answer]: **MVP 仅一个反向事件 `SettlementPaid`**，标记为通知类。

设计要点（FB5 修正：删除完整对照表，避免设计前后冲突）：
- U04 promotion.settlement_status 与 U05 settlement.settlement_status 是**独立状态机**，语义脱钩（详见 §1.2）
- 仅终态 mark_paid 有共同含义"流程结束"，需要反向通知 U04 同步推进 promotion.settlement_status="已付款"
- `SettlementPaid` 标记 `required_handler = False`（无 listener 不抛错）
- U04 端 listener 监听 SettlementPaid → UPDATE promotion SET settlement_status='已付款' WHERE tenant_id=... AND settlement_status='待付款' AND id=... （FB7 模式 + 旧状态校验）
- U04 端在 V1 / U14 实施前可暂不实施 listener；U05 单独运转不影响 U04 数据正确性
- 中间状态（reject / fill_payment / resubmit）**不反向同步**；以 settlement 为 source of truth

具体事件契约：
```python
@dataclass(frozen=True)
class SettlementPaid:
    event_type: ClassVar[str] = "SettlementPaid"
    required_handler: ClassVar[bool] = False  # 通知类
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    payment_amount: Decimal
    payment_date: date
    paid_by: UUID
```

> **MVP 阶段不引入** `SettlementRejected` / `SettlementResubmitted` 等反向事件 — promotion.settlement_status="待付款" 在驳回 / 重提期间保持不变，以 settlement 端展示为准。
> V1 视用户反馈再评估是否需要细分中间状态的反向同步。

### 3.4 幂等设计

**Q6**：settlement 创建幂等如何保证？

[Answer]: **三重防护**（FB3 修正：永久唯一）：
1. **DB 层 UNIQUE 永久**：`UNIQUE (tenant_id, promotion_id)` — 不带 partial WHERE，永久不可替换
2. **request_event_id 兜底**：`UNIQUE (request_event_id)` 防同一事件被重放投递
3. **service 层 SELECT 兜底**：handler 入口先 SELECT 是否已存在 → 已存在则 no-op + 写 audit `settlement.create_skipped_duplicate` + 不抛错

事件重放场景（U05 重启 / Celery retry）：
- 同一 SettlementRequested.event_id 重复投递 → DB UNIQUE(request_event_id) 阻止 + service 层 SELECT 检测 → no-op
- 不同 event_id 但同 promotion_id（不可能，但兜底）→ DB UNIQUE(promotion_id) 阻止

**Q7**：settlement 创建 handler 在什么 session 中执行？

[Answer]: **U04 的 service session**（同事务）：
- U04 service.review approve 调 `event_bus.dispatch(event, session=self._session)`
- 该 session 已含 promotion 的 UPDATE（settlement_status: 待核查 → 待付款）
- handler 在同一 session 中创建 settlement → **同事务原子提交**
- 若 settlement INSERT 失败 → 整个事务回滚 → promotion 状态也回滚（FB1 强一致）

实施细节：
```python
# modules/finance/listeners.py
async def on_settlement_requested(event: SettlementRequested, session: AsyncSession) -> None:
    """同事务 handler：失败抛异常导致 U04 端事务回滚."""
    repo = SettlementRepository(session)
    
    # 1. 幂等检查：DB UNIQUE 已防，但额外 SELECT 兜底（友好错误 + audit 区分）
    existing = await repo.find_by_promotion_id(event.promotion_id)
    if existing is not None:
        log.info("settlement_create_skipped_duplicate", extra={
            "event_id": str(event.event_id),
            "existing_settlement_id": str(existing.id),
        })
        # 不抛错，但可写 audit 标记 duplicate
        return
    
    # 2. 序列号原子获取（复用 U04 next_internal_sequence 模式）
    seq = await repo.next_settlement_sequence(
        tenant_id=event.tenant_id, date_key=event.requested_at.date()
    )
    settlement_no = format_settlement_no(...)
    
    # 3. 创建实体 + add 到同 session
    settlement = Settlement(
        id=uuid4(),
        tenant_id=event.tenant_id,
        promotion_id=event.promotion_id,
        blogger_id=event.blogger_id,
        ...
        settlement_no=settlement_no,
        amount=event.amount,
        total_amount=event.amount,  # 初始无 extra_item
        settlement_status=SettlementStatus.PENDING_REVIEW.value,  # FB1: 起点 = 待核查
        request_event_id=event.event_id,
    )
    session.add(settlement)
    
    # FB6: 立即 flush 让 UNIQUE / FK 错误在 dispatch 阶段就暴露
    # 而不是延迟到外层 commit（错误定位 / audit / metrics 都更明确）
    await session.flush()
```

### 3.5 字段级权限

**Q8**：哪些字段是敏感字段（U09 字段级权限前的硬编码范围）？

[Answer]: 3 类敏感字段：

```python
# modules/finance/legacy_field_permissions.py
PAYMENT_VISIBLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager", "finance"
})
"""可见 payment_amount / payment_date / payment_proof / extra_item.amount 的角色."""

PAYMENT_WRITABLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager"  # finance 可见但不可写 payment_amount（fill_payment 是 PR 主管动作）
})
"""可写 payment_amount 的角色."""

PROOF_UPLOAD_ROLES: frozenset[str] = frozenset({
    "admin", "finance"  # PR 主管不能上传付款截图
})
"""可上传付款截图的角色."""
```

权限矩阵：

| 角色 | settlement read | review approve | fill_payment_amount | upload_proof | view_payment_amount | add_extra_item |
|---|---|---|---|---|---|---|
| admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| finance | ✅（限制 payment 视图）| ❌ | ❌ | ✅ | ✅ | ❌ |
| pr | ✅（限自己提交的） | ❌ | ❌ | ❌ | ❌ | ❌ |
| 其他 | ❌ | — | — | — | — | — |

### 3.6 R2 付款截图

**Q9**：付款截图存储 + 签名 URL 流程？

[Answer]: 流程（U01 已建好的 attachment 框架，FB4 修正：必须通过 attachment 表引用）：

1. 财务前端选择文件 → POST `/api/attachments/upload`（U01 attachment 框架）→ 后端创建 `attachment` 表行（status="uploading"）+ 返回 `attachment_id` + 临时上传 URL
2. 前端直传 R2 private bucket，path = `{tenant_id}/settlements/proof/{attachment_id}/{filename}`（path 由 attachment 框架决定）
3. 前端调 POST `/api/attachments/{attachment_id}/complete` 标记 status="ready" + 校验 mime + size
4. 前端拿到 attachment_id → POST `/api/settlements/{id}/payment-proof` 提交 payment_date + attachment_id（不是裸 R2 key）
5. service 校验（FB4 关键）：
   - settlement_status="待财务付款"
   - payment_date 必填 + 不晚于 today
   - attachment 通过 `AttachmentService.get_by_id(attachment_id)` 取出，校验：
     - `attachment.tenant_id == current_user.tenant_id`（防越权）
     - `attachment.bucket == "private"`
     - `attachment.purpose == "settlement_proof"`（用途绑定）
     - `attachment.mime_type IN ("image/jpeg", "image/png", "image/webp", "application/pdf")`
     - `attachment.size_bytes <= 10 * 1024 * 1024`
     - `attachment.status == "ready"`
6. 写入 settlement.payment_proof_attachment_id (FK) + settlement_status="已付款"
7. 发 SettlementPaid 事件（通知类）
8. 后续读取通过 `AttachmentService.get_signed_url(attachment_id, ttl=15min)` 返回签名 URL（在 PromotionResponse / SettlementResponse 序列化时按角色权限决定是否暴露）

**绝不存裸 R2 key**：
- 裸 key 绕过 attachment 表的权限、审计、生命周期管理
- 没有租户隔离、purpose 用途绑定、过期清理、引用计数等保护
- attachment 表是统一的"附件元数据 source of truth"

**Q10**：付款截图删除 / 替换策略？

[Answer]: **MVP 完全不支持替换 / 删除**（FB3 一致：财务记录不可替换原则）：
- settlement.settlement_status="已付款" 是终态
- payment_proof_attachment_id 一旦写入就不可修改
- 若发现错误付款，**V2 通过 order_adjustment 调整单记录修正**（U16 落地）
- MVP 阶段若极少数情况需修正：admin 手动 SQL + audit 留痕 + Sentry 告警
- attachment 表本身的 GC 策略需排除"已被 settlement 引用"的 attachment（V1 attachment 引用计数）

### 3.7 当日汇总

**Q11**：当日结算汇总查询语义？

[Answer]: **FB7 修正：拆成双口径，避免混合**

#### 口径 A — 当日活动汇总（activity）
GET `/api/settlements/daily-summary/activity?date=2026-05-26`

回答"今天发生了什么结算动作"。
- 按"发生在 :date 当天"分组：
  - 当天 created（新建）的 settlement 计入"待核查"或当日推进后的状态
  - 当天 paid（payment_date == :date）的计入"已付款"
- 适合每日复盘、核查工作量、对账单生成

```json
{
  "kind": "activity",
  "date": "2026-05-26",
  "activity": {
    "newly_created": { "count": 12, "total_amount": "12000.00" },
    "newly_approved": { "count": 8, "total_amount": "8500.00" },
    "newly_paid": { "count": 20, "total_amount": "21000.00" },
    "newly_rejected": { "count": 2, "total_amount": "1500.00" }
  }
}
```

实现：扫描 audit_log + settlement 表交叉查询当天发生的状态推进。

#### 口径 B — 截至当日快照（as_of）
GET `/api/settlements/daily-summary/as-of?date=2026-05-26`

回答"截至 :date 末，各状态的余额是多少"。
- 取 :date 当天 23:59 时刻的 settlement 状态快照
- 适合现金流预测、未付款余额监控

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
其中 outstanding = pending_review + pending_payment + pending_finance（不含已驳回 / 已付款）。

实现：直接 GROUP BY settlement_status；时区取 Asia/Shanghai，复用 `urge_calculator.get_today` 入口。

> **MVP 阶段两个 endpoint 都实现**；前端默认调 as_of（用户最常查的"还差多少没付"），activity 仅用于专项报表查询。
> EP06-S08 故事文本如未明确口径，按上述"双口径"方案实施并在 functional-design 文档中标注。

### 3.8 列表查询

**Q12**：settlement 列表筛选维度？

[Answer]:
- 状态：settlement_status / 多选
- 时间范围：created_at_from/to + payment_date_from/to
- 关联：promotion_id / blogger_id / style_id / pr_id / reviewed_by / paid_by
- 关键字：settlement_no（GIN trgm 索引）+ promotion.internal_code 联合 ILIKE
- 是否仅显示自己提交的（is_my=true 时 promotion.pr_id=current_user.id）
- amount_from/to / payment_amount_from/to 范围筛选

排序：默认 created_at DESC；可指定 payment_date DESC。

### 3.9 删除 / 软停用

**Q13**：settlement 删除策略？

[Answer]: **MVP 不提供删除接口**（FB3 修正：财务记录永久不可替换）：
- DELETE `/api/settlements/{id}` 直接返回 405 Method Not Allowed
- promotion 软删时**不级联**软删 settlement（settlement 是独立财务实体，必须独立留痕）
- 极少数场景需取消（如"PR 主管误审了 5 分钟前的 promotion"）：通过状态机 reject 路径走"已驳回"，而非删除
- V2 通过 order_adjustment 调整单记录金额修正，settlement 行始终保留

### 3.10 audit 脱敏

**Q14**：audit 字段白名单 + 脱敏？

[Answer]: 复用 U02/U03/U04 同模式：

```python
SETTLEMENT_SENSITIVE_FIELDS = frozenset({
    "amount", "total_amount", "payment_amount", "payment_date",
    "settlement_status", "review_action", "payment_proof_attachment_id",
})

SETTLEMENT_SENSITIVE_VALUE_FIELDS = frozenset({
    "amount", "total_amount", "payment_amount",
})  # 仅记 *_changed: true 标记
```

`payment_proof_attachment_id` 写 audit 时仅记 `attachment_id_changed: true`（避免暴露 attachment 内部 ID 与 R2 路径的关联）。

非敏感字段（如 remark / note_title）→ 不写 audit。

### 3.11 性能 / 并发

**Q15**：settlement 创建 handler 并发性能？

[Answer]:
- 单租户单日 SettlementRequested 事件估算 ≤ 100 个（与 U04 review approve 同频次）
- handler 同事务执行，不引入异步队列（FB1 强一致）
- INSERT settlement + INSERT settlement_sequence ON CONFLICT 原子操作 → 单事件 P95 ≤ 50ms 增量
- 不影响 U04 review approve P95 ≤ 500ms 的 SLA（review approve 总 P95 含 settlement 创建）

### 3.12 历史数据

**Q16**：U05 上线时是否需要为 U04 已审核的 promotion 补建 settlement？

[Answer]: **是**，但需严格收紧（FB1 + FB2 + FB8 修正）：

#### 范围限定
- U04 + U05 同批部署 → 实际只有 U04 早于 U05 上线的"几小时窗口"内累积的待付款 promotion
- 这些 promotion 的 promotion.settlement_status="待付款"（U04 已 review approve）
- 但因为 U05 监听器尚未启动，dispatch 阶段抛 MissingRequiredHandlerError → review approve 事务回滚 → 实际不应有这种数据

> **预期结果**：FB1 强一致策略下，backfill 应该补 0 行（理论上）。
> Migration 仍要落地以应对边界场景（如 listener 注册晚于 review approve 半秒、监听器异常重启等）。

#### 状态映射（FB1 + FB2 修正：与正常事件创建口径一致）
- 正常事件创建路径 → settlement_status="**待核查**"
- backfill 路径 → settlement_status="**待核查**"（保持一致，不是"待付款"）
- 含义：U05 视角"我是新建的，等 PR 主管核查"
- 副作用：原本 promotion.settlement_status="待付款" 的 promotion 现在对应一条 settlement（待核查）。这种短暂"U04 已待付款 / U05 还待核查"是合理的：promotion 端语义为"已交给 U05 流程"，settlement 端语义为"等 U05 内部核查"。U05 PR 主管 approve 后两边对齐到"待付款"。

#### settlement_no 格式（FB8 修正：复用正常序列号）
不再使用"BACKFILL"专用字符串前缀；通过 settlement_sequence 表正常分配 settlement_no（与正常路径完全一致）。

#### 独立 migration 文件（FB8 修正：不在 007 的 downgrade 后追加）
新建 `008_u05_backfill_settlements.py`：

```python
# alembic/versions/008_u05_backfill_settlements.py
"""U05 - 回填 U04 已审核但未创建 settlement 的历史数据.

Revision ID: 008_u05_backfill_settlements
Revises: 007_u05_create_settlement_tables
"""

def upgrade() -> None:
    # 1) 找出待回填 promotion
    op.execute("""
        CREATE TEMP TABLE _backfill_promotions AS
        SELECT
            p.id AS promotion_id,
            p.tenant_id,
            p.blogger_id,
            p.style_id,
            p.pr_id,
            p.quote_amount AS amount,
            p.note_title,
            p.reviewed_at AS requested_at,
            p.reviewed_by AS requested_by
        FROM promotion p
        WHERE p.settlement_status = '待付款'
          AND p.is_active = true
          AND NOT EXISTS (
              SELECT 1 FROM settlement s
              WHERE s.promotion_id = p.id
          );
    """)

    # 2) 为每个 (tenant_id, date_key) 通过 settlement_sequence 分配序号
    #    复用正常序列号体系，不引入"BACKFILL"特殊字符串
    op.execute("""
        DO $$
        DECLARE
            r RECORD;
            v_seq INTEGER;
            v_no TEXT;
            v_prefix TEXT;
        BEGIN
            FOR r IN SELECT * FROM _backfill_promotions ORDER BY tenant_id, requested_at LOOP
                -- 通过 INSERT ON CONFLICT 分配下一序列号（与正常 next_settlement_sequence 完全一致）
                INSERT INTO settlement_sequence (id, tenant_id, date_key, last_seq, created_at, updated_at)
                VALUES (gen_random_uuid(), r.tenant_id, r.requested_at::date, 1, NOW(), NOW())
                ON CONFLICT (tenant_id, date_key) DO UPDATE
                SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
                RETURNING last_seq INTO v_seq;

                -- 取 tenant_prefix（与 service 层 _get_tenant_code 一致）
                SELECT UPPER(LEFT(COALESCE(t.code, ''), 2)) INTO v_prefix
                FROM tenant t WHERE t.id = r.tenant_id;
                v_prefix := COALESCE(NULLIF(v_prefix, ''), 'XX');
                IF LENGTH(v_prefix) < 2 THEN
                    v_prefix := RPAD(v_prefix, 2, 'X');
                END IF;

                -- 生成 settlement_no（与 format_settlement_no 函数完全一致）
                v_no := v_prefix || 'S' || TO_CHAR(r.requested_at::date, 'YYMMDD') || LPAD(v_seq::TEXT, 4, '0');

                INSERT INTO settlement (
                    id, tenant_id, promotion_id, blogger_id, style_id, pr_id,
                    settlement_no, amount, total_amount, settlement_status,
                    request_event_id, note_title,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), r.tenant_id, r.promotion_id, r.blogger_id,
                    r.style_id, r.pr_id, v_no,
                    r.amount, r.amount,
                    '待核查',  -- FB1+FB2: 与正常路径一致
                    gen_random_uuid(),  -- 合成 event_id（无原始事件可追溯）
                    r.note_title,
                    NOW(), NOW()
                );
            END LOOP;
        END $$;
    """)

    op.execute("DROP TABLE _backfill_promotions;")


def downgrade() -> None:
    # 不可逆：财务数据不删；如需回滚需要 admin 手动审计后清理
    raise RuntimeError("backfill migration is not reversible")
```

执行时机：007 创建表后立即执行 008（同一 alembic upgrade 调用链一次完成）。

### 3.13 索引设计

**Q17**：settlement 表关键索引？

[Answer]（FB3 修正：永久唯一约束，无 partial）：
- `uq_settlement_no`：UNIQUE (tenant_id, settlement_no) — 永久
- `uq_settlement_promotion`：UNIQUE (tenant_id, promotion_id) — **永久（FB3：财务记录不可替换）**
- `uq_settlement_request_event_id`：UNIQUE (request_event_id) — 永久（事件重放防护）
- `idx_settlement_tenant_status`：(tenant_id, settlement_status, created_at DESC)
- `idx_settlement_blogger`：(tenant_id, blogger_id)
- `idx_settlement_style`：(tenant_id, style_id)
- `idx_settlement_pr`：(tenant_id, pr_id)
- `idx_settlement_payment_date`：(tenant_id, payment_date)（已付款汇总）
- `idx_settlement_reviewed_by`：(tenant_id, reviewed_by)
- `idx_settlement_paid_by`：(tenant_id, paid_by)
- `idx_settlement_no_trgm`：GIN trgm（无 partial：所有 settlement 都活跃）

settlement_extra_item 表：
- `idx_extra_item_settlement`：(tenant_id, settlement_id)

settlement_sequence 表：
- `uq_settlement_sequence`：UNIQUE (tenant_id, date_key)

### 3.14 向 U04 反向同步

**Q18**：U05 settlement 状态变化是否反向同步 promotion.settlement_status？

[Answer]（FB5 修正：删除完整对照表 + 锁定简化版）：

**MVP 仅同步 mark_paid 一个动作**：
- settlement.mark_paid（待财务付款 → 已付款）→ 发 `SettlementPaid` 事件
- U04 端 listener 监听 `SettlementPaid` → UPDATE promotion SET settlement_status='已付款' WHERE tenant_id=... AND settlement_status='待付款' AND id=...（FB7 模式 + 旧状态校验 + 多租户防护）
- U04 端 listener 不抛错（required_handler=False）

**其他状态推进不反向同步**：
- reject / fill_payment / resubmit 仅在 U05 settlement.settlement_status 反映；promotion.settlement_status 保持"待付款"不变
- 用户在 U04 端看到 promotion.settlement_status="待付款" 的语义为"已交给 U05 流程"，不细分 U05 内部状态
- 用户需查 settlement 详情时跳转到 settlement 列表 / 详情页

**实施位置**：U04 端 listener 在 `modules/promotion/listeners.py` 注册（U05 实施时新建 + main.py register_event_listeners 加载）

> 与 FB5 反馈一致：MVP 不实施完整双向同步对照表；V1 视用户反馈再评估。

---

## 4. 待澄清的歧义

无明显歧义。所有决策基于：
- INCEPTION U04+U05 同批部署 + FB1 强一致
- 复用 U02/U03/U04 模式（partial UNIQUE / GIN trgm / audit 脱敏 / legacy_field_permissions / FB2 序列号）
- 开发文档第 6.7 节"财务结款"15 列字段表 + 第 9 节"settlement_status"状态机表

---

## 5. 与下一阶段衔接

U05 完成后 + 与 U04 同批部署激活后：
- ✅ EP05-S13 端到端：PR 主管审核 → SettlementRequested → settlement 创建 → 状态推进 → 财务付款
- ✅ MVP 财务流程闭环
- 下一路径：**U06a 统一导入框架**（独立分支）或 **U07 企微基础**（监听 PromotionPublished）

---

**等待用户审阅 [Answer]，回复"继续"后进入 Step 3 生成 3 份功能设计文档。**
