# U04 业务规则（Business Rules）

> 单元：U04 — 推广合作核心  
> 与 domain-entities.md / business-logic-model.md 配合阅读  
> 复用 U02/U03 已建立的字段权限 / 审计脱敏模式

---

## 1. internal_code 生成规则

### BR-U04-01 — internal_code 格式
- **格式**：`<tenant_prefix><yyMMdd><sequence>` （4 位序列号）
- **tenant_prefix**：tenant.code 前 2 位转大写（不足 2 字符补 `X`）
- **yyMMdd**：取 `cooperation_date`（不是 `created_at`）
- **sequence**：4 位 0 填充（0001..9999），按 (tenant_id, cooperation_date) 当天累加
- **示例**：`DE2605260001`
- **超过 9999/天**：返回 `409 SEQUENCE_OVERFLOW`，要求联系运维（业务上 MVP 单租户单天创建 ≤ 1000 推广可控）

### BR-U04-02 — 序列号生成（防 race）
- 使用独立 `promotion_sequence` 表
- service 层在事务内执行：
  ```sql
  SELECT * FROM promotion_sequence
   WHERE tenant_id = :t AND date_key = :d
   FOR UPDATE;
  ```
- 不存在则 INSERT，存在则 UPDATE last_seq + 1
- INSERT promotion 与 UPDATE promotion_sequence 同事务 commit
- 防 race：行级锁 + 同事务

---

## 2. 必填与引用完整性

### BR-U04-10 — Promotion 必填字段（创建时）
- `style_id`（外键校验存在）
- `blogger_id`（外键校验存在）
- `platform`（Platform 枚举）
- `cooperation_date`
- `pr_id`（自动从 current_user 填入）
- `quote_amount` 必填（创建时若 blogger.quote 不为 NULL 则快照，否则要求 PR 显式提供）

可选：scheduled_publish_date / sku_id / quote_amount 可手填覆盖快照值 / cost_snapshot / 等其他

### BR-U04-11 — 引用完整性
- `style_id` 必须存在 + `is_deleted=false`
- `blogger_id` 必须存在 + `is_deleted=false`
- `sku_id`（若提供）必须存在 + `style_id` 与 promotion.style_id 一致
- 缺失 → `422 INVALID_<X>_REFERENCE`

### BR-U04-12 — 快照字段填充（service 层）
- `style_code_snapshot` = style.style_code
- `style_short_name_snapshot` = style.short_name OR style.style_name（fallback）
- `quote_amount` = blogger.quote（创建时；若 PR 显式传值则用 PR 值）
- `cost_snapshot` = sum(sku.cost_price for relevant sku)（若 sku_id 提供）；否则 NULL

---

## 3. 状态机规则（3 个并行）

### BR-U04-20 — publish_status 转移表

| from | event | to | 触发者 | 校验 |
|---|---|---|---|---|
| 未发布 | publish | 已发布 | PR | publish_url 必填 + 合法 URL；actual_publish_date 必填；同事务推进 settlement_status="未核查"→"待核查"；发 PromotionPublished 事件 |
| 未发布 | cancel | 已取消 | PR | cancel_reason 必填 |
| 未发布 | mark_abnormal | 异常 | admin / pr_manager | reason 必填 |
| 已发布 | mark_abnormal | 异常 | admin / pr_manager | — |
| 已发布 | cancel | （拒绝） | — | 返回 422 `CANCEL_NOT_ALLOWED_FOR_PUBLISHED`，提示走召回 |
| 异常 | restore | 未发布 | admin | — |

非法转移 → `422 ILLEGAL_STATE_TRANSITION` + 错误详情包含 `from / to` 状态。

### BR-U04-21 — recall_status 转移表

| from | event | to | 前置条件 | 触发者 |
|---|---|---|---|---|
| 未召回 | start_recall | 召回中 | publish_status ∈ {已发布, 已取消} | PR |
| 召回中 | recall_success | 召回成功 | — | PR / pr_manager |
| 召回中 | recall_failure | 召回失败 | — | PR |
| 召回失败 | start_recall | 召回中 | — | PR（可重新发起） |
| 召回成功 | （终态） | — | — | — |

### BR-U04-22 — settlement_status 转移表

| from | event | to | 前置条件 | 触发者 |
|---|---|---|---|---|
| 未核查 | auto_advance（publish 触发） | 待核查 | publish_status="已发布" 同事务 | system |
| 待核查 | approve | 待付款 | publish_status="已发布"；reviewer 必须 ≠ pr_id（不能自审） | pr_manager |
| 待核查 | reject | 已驳回 | review_reason 必填 | pr_manager |
| 已驳回 | (PR 修改后)/(系统重新推进) | 待核查 | — | PR / system |
| 待付款 | mark_paid | 已付款 | (U05 settlement 状态推进时同步) | (U05 反向通知，U04 监听) |

> 注：approve 后**额外发 SettlementRequested 事件**，被 U05 监听创建 settlement。U04 不创建 settlement 记录。

### BR-U04-23 — 状态机非法转移处理
- 抛 `IllegalStateTransitionError(from_state, to_state, event)` → `422`
- audit_log 记录尝试（即使失败也要记日志，便于追踪滥用）

### BR-U04-24 — 跨状态机校验
- 启动 recall 前：publish_status ∈ {已发布, 已取消}（否则 422）
- 推进 settlement_status approve 前：publish_status="已发布"（否则 422）
- 跨校验在状态机基类 `from_state_check` 钩子实施

---

## 4. 实时计算字段

### BR-U04-30 — urge_status 计算（EP05-S06）

#### 计算逻辑
```python
def calculate_urge_status(
    publish_status: str,
    scheduled_publish_date: date | None,
    today: date,
    urge_threshold_days: int = 10,
    important_threshold_days: int = 3,
) -> str:
    if publish_status == "已取消":
        return "已取消"
    if publish_status == "已发布":
        return "已发布"
    if publish_status not in {"未发布", "异常"}:
        return "已删除"  # 兜底
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
```

#### SQL 表达式（用于列表 CTE）
```sql
CASE
  WHEN publish_status = '已取消' THEN '已取消'
  WHEN publish_status = '已发布' THEN '已发布'
  WHEN scheduled_publish_date IS NULL THEN '未排期'
  WHEN (scheduled_publish_date - CURRENT_DATE) > :urge_days THEN '档期内'
  WHEN (scheduled_publish_date - CURRENT_DATE) > :important_days THEN '催发'
  WHEN (scheduled_publish_date - CURRENT_DATE) >= 0 THEN '重要催发'
  ELSE '超时'
END AS urge_status
```

#### 一致性测试
单元测试 `test_urge_calculator_python_vs_sql` — 100 个 mock 数据，对比两实现结果一致。

### BR-U04-31 — effective_like_count 计算（EP05-S10）

```python
PLATFORM_LIKE_COEFFICIENT = {
    "小红书": Decimal("1.0"),
    "抖音": Decimal("0.1"),    # 抖音 ÷ 10
    "快手": Decimal("0.1"),
    "B站": Decimal("1.0"),
}

def calculate_effective_like_count(
    platform: str, like_count: int | None
) -> int | None:
    if like_count is None:
        return None
    coefficient = PLATFORM_LIKE_COEFFICIENT.get(platform, Decimal("1.0"))
    return int((Decimal(like_count) * coefficient).to_integral_value(ROUND_HALF_UP))
```

**历史不重算策略**：不持久化 effective_like_count；每次响应实时计算。系数调整后所有 promotion 的 effective_like_count 显示按新系数；但 cost_snapshot 是创建时快照不变。

### BR-U04-32 — is_hit 计算（EP05-S11）

```python
HIT_THRESHOLD_LIKE_COUNT = 1000  # 系统设置（V1 改读 system_setting）

def calculate_is_hit(like_count: int | None) -> bool:
    if like_count is None:
        return False
    return like_count >= HIT_THRESHOLD_LIKE_COUNT
```

阈值调整后实时按新阈值计算（与 effective_like_count 一致）。

### BR-U04-33 — cpl 计算（EP05-S12）

```python
def calculate_cpl(
    quote_amount: Decimal,
    effective_like_count: int | None,
) -> Decimal | None:
    if effective_like_count is None or effective_like_count == 0:
        return None
    return (quote_amount / Decimal(effective_like_count)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
```

精度 DECIMAL(10, 4)；零分母返回 None（前端展示 "—"）。

### BR-U04-34 — dual_platform 计算

```sql
-- SQL 表达式（同 style_id 多 platform 视为 dual_platform）
EXISTS (
  SELECT 1 FROM promotion p2
  WHERE p2.tenant_id = p.tenant_id
    AND p2.style_id = p.style_id
    AND p2.platform != p.platform
    AND p2.publish_status NOT IN ('已删除', '异常')
    AND p2.is_active = true
) AS dual_platform
```

不持久化；列表 CTE 计算。

---

## 5. 重复检测与提示

### BR-U04-40 — 同款博主重复检测（EP05-S04）

#### 活跃 promotion 定义
- 同 (style_id, blogger_id) 组合
- `publish_status` ∈ {未发布, 已发布}
- `is_active = true`

#### 处理流程
- 创建路径正常成功
- response 含 `warnings` 数组（不阻塞）：
  ```json
  {
    "data": {... promotion 完整字段 ...},
    "warnings": [
      {
        "code": "DUPLICATE_PROMOTION",
        "message": "该博主已有相同款式的活跃合作",
        "details": {
          "existing_promotion_id": "...",
          "existing_internal_code": "DE2605260001"
        }
      }
    ]
  }
  ```
- 前端自行决定是否提示用户

---

## 6. 字段级权限（U02/U03 模式延续 / U09 落细）

### BR-U04-50 — 模块权限矩阵

| 角色 | promotion:read | promotion:write | promotion:delete | promotion:review |
|---|---|---|---|---|
| admin | ✅ | ✅ | ✅ | ✅ |
| pr | ✅ | ✅ | ❌ | ❌ |
| pr_manager | ✅ | ✅ | ✅ | ✅ |
| finance | ✅ | ❌ | ❌ | ❌ |
| 其他（merchandiser/designer/operations 等） | ✅（只读） | ❌ | ❌ | ❌ |

> 注：`promotion:review` 是新增的细粒度权限（PR 主管审核专用）。

### BR-U04-51 — 字段级读权限矩阵（金额字段）

| 角色 | quote_amount | cost_snapshot |
|---|---|---|
| admin / pr / pr_manager / finance | ✅ | ✅ |
| 其他角色（含 merchandiser / designer / operations） | ❌ | ❌ |

### BR-U04-52 — 字段级写权限

`quote_amount` / `cost_snapshot` 仅 admin / pr / pr_manager 可写（finance 仅读）。

实施常量（位于 `modules/promotion/legacy_field_permissions.py`）：
```python
AMOUNT_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager", "finance"})
AMOUNT_WRITABLE_ROLES = frozenset({"admin", "pr", "pr_manager"})
```

带 `# TODO U09` 注释。模式与 U02/U03 完全一致。

---

## 7. 审计触发条件

### BR-U04-60 — Promotion 编辑触发审计的字段（敏感字段白名单）

#### 写 audit + 真实 before/after
- `internal_code`（极少变更）
- `publish_status` / `recall_status` / `settlement_status`（状态推进）
- `cancel_reason` / `recall_reason` / `review_action` / `review_reason`
- `actual_publish_date` / `publish_url`

#### 写 audit + 脱敏标记
- `quote_amount` → `quote_amount_changed: true`
- `cost_snapshot` → `cost_snapshot_changed: true`

#### 不写 audit
- `like_count`（U13 自动采集变更频繁，audit 噪音）
- `note_title` / `remark` / `is_active` / `scheduled_publish_date`

### BR-U04-61 — 创建 promotion 写 audit
- action = `promotion.create`
- after 仅记 `internal_code` + `style_id` + `blogger_id` + `platform` + `cooperation_date`（金额脱敏）

### BR-U04-62 — 状态推进写专门 action
- `promotion.publish` / `promotion.cancel` / `promotion.recall_start` / `promotion.recall_success` / `promotion.recall_failure`
- `promotion.review_approve` / `promotion.review_reject`
- `promotion.settlement_status.auto_advance`（publish 同事务自动推进）

### BR-U04-63 — like_count 变更分两类
- 用户编辑 → action = `promotion.like_count_updated_by_user`
- 采集 worker → action = `promotion.like_count_updated_by_crawler`（actor_type="worker"）
- like_count 在白名单内但不脱敏（非敏感数值）

### BR-U04-64 — 自审禁止
- pr_manager review approve/reject 自己创建的 promotion → 422 `SELF_REVIEW_NOT_ALLOWED`

---

## 8. SettlementRequested 事件契约

### BR-U04-70 — 事件发布时机
- 仅在 `settlement_status` 从 `"待核查"` 转移到 `"待付款"`（即 review approve）时发出
- 同事务发出（U04 commit 前 U05 已创建 settlement）

### BR-U04-71 — 事件 payload 完整性
event 必须包含：
- `event_id` (UUID4 新生成，每次审核一个新值)
- `timestamp` (datetime)
- `tenant_id`
- `promotion_id` / `promotion_internal_code`
- `blogger_id` / `style_id`
- `amount` (= promotion.quote_amount)
- `requested_by` (= pr_manager.id)
- `requested_at` (= promotion.reviewed_at)

### BR-U04-72 — 幂等保证
- U04 端：每次审核新 event_id（即使重复触发审核，event 不同）
- U05 端：DB UNIQUE(promotion_id) 约束兜底；service 层先 SELECT 检查
- 事件总线本地内存（同事务），无重复投递问题

### BR-U04-73 — 事件失败处理
- 监听器（U05）抛异常 → 整个事务回滚（U04 review 不成功，promotion.settlement_status 不前进）
- 这是同事务事件总线的优势

---

## 9. 列表查询与分页

### BR-U04-80 — 列表筛选参数
- `keyword`：ILIKE 模糊匹配 internal_code / style_code_snapshot / style_short_name_snapshot / publish_url
- `pr_id` / `blogger_id` / `style_id`
- `platform`
- `publish_status` / `recall_status` / `settlement_status`
- `urge_status`（CTE 计算列）
- `cooperation_date_from` / `cooperation_date_to`
- `is_hit`（计算列）
- `dual_platform`（计算列）
- `is_active`（默认 true）

排序：默认 `cooperation_date DESC, created_at DESC`，可选 `like_count DESC`、`cpl ASC`（计算列排序）。

分页：page / page_size（默认 1/20，max 100）。

### BR-U04-81 — 字段过滤
- 列表与详情接口共享同一个 `_to_response` 字段过滤函数（避免列表能看到详情看不到的歧义）
- 计算字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）按响应一并返回

---

## 10. 错误码矩阵

| 场景 | HTTP | code |
|---|---|---|
| internal_code 重复（理论不应触发，序列号防 race） | 409 | `INTERNAL_CODE_CONFLICT` |
| 当天 sequence > 9999 | 409 | `SEQUENCE_OVERFLOW` |
| style_id 不存在 | 422 | `INVALID_STYLE_REFERENCE` |
| blogger_id 不存在 | 422 | `INVALID_BLOGGER_REFERENCE` |
| sku_id 不存在 / 与 style_id 不一致 | 422 | `INVALID_SKU_REFERENCE` |
| publish 时 publish_url 缺失或非法 | 422 | `INVALID_PUBLISH_URL` |
| cancel 已发布 promotion | 422 | `CANCEL_NOT_ALLOWED_FOR_PUBLISHED` |
| 状态机非法转移 | 422 | `ILLEGAL_STATE_TRANSITION`（带 from/to） |
| pr_manager 自审 | 422 | `SELF_REVIEW_NOT_ALLOWED` |
| reject 缺 review_reason | 422 | `REVIEW_REASON_REQUIRED` |
| 字段写权限拒绝（quote_amount） | 403 | `FIELD_PERMISSION_DENIED` |
| 模块权限拒绝 | 403 | `PERMISSION_DENIED` |
| promotion 不存在 | 404 | `PROMOTION_NOT_FOUND` |
| 启动 recall 时 publish_status 不在允许集 | 422 | `RECALL_NOT_ALLOWED` |
| Pydantic 校验失败 | 422 | `VALIDATION_ERROR` |

---

## 11. 性能 / 容量预估

| 指标 | 预估 | 说明 |
|---|---|---|
| Promotion 行数 / 租户 | MVP 2 万；V1 10 万；V2+ 50 万 | 业务文档基线 5494 × 4 倍冗余 ~= 2 万 |
| GET /api/promotions/ 列表 P95 | ≤ 300ms | CTE 计算 urge_status + 多筛选 |
| 创建 P95 | ≤ 300ms | 含序列号锁 + 快照 + 重复检测 |
| 状态推进 P95 | ≤ 200ms | 含状态机校验 + audit + 事件触发 |
| Review approve P95 | ≤ 300ms | 含状态推进 + 事件分发 + U05 同事务创建 settlement |

---

## 12. 与后续单元的契约

| 单元 | 引用方式 | 契约要求 |
|---|---|---|
| U05 财务结款 | 监听 SettlementRequested 事件 | promotion_id 不变 / amount = quote_amount 快照 |
| U07 企微 | 监听 PromotionPublished 事件 | publish_url 不变 |
| U08 发文看板 | 查询 promotion publish_status / urge_status | 字段稳定 |
| U13 数据采集 | 调用 `update_like_count(promotion_id, like_count, source="crawler")` 内部 API | 接口稳定 |
| U14 投产报表 | 聚合 promotion.cost_snapshot + 实时 cpl | 快照字段不漂移 |
| U16 拍单刷单 | 关联 promotion.id 为订单分类依据 | promotion.id 稳定 |
| U09 字段级权限 | grep `legacy_field_permissions` 替换 | 一文件清理点 |

---

## 13. 一致性校验

| 校验 | 结果 |
|---|---|
| 3 状态机 transition table 完整 + 跨状态校验明确 | ✅ |
| internal_code 序列号防 race（行级锁 + 同事务） | ✅ |
| 衍生字段不持久化（避免数据漂移） | ✅ |
| 字段权限模式与 U02/U03 一致 | ✅ |
| 审计敏感字段脱敏（quote_amount/cost_snapshot） | ✅ |
| SettlementRequested 事件 payload 完整含幂等键 | ✅ |
| pr_manager 自审禁止 | ✅ |
| publish 同事务推进 settlement_status="待核查" | ✅ |
| 错误码与 U01 错误码体系一致 | ✅ |
| 与 U05/U07/U13/U14 契约预留 | ✅ |
