# U04 API 端点总结（11 端点）

> 全部端点前缀：`/api`  
> 鉴权：`Authorization: Bearer <jwt>`  
> 错误响应统一格式：`{ "code": "...", "message": "...", "details": {...} }`（U01 全局 error handler）

---

## 1. CRUD（5 端点）

### 1.1 POST `/api/promotions/`
创建推广（EP05-S02 + S03 + S04）。

- 权限：`promotion:write`
- 请求：`PromotionCreate` — style_id / blogger_id / platform / cooperation_date / 可选 sku_id / scheduled_publish_date / quote_amount / note_title / remark
- 响应：`PromotionResponse`（含 `duplicate_warnings` 数组，重复时填入引导）
- 错误：
  - `422 INVALID_STYLE_REFERENCE` / `INVALID_BLOGGER_REFERENCE` / `INVALID_SKU_REFERENCE`
  - `422 PUBLISH_URL_REQUIRED`（blogger.quote 为空且未传 quote_amount）
  - `409 PROMOTION_SEQUENCE_OVERFLOW`（当天 > 9999）
  - `403 FIELD_PERMISSION_DENIED`（quote_amount 写权限）

### 1.2 GET `/api/promotions/`
列表 + 分页 + 衍生字段 CTE（EP05-S05 / S06）。

- 权限：`promotion:read`
- Query 参数：page / page_size / keyword / publish_status / recall_status / settlement_status / platform / blogger_id / style_id / pr_id / cooperation_date_from/to / scheduled_publish_date_from/to / is_active / only_dual_platform / is_hit
- 响应：`PromotionPage { items, total, page, page_size }`
  - items[i].urge_status / dual_platform / effective_like_count / is_hit / cpl 由后端实时计算（CTE + Python）
- 失败：业务未匹配返回 200 + 空数组；系统失败异常自然冒泡 → 5xx

### 1.3 GET `/api/promotions/{promotion_id}`
获取单条详情。

- 权限：`promotion:read`
- 响应：`PromotionResponse`
- 错误：`404 PROMOTION_NOT_FOUND`

### 1.4 PATCH `/api/promotions/{promotion_id}`
编辑（PATCH 语义；不能改状态字段，状态走专门接口）。

- 权限：`promotion:write`
- 请求：`PromotionUpdate`（部分字段）
- 响应：`PromotionResponse`
- 错误：`404` / `422` / `403 FIELD_PERMISSION_DENIED`

### 1.5 DELETE `/api/promotions/{promotion_id}`
软停用（`is_active=false`，与状态机正交）。

- 权限：`promotion:delete`
- 响应：`204 No Content`
- 错误：`404` / `409 PROMOTION_STATE_CONFLICT`

---

## 2. 状态推进（6 端点）

### 2.1 POST `/api/promotions/{promotion_id}/publish`
发布（EP05-S07）。

- 权限：`promotion:write`
- 请求：`PromotionPublishRequest` — publish_url（必填，必须 http(s)://）+ actual_publish_date（必填）
- 响应：`PromotionResponse` — publish_status="已发布" + settlement_status 自动推进到"待核查"
- 副作用：发 `PromotionPublished` 通知事件（无 listener 不阻塞）
- 错误：
  - `404 PROMOTION_NOT_FOUND`
  - `422 ILLEGAL_STATE_TRANSITION`（业务前置）
  - `409 PROMOTION_STATE_CONFLICT`（并发竞争 / 跨租户 / 软删）

### 2.2 POST `/api/promotions/{promotion_id}/cancel`
取消（EP05-S08，仅 publish_status=未发布）。

- 权限：`promotion:write`
- 请求：`PromotionCancelRequest` — cancel_reason（必填）
- 错误：`422 ILLEGAL_STATE_TRANSITION`（已发布需走召回）

### 2.3 POST `/api/promotions/{promotion_id}/recall/start`
启动召回（EP05-S09，跨状态机：要求 publish_status ∈ {已发布, 已取消}）。

- 权限：`promotion:write`
- 请求：`PromotionRecallStartRequest` — recall_reason（可选，max 2000 字）
- 错误：
  - `409 PROMOTION_STATE_CONFLICT`（publish_status 不满足前置）
  - `422 ILLEGAL_STATE_TRANSITION`（recall_status 不允许转移）

### 2.4 POST `/api/promotions/{promotion_id}/recall/success`
召回成功（终态）。

- 权限：`promotion:write`
- 请求：`PromotionRecallResultRequest` — remark（可选）

### 2.5 POST `/api/promotions/{promotion_id}/recall/failure`
召回失败（可重新发起）。

- 权限：`promotion:write`
- 请求：`PromotionRecallResultRequest` — remark（可选）

### 2.6 POST `/api/promotions/{promotion_id}/review`
PR 主管审核（EP05-S13）。

- 权限：`promotion.review:approve`
- 请求：`PromotionReviewRequest` — action（approve/reject）+ review_reason（reject 时必填）
- 响应：approve → settlement_status="待付款"；reject → settlement_status="已驳回"
- 副作用：approve 时同事务发 `SettlementRequested` 强一致事件（U05 监听创建 settlement）
- 错误：
  - `403 SELF_REVIEW_FORBIDDEN`（不能审自己提交的）
  - `422 REVIEW_REASON_REQUIRED`（reject 缺 reason）
  - `409 PROMOTION_STATE_CONFLICT`（publish_status != 已发布 或并发竞争）
  - `500 MISSING_REQUIRED_HANDLER`（U05 未部署，FB1）

---

## 3. 内部 API（不暴露 HTTP）

### `PromotionService.update_like_count(promotion_id, like_count, tenant_id, actor_user_id?)`
U13 数据采集 Worker 调用。
- actor_type="system" 写 audit
- WHERE 包含 tenant_id + is_active 防护

---

## 4. 衍生字段说明（响应内嵌）

| 字段 | 计算 | 触发系数变更后的行为 |
|---|---|---|
| `urge_status` | 7 分支 + scheduled_publish_date - today | 实时 |
| `dual_platform` | EXISTS 同 style_id 其他平台活跃推广 | 实时 |
| `effective_like_count` | like_count × 平台系数 ROUND_HALF_UP | 实时（与 cost_snapshot 不同） |
| `is_hit` | 原始 like_count >= HIT_THRESHOLD | 实时 |
| `cpl` | quote_amount / effective_like_count（4 位精度） | 实时 |

**敏感字段过滤**：`quote_amount` / `cost_snapshot` / `cpl` 在用户角色不在 `AMOUNT_VISIBLE_ROLES` 时返回 `null`（U09 后改为字段级权限）。
