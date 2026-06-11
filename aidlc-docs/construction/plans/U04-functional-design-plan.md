# U04 功能设计计划（Functional Design Plan）

> 单元：U04 — 推广合作核心  
> 阶段：MVP 第 4 个单元（关键路径核心）  
> 依赖：U01 + U02 + U03 全部就绪

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 | 说明 |
|---|---|---|
| EP05-S02 | MVP | PR 创建推广合作（含 internal_code 生成 + 必填校验） |
| EP05-S03 | MVP | 自动按款号填充商品简称（复用 EP02-S06） |
| EP05-S04 | MVP | 同款博主重复检测 |
| EP05-S05 | MVP | 双平台标记 |
| EP05-S06 | MVP | 实时计算催发状态（5 状态：未排期/档期内/催发/重要催发/超时 + 已发布/已取消） |
| EP05-S07 | MVP | PR 填入发布链接 → publish_status 推进到"已发布" |
| EP05-S08 | MVP | PR 取消合作 → publish_status 推进到"已取消" |
| EP05-S09 | MVP | PR 发起召回 → recall_status 状态机 |
| EP05-S10 | MVP | 系统计算有效点赞量（平台折算系数） |
| EP05-S11 | MVP | 爆文标记（实时阈值计算） |
| EP05-S12 | MVP | 单件点赞成本 CPL 计算 |
| EP05-S13 | MVP | PR 主管审核 → 推进 settlement_status="待核查" + 发 SettlementRequested 事件 |

### 1.2 职责边界（关键决策）

> 这是 INCEPTION 阶段已锁定决策（Findings P1：解决 U04/U05 循环依赖）

**U04 的职责**：
- promotion CRUD + 3 个并行状态机（publish / recall / settlement_request）
- 实时计算字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）
- PR 主管审核：仅推进 `promotion.settlement_status` 到 `"待核查"` + 发 `SettlementRequested(promotion_id, amount, ...)` 领域事件
- **不直接创建 settlement 记录**（避免与 U05 职责循环）

**U05 的职责（U04 不做）**：
- 监听 `SettlementRequested` 事件，**创建 settlement 记录**
- settlement 全生命周期管理

> ⚠️ EP05-S13 和 EP06-S02 故事文本写"自动生成 settlement 记录"，与 INCEPTION 决策**有出入**。本 U04 单元按 INCEPTION 决策实施（事件驱动），故事文本在 U05 实施时同步修正。

### 1.3 覆盖代码
- `backend/app/modules/promotion/`（核心业务模块）
  - `enums.py`、`models.py`、`schemas.py`、`permissions.py`
  - `legacy_field_permissions.py`（quote_amount / payment_amount 等敏感字段）
  - `exceptions.py`、`domain.py`、`repository.py`、`service.py`
  - `state_machines.py`（3 个并行状态机定义 + transition table）
  - `urge_calculator.py`（UrgeStatusCalculator：Service + SQL 表达式双实现）
  - `metrics_calculator.py`（effective_like_count / is_hit / cpl）
  - `events.py`（领域事件 SettlementRequested 定义）
  - `deps.py`、`api.py`
- 可能修改 `backend/app/core/state_machine.py`（U01 已建基类，本单元首次实战使用）
- 可能新增 `backend/app/services/metric/publish_progress.py`（部分指标，与 U07/U14 共用）

### 1.4 依赖关系
- 强依赖 U02：`promotion.style_id` FK + 创建时**快照** style.short_name / 总成本
- 强依赖 U03：`promotion.blogger_id` FK + 创建时**快照** blogger.quote 到 `promotion.quote_amount`
- 强依赖 U01：core/state_machine.py（首次实战使用）

### 1.5 设计阶段后产出文档
- `aidlc-docs/construction/U04/functional-design/domain-entities.md`
- `aidlc-docs/construction/U04/functional-design/business-rules.md`
- `aidlc-docs/construction/U04/functional-design/business-logic-model.md`

---

## 2. 计划步骤

### Step 1 — 确认范围
- [x] 1.1 读取 unit-of-work U04 定义
- [x] 1.2 读取 stories EP05-S02~S13 完整验收
- [x] 1.3 标记 U04/U05 职责边界（事件驱动，不创建 settlement）
- [x] 1.4 标记 U07 (企微通知) / U10b (智能标签) 范围排除

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出覆盖故事与职责边界
- [x] 2.2 列出问题分类
- [x] 2.3 等待用户填答 [Answer]

### Step 3 — 生成 domain-entities.md
- [x] 3.1 Promotion 实体字段表（含 3 个 status + 实时计算字段）
- [x] 3.2 PromotionReview 实体（PR 主管审核记录）
- [x] 3.3 4 个 Python Enum（PublishStatus / RecallStatus / SettlementStatus / Platform）
- [x] 3.4 ER 图（Mermaid）
- [x] 3.5 索引清单（含 GIN / 范围索引）
- [x] 3.6 演化路线（U05 / U07 / U10b / U14 引用）

### Step 4 — 生成 business-rules.md
- [x] 4.1 internal_code 生成规则
- [x] 4.2 唯一性 + 必填 + 引用完整性
- [x] 4.3 3 个状态机的 transition table
- [x] 4.4 实时计算字段的公式与触发时机
- [x] 4.5 爆文阈值 / 平台折算系数 / 催发天数（系统设置）
- [x] 4.6 重复检测 + 双平台标记规则
- [x] 4.7 字段级权限（quote_amount / payment_amount 等）
- [x] 4.8 SettlementRequested 事件契约
- [x] 4.9 错误码矩阵

### Step 5 — 生成 business-logic-model.md
- [x] 5.1 创建 promotion UC（含快照 + 重复检测 + 自动填充）
- [x] 5.2 publish UC（PR 填发布链接 → 状态推进）
- [x] 5.3 cancel UC（PR 取消 → 状态约束）
- [x] 5.4 recall UC（召回流程）
- [x] 5.5 review UC（PR 主管审核 → 发事件）
- [x] 5.6 metrics 实时计算流程（urge_status / cpl / is_hit）
- [x] 5.7 列表查询 + 多筛选

### Step 6 — 提交完成消息 + 等待审批

---

## 3. 澄清问题（请填 [Answer]）

> U04 是 MVP 关键路径核心，复杂度显著高于 U02/U03。22 个澄清问题，每问预填合理默认值。

### 3.1 internal_code 生成

**Q1**：`internal_code` 生成规则？

[Answer]: 格式 `<tenant_prefix><yyMMdd><sequence>`：
- `tenant_prefix`：tenant.code 前 2 位大写（如 "DE" for default）；若 tenant.code 不足 2 字符则补 `X`
- `yyMMdd`：合作日期 cooperation_date（不是创建时间）
- `sequence`：4 位数序列号，按 (tenant_id, cooperation_date) 当天累加（从 0001 开始）
- 示例：`DE2605260001`
- **唯一约束**：`UNIQUE (tenant_id, internal_code)`（U02 风格 partial unique；本单元 promotion 不软删，标准 unique 即可）

实施方式：service 层使用 PostgreSQL `LOCK TABLE promotion IN SHARE ROW EXCLUSIVE MODE` 或基于序列表（避免并发 race）。**优先方案**：建独立 `promotion_seq` 表 `(tenant_id, date_key, last_seq)` + 行级锁 `SELECT ... FOR UPDATE` 计算下一序列号。

### 3.2 Promotion 实体字段范围

**Q2**：Promotion 表 MVP 字段范围？

[Answer]: 完整字段表（28 字段）：

**关联**：
- `id` (UUID PK) / `tenant_id` (UUID FK)
- `style_id` (UUID FK to style)
- `sku_id` (UUID FK to sku, 可选 — 若推广是单一款式按 sku 拆分则填，否则按 style 聚合)
- `blogger_id` (UUID FK to blogger)
- `pr_id` (UUID FK to user, 创建该 promotion 的 PR)

**业务键**：
- `internal_code` (VARCHAR(64), UNIQUE per tenant)

**快照字段**（U02/U03 决策的"快照不重算"原则）：
- `style_code_snapshot` (VARCHAR(64))：创建时从 style 复制
- `style_short_name_snapshot` (VARCHAR(128))：创建时从 style.short_name 或 style_name 复制
- `quote_amount` (DECIMAL(10,2))：合作报价（创建时从 blogger.quote 快照，后续可手动调整）
- `cost_snapshot` (DECIMAL(10,2))：总成本快照（创建时按 sku.cost_price * 数量 聚合）

**业务字段**：
- `platform` (VARCHAR(16))：平台枚举（小红书/抖音/快手/B站）
- `cooperation_date` (DATE)：合作日期
- `scheduled_publish_date` (DATE, 可选)：预定发布日期
- `actual_publish_date` (DATE, 可选)：实际发布日期
- `publish_url` (VARCHAR(512), 可选)
- `cancel_reason` (TEXT, 可选)
- `recall_reason` (TEXT, 可选)
- `like_count` (INT, 可选)：原始点赞量（PR 录入或 U13 自动采集）
- `note_title` (VARCHAR(255), 可选)
- `remark` (TEXT, 可选)

**3 个状态字段**：
- `publish_status` (VARCHAR(16))：5 值（未发布 / 已发布 / 已取消 / 异常 / 已删除）
- `recall_status` (VARCHAR(16), 可选)：3 值（未召回 / 召回中 / 召回成功 / 召回失败）默认 `'未召回'`
- `settlement_status` (VARCHAR(16))：5 值（未核查 / 待核查 / 待付款 / 已付款 / 已驳回）默认 `'未核查'`

**审核相关**：
- `reviewed_by` (UUID FK to user, 可选)
- `reviewed_at` (TIMESTAMPTZ, 可选)
- `review_action` (VARCHAR(16), 可选)：approve / reject
- `review_reason` (TEXT, 可选)

**通用**：
- `is_active` (BOOLEAN)：是否启用（软停用，与 publish_status="已删除" 区分）
- `created_at` / `updated_at` (TIMESTAMPTZ)

不含的字段（V1+ / 其他单元）：
- `dual_platform` 不存表，按 SQL 实时计算
- `urge_status` 不存表，按 SQL 实时计算（带索引视图）
- `is_hit` 不存表，按 SQL 实时计算
- `effective_like_count` 不存表，按 SQL 实时计算
- `cpl` 不存表，按 SQL 实时计算

> 决策：所有衍生字段不持久化（避免数据漂移），通过 SQL 表达式实时计算 + service 层补充 Pydantic response

**Q3**：是否需要 `PromotionReview` 子表（独立审核记录历史）？

[Answer]: **不需要独立子表** — promotion 表已含 `reviewed_by` / `reviewed_at` / `review_action` / `review_reason` 4 个字段；审核操作的历史变更（如先 reject 后 re-approve）通过 audit_log 追溯（U01 已实现）。
- 后续若 V1+ 需要审核流程委派多人审批 → 引入独立 PromotionReview 子表（届时数据迁移）

### 3.3 状态机定义

**Q4**：3 个状态机的 transition table？

[Answer]:

#### publish_status（5 状态）

| from | event | to | 触发者 | 校验 |
|---|---|---|---|---|
| 未发布 | publish | 已发布 | PR | publish_url 必填 + 合法 URL；actual_publish_date 必填 |
| 未发布 | cancel | 已取消 | PR | cancel_reason 必填 |
| 未发布 | mark_abnormal | 异常 | admin / pr_manager | reason 必填 |
| 已发布 | recall | （触发 recall_status） | PR | 进入召回流程，publish_status 不变 |
| 已发布 | mark_abnormal | 异常 | admin / pr_manager | — |
| 已取消 | (终态) | — | — | — |
| 异常 | restore | 未发布 | admin | — |

#### recall_status（3 状态 + 默认）

| from | event | to | 触发者 |
|---|---|---|---|
| 未召回 | start_recall | 召回中 | PR (publish_status="已发布" 或 "已取消") |
| 召回中 | recall_success | 召回成功 | PR / pr_manager |
| 召回中 | recall_failure | 召回失败 | PR |
| 召回失败 | start_recall | 召回中 | PR（可重新发起） |
| 召回成功 | (终态，不可逆) | — | — |

#### settlement_status（5 状态）

| from | event | to | 触发者 | 校验 |
|---|---|---|---|---|
| 未核查 | request_review | 待核查 | PR (publish_status="已发布") | — |
| 待核查 | approve | 待付款 | pr_manager | **同时发 SettlementRequested 事件** |
| 待核查 | reject | 已驳回 | pr_manager | review_reason 必填 |
| 已驳回 | (PR 修改后) | 待核查 | PR | — |
| 待付款 | mark_paid | 已付款 | (U05 settlement 状态推进时同步) | — |

> 注：`settlement_status="未核查"` 是创建时初始值；当 PR 通过 publish 接口推进 `publish_status="已发布"` 时**自动转换** `settlement_status="未核查"` → `"待核查"`（自动 transition，详见 EP05-S07 流程）。

**Q5**：3 个状态机是否相互独立？是否存在跨状态机校验？

[Answer]: 
- **publish_status 是主线**，其他两个状态机受其约束：
  - 只有 `publish_status="已发布"` 或 `"已取消"` 才能启动 recall_status 转移
  - 只有 `publish_status="已发布"` 才能将 settlement_status 从"未核查"推进到"待核查"
- **recall_status 与 settlement_status 完全独立**（召回和结算可并行进行）
- 跨校验在 service 层 + 状态机基类的 `from_state_check` 钩子实现

### 3.4 实时计算字段（关键）

**Q6**：`urge_status` 5 状态计算逻辑？是否需要 SQL 表达式 + Service 双实现？

[Answer]: 是 — 双实现避免数据漂移：

#### 计算逻辑（按 EP05-S06）

```python
# UrgeStatusCalculator.calculate(promotion, today, urge_threshold_days, important_threshold_days)
# 默认 urge_threshold_days=10, important_threshold_days=3

if publish_status == "已取消":
    return "已取消"
if publish_status == "已发布":
    return "已发布"
# publish_status == "未发布"
if scheduled_publish_date is None:
    return "未排期"
diff = (scheduled_publish_date - today).days
if diff > urge_threshold_days:        # 例如 today < scheduled - 10
    return "档期内"
elif diff > important_threshold_days: # 例如 scheduled - 10 ≤ today < scheduled - 3
    return "催发"
elif diff >= 0:                       # 例如 scheduled - 3 ≤ today ≤ scheduled
    return "重要催发"
else:
    return "超时"
```

#### SQL 表达式（用于列表查询时附加 urge_status 列，避免 N+1）

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

#### 双实现一致性测试
- 单元测试：`test_urge_calculator_python_vs_sql` — 100 个 mock 数据，对比 Python 计算结果与 SQL 计算结果一致

**Q7**：`urge_status` 是否需要支持 `?urge_status=催发` 筛选？

[Answer]: 是 — 列表接口支持按 urge_status 筛选。实现方式：在 SQL 表达式外层 wrap 子查询，或用 `WITH urge_view AS (SELECT ... CASE ...)` CTE。性能预估：promotion 表 ≤ 10 万 / 租户，CTE 计算 P95 ≤ 300ms 可接受。索引帮助：`(tenant_id, publish_status, scheduled_publish_date)` 复合索引。

**Q8**：`effective_like_count` 平台折算系数从哪里读？

[Answer]: 系统设置（system_setting 表）。U04 阶段：
- 暂不创建 system_setting 表（避免 U04 范围蔓延）
- service 层硬编码默认系数（与 U03 hardcode 模式一致）：
  ```python
  # modules/promotion/legacy_settings.py
  PLATFORM_LIKE_COEFFICIENT: dict[str, Decimal] = {
      "小红书": Decimal("1.0"),
      "抖音": Decimal("0.1"),       # 抖音点赞 ÷ 10
      "快手": Decimal("0.1"),
      "B站": Decimal("1.0"),
  }
  HIT_THRESHOLD_LIKE_COUNT: int = 1000  # 爆文阈值
  URGE_THRESHOLD_DAYS: int = 10
  IMPORTANT_THRESHOLD_DAYS: int = 3
  ```
- 标记 `# TODO U07/U14: 改读 system_setting 表`
- 配套 V1 单元（U07 或独立 system_setting 单元）实施动态配置后清理

**Q9**：`effective_like_count` 计算时机和"历史不重算"如何实现？

[Answer]:
- 编辑 promotion 时：若 like_count / platform 变更 → service 层**重新计算** effective_like_count（取当前生效系数）
- 历史 promotion 不重算：因为 effective_like_count 不持久化；每次查询时按 (current_platform_coefficient, like_count) 实时计算
- "历史不重算"语义改为："系数调整后，所有 promotion 的 effective_like_count 显示按新系数；但 cpl 计算口径中的成本 cost_snapshot 是创建时快照，不变"
- 决策一致性测试：单元测试 `test_effective_like_count_uses_current_coefficient` + `test_cost_snapshot_does_not_recalculate`

**Q10**：`cpl` 计算公式和零分母处理？

[Answer]:
- `cpl = quote_amount / effective_like_count`（不是 cost_snapshot）
- effective_like_count == 0 或 None → cpl = None
- effective_like_count > 0 但 quote_amount = 0 → cpl = 0（合规）
- service 层在 to_response 时计算并填入响应；不建表字段
- DECIMAL 精度：DECIMAL(10, 4)（4 位小数足够 ROI 比对）

**Q11**：`is_hit` 阈值变更时如何影响历史数据？

[Answer]: 与 effective_like_count 一致 — 不持久化，按当前阈值实时计算。EP05-S11 验收"阈值调整重新查询按新阈值实时计算" ✅。

### 3.5 重复检测与双平台

**Q12**：EP05-S04 重复检测的"活跃记录"定义？

[Answer]: 满足以下全部条件的 promotion 视为活跃：
- 同 (style_id, blogger_id) 组合
- `publish_status` ∈ {未发布, 已发布}（不含已取消 / 异常 / 已删除）
- `is_active = true`

实施：service 层在 create 路径中查询，命中时返回 `warning` 字段（不阻塞）：
```json
{
  "data": {... 创建成功的 promotion ...},
  "warnings": [
    {
      "code": "DUPLICATE_PROMOTION",
      "message": "该博主已有相同款式的活跃合作",
      "details": {"existing_promotion_id": "..."}
    }
  ]
}
```

但 EP05-S04 验收说"返回 warning（不阻塞），前端弹出确认对话框" — 业务流程为：
- 第一次创建 → 返回 200/201 + warnings（前端展示对话框）
- 用户确认后 → 前端**重新调用** POST 带 `?confirm_duplicate=true` 参数 → 不再返回 warning，正常创建

或者简化为：
- 创建路径**始终成功**（除非校验错误）
- warning 信息一并返回，前端自行决定是否提示
- 不做 confirm 二次提交（简化交互）

**实际选用**：简化方案。warnings 是"提示性返回"，不阻塞创建。

### 3.6 settlement_status 自动推进

**Q13**：当 PR 调用 `PUT /api/promotions/{id}/publish` 推进 `publish_status="已发布"` 时，是否同时自动推进 `settlement_status="未核查" → "待核查"`？

[Answer]: 是 — 同事务内推进。具体逻辑：
- 在 `PromotionService.publish()` 内部，先推进 publish_status，然后**同事务**推进 settlement_status（仅当当前 settlement_status="未核查"）
- audit_log 写两条：`promotion.publish` + `promotion.settlement_status.auto_advance`
- 这避免 PR 主管打开列表时看到"已发布但 settlement_status=未核查"的歧义状态

### 3.7 SettlementRequested 事件契约

**Q14**：SettlementRequested 事件的字段 + 发布通道？

[Answer]:

事件 payload：
```python
@dataclass
class SettlementRequested:
    event_type: str = "SettlementRequested"
    event_id: UUID                  # 幂等键
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal                 # = promotion.quote_amount
    requested_by: UUID              # = pr_manager.id (审核人)
    requested_at: datetime          # = promotion.reviewed_at
```

发布通道：
- **MVP 阶段使用本地内存事件总线**（`core/events.py` 内简单 dispatcher，同步触发）
- U05 监听器：`@event_handler("SettlementRequested")` 装饰 SettlementService.create_from_event
- 同事务内执行（U04 commit 前 U05 已创建 settlement，事务一致）
- V1+ 评估升级到 Celery / Redis Streams（解耦时机）

**幂等保证**：
- U04 发事件时 event_id 由 UUID4 生成
- U05 创建 settlement 表加 UNIQUE(promotion_id) 约束 — DB 层兜底
- 同时 U05 service 层先 SELECT 检查 promotion_id 是否已有 settlement，已有则跳过

### 3.8 字段级权限

**Q15**：U04 敏感字段权限矩阵？

[Answer]: 
- `quote_amount` / `cost_snapshot`（金额字段）：admin / pr / pr_manager / finance 可见可写（PR 创建后可调整 quote_amount）；其他角色不可见
- `like_count` / `note_title` / `publish_url`：所有有 `promotion:read` 权限的角色可见
- 不含 wechat / phone（promotion 表无联系人字段，那些字段在 blogger 表）

实施位置：`modules/promotion/legacy_field_permissions.py`：
```python
AMOUNT_VISIBLE_ROLES = frozenset({"admin", "pr", "pr_manager", "finance"})
AMOUNT_WRITABLE_ROLES = frozenset({"admin", "pr", "pr_manager"})  # finance 仅读
```

模式与 U02/U03 完全一致；TODO U09 清理。

### 3.9 列表查询能力

**Q16**：列表接口支持哪些筛选？

[Answer]:
- `keyword`：ILIKE 模糊匹配 internal_code / style_code_snapshot / style_short_name_snapshot / publish_url（OR 关系）
- `pr_id`：按 PR 筛选
- `blogger_id`：按博主筛选
- `style_id`：按款式筛选
- `platform`：按平台筛选
- `publish_status` / `recall_status` / `settlement_status`：按状态筛选
- `urge_status`（CTE 计算列）：按催发状态筛选
- `cooperation_date_from` / `cooperation_date_to`：合作日期范围
- `is_hit`：仅看爆文（计算列）
- `dual_platform`：仅看双平台（计算列）
- 排序：默认 `cooperation_date DESC, created_at DESC`，可选 `like_count DESC`、`cpl ASC`
- 分页：page / page_size（max 100）

### 3.10 与 U07 / U13 / U14 的契约

**Q17**：EP05-S07 提到"触发企微发文通知（EP08-S09）"，U04 是否实施？

[Answer]: U04 不实施企微通知，仅发出领域事件 `PromotionPublished(promotion_id, publish_url, ...)`，由 U07（企微集成）监听并发企微消息。U04 阶段事件可以发但**没有 listener**（U07 启用前消息丢弃）。这是事件驱动架构的演进路径。

**Q18**：U13（数据采集）后续会更新 promotion.like_count，U04 是否预留？

[Answer]: U04 提供 `PromotionService.update_like_count(promotion_id, like_count, source)` 内部 API（不暴露 HTTP），U13 通过此 API 更新。该 API：
- 写权限：仅系统/采集 worker（actor_type="worker"）可调用，PR 通过普通 PUT 也能写但走不同路径
- audit_log 区分 `promotion.like_count_updated_by_user` vs `promotion.like_count_updated_by_crawler`

**Q19**：U14（投产报表）会查询 promotion 大量数据，U04 是否需要预聚合？

[Answer]: U04 不预聚合（V1 / U14 单元负责）。U04 阶段只需保证基础查询索引就绪。

### 3.11 测试与性能

**Q20**：U04 性能 SLA？

[Answer]: 
- 列表查询（含 urge_status CTE 计算）：P95 ≤ 300ms（10 万行 / 租户）
- 详情查询：P95 ≤ 100ms
- 创建（含快照 + 重复检测 + internal_code 序列号锁）：P95 ≤ 300ms
- 状态推进（publish/cancel/recall/review）：P95 ≤ 200ms
- 性能测试：nightly 跑 + @pytest.mark.performance

**Q21**：测试覆盖关键场景？

[Answer]:
1. EP05-S02 创建 + internal_code 自动生成 + 序列号并发安全
2. EP05-S04 重复检测 warning（不阻塞）
3. EP05-S06 urge_status 7 个 GWT 场景（5 状态 + 已发布 + 已取消）
4. EP05-S06 Python 计算与 SQL 计算结果一致性
5. EP05-S07 publish + 自动推进 settlement_status="待核查"
6. EP05-S08 取消已发布 → 422（必须走召回）
7. EP05-S09 召回状态机
8. EP05-S10 平台折算系数 + 历史不重算
9. EP05-S11 爆文阈值实时计算
10. EP05-S12 cpl + 零分母 null
11. EP05-S13 审核 approve → 发 SettlementRequested 事件 + 不创建 settlement（mock 监听器验证事件参数）
12. EP05-S13 审核 reject → settlement_status="已驳回"
13. 字段权限：4 角色 × 2 字段（quote / cost_snapshot）矩阵
14. 多租户隔离回归

**Q22**：是否需要事件总线集成测试（U04 发事件，mock U05 监听）？

[Answer]: 是 — 集成测试必须包含：
- `test_review_approve_emits_settlement_requested` — mock 事件 dispatcher，断言 SettlementRequested payload 字段完整
- `test_review_approve_does_not_create_settlement` — 显式断言 settlement 表（U05 表，需 mock 或检查不存在）未被创建
- `test_settlement_requested_idempotent_via_event_id` — 重复触发事件，event_id 不同，但 U05 端通过 promotion_id UNIQUE 兜底（U05 单元测试）

---

## 4. 决策摘要（用户填答后由 AI 整理）

> 用户回复"继续"后，AI 总结所有 [Answer] 形成最终决策清单。
