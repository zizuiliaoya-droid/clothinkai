# U05 财务结款核心 — API 端点清单

> 单元：U05  
> 全部端点前缀 `/api`，需 Bearer Token 鉴权（DELETE 405 除外）。  
> 金额字段（amount / total_amount / payment_amount / payment_proof_signed_url）按角色过滤。

---

## 1. Settlement 端点（8）

| 方法 | 路径 | 权限 | 说明 | 故事 |
|---|---|---|---|---|
| GET | `/api/settlements/` | settlement:read | 列表 + 多筛选 + 分页（PR 角色自动限自己提交的） | EP06 |
| GET | `/api/settlements/{id}` | settlement:read | 详情（含 extra_items + 签名 URL） | EP06 |
| GET | `/api/settlements/daily-summary/as-of` | settlement:read | 口径 B：截至当日各状态快照（FB7） | EP06-S08 |
| GET | `/api/settlements/daily-summary/activity` | settlement:read | 口径 A：当天发生的动作（FB7） | EP06-S08 |
| PUT | `/api/settlements/{id}/review` | settlement.review:approve | 核查 approve / 驳回 reject（含自审禁止） | EP06-S03/S04 |
| POST | `/api/settlements/{id}/extra-items` | settlement:write | 增加结算项（运费/赞奖）+ total 重算（201） | EP06-S05 |
| PUT | `/api/settlements/{id}/payment-amount` | settlement:write | PR 主管填写付款金额 → 待财务付款 | EP06-S06 |
| PUT | `/api/settlements/{id}/payment-proof` | settlement.pay:upload_proof | 财务上传付款截图 → 已付款（FB4 + FB5） | EP06-S07 |

### 1.1 DELETE → 405（FB3）

| 方法 | 路径 | 行为 |
|---|---|---|
| DELETE | `/api/settlements/{id}` | 硬编码 405 — 财务记录永久不可删除；提示走 reject 或 V2 调整单 |

---

## 2. Shared attachment 端点（2，通用基础设施）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/attachments/upload-init` | 创建 attachment 记录（status='uploading'）+ 返回 presigned PUT URL（15min）；purpose 白名单校验（201） |
| POST | `/api/attachments/{id}/complete` | 前端直传 R2 完成后调用，标记 status='ready'（WHERE tenant_id 防越权） |

### 2.1 上传付款截图三步流程

```
1. POST /api/attachments/upload-init { bucket:"private", purpose:"settlement_proof", mime_type, size_bytes }
   → { attachment_id, presigned_url, expires_in_seconds: 900 }
2. PUT <presigned_url>  (前端直传 R2，Content-Type 须与 mime_type 一致；不带 Authorization)
3. POST /api/attachments/{attachment_id}/complete  → status='ready'
4. PUT /api/settlements/{id}/payment-proof { payment_date, payment_proof_attachment_id }
   → ProofAttachmentValidator 6 项强校验（FB4）→ 已付款 + SettlementPaid 反向事件（FB5）
```

---

## 3. 关键请求 / 响应示例

### 3.1 PUT /api/settlements/{id}/review

请求：
```json
{ "action": "approve" }
```
或驳回：
```json
{ "action": "reject", "review_reason": "金额与合同不符" }
```

响应（200）：`SettlementResponse`（settlement_status: "待付款" / "已驳回"）

### 3.2 POST /api/settlements/{id}/extra-items

请求：
```json
{ "item_type": "运费", "amount": "30.00", "remark": "顺丰到付" }
```
响应（201）：`SettlementResponse`（total_amount 已重算 = amount + Σ extra_items）

### 3.3 PUT /api/settlements/{id}/payment-proof

请求：
```json
{ "payment_date": "2026-05-26", "payment_proof_attachment_id": "uuid" }
```
响应（200）：`SettlementResponse`（settlement_status: "已付款" + payment_proof_signed_url）

校验失败（422）：attachment 6 项任一不满足（不存在 / 跨租户 / bucket / purpose / mime / size / 非 ready）

### 3.4 GET /api/settlements/daily-summary/as-of?date=2026-05-26

响应（200）：
```json
{
  "kind": "as_of",
  "date": "2026-05-26",
  "as_of": {
    "pending_review": { "count": 3, "total_amount": "300.00" },
    "pending_payment": { "count": 2, "total_amount": "400.00" },
    "pending_finance": { "count": 0, "total_amount": "0" },
    "paid": { "count": 1, "total_amount": "500.00" },
    "rejected": { "count": 0, "total_amount": "0" }
  },
  "outstanding_total": { "count": 5, "total_amount": "700.00" }
}
```

---

## 4. 字段级权限矩阵（legacy，U09 清理）

| 角色 | 金额可见 | payment_amount 可写 | 上传付款截图 |
|---|---|---|---|
| admin | ✓ | ✓ | ✓ |
| pr_manager | ✓ | ✓ | ✗ |
| finance | ✓ | ✗ | ✓ |
| pr | ✗ | ✗ | ✗ |

> 不可见角色：amount / total_amount / payment_amount / payment_proof_signed_url 返回 null；列表自动限自己提交的。

---

## 5. 降级语义

| 场景 | 响应 |
|---|---|
| 业务未匹配（列表无结果） | 200 + 空数组 |
| 系统失败（DB / R2） | 5xx + Sentry（不伪装空结果） |
| 状态冲突（并发推进） | 409 SETTLEMENT_STATE_CONFLICT |
| attachment 校验失败 | 422（跨租户不暴露存在性，统一 INVALID_ATTACHMENT_REFERENCE） |
| DELETE settlement | 405 METHOD_NOT_ALLOWED（FB3） |
