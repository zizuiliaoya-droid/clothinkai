# U02 API 端点摘要

> 18 个端点（不含 /health, /ready, /metrics, /api/docs 等基础设施端点，本单元仅新增业务路由）。  
> 全部端点路径前缀 `/api`，从 `app.modules.product.api.router` 注册。

---

## 1. 端点矩阵

| # | 方法 | 路径 | 故事 | 权限 | Schema (request) | Schema (response) |
|---|------|------|------|------|------------------|-------------------|
| 1 | POST | `/api/styles/` | EP02-S01 | `product:write` | `StyleCreate` | `StyleResponse` (201) |
| 2 | GET | `/api/styles/match` | EP02-S06 | `product:read` | query: `style_code` 或 `keyword` | `MatchResponse` |
| 3 | GET | `/api/styles/` | — | `product:read` | query: 多筛选 | `StylePage` |
| 4 | GET | `/api/styles/{style_id}` | — | `product:read` | path | `StyleResponse` |
| 5 | PUT | `/api/styles/{style_id}` | EP02-S03 | `product:write` | `StyleUpdate` | `StyleResponse` |
| 6 | DELETE | `/api/styles/{style_id}` | — | `product:delete` | path | (204) |
| 7 | POST | `/api/styles/{style_id}/disable` | — | `product:write` | path | `StyleResponse` |
| 8 | POST | `/api/styles/{style_id}/restore` | — | `product:delete` | path | `StyleResponse` |
| 9 | POST | `/api/skus/` | EP02-S02 | `product:write` | `SkuCreate` | `SkuResponse` (201) |
| 10 | GET | `/api/skus/by-style/{style_id}` | EP02-S05 | `product:read` | path + `include_inactive` | `list[SkuResponse]` |
| 11 | GET | `/api/skus/{sku_id}` | — | `product:read` | path | `SkuResponse` |
| 12 | PUT | `/api/skus/{sku_id}` | EP02-S04 | `product:write` | `SkuUpdate` | `SkuResponse` |
| 13 | DELETE | `/api/skus/{sku_id}` | — | `product:delete` | path | (204) |
| 14 | POST | `/api/brands/` | — | `brand:write` | `BrandCreate` | `BrandResponse` (201) |
| 15 | GET | `/api/brands/` | — | `brand:read` | query: page/page_size/is_active | `{items, total, page, page_size}` |
| 16 | GET | `/api/brands/{brand_id}` | — | `brand:read` | path | `BrandResponse` |
| 17 | PUT | `/api/brands/{brand_id}` | — | `brand:write` | `BrandUpdate` | `BrandResponse` |
| 18 | DELETE | `/api/brands/{brand_id}` | — | `brand:delete` | path | `BrandResponse`（停用，非硬删） |

---

## 2. 关键端点示例

### 2.1 EP02-S01 — 创建款式

```http
POST /api/styles/
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "style_code": "W001",
  "style_name": "波点花边连衣裙",
  "short_name": "波点花边",
  "brand_id": "550e8400-e29b-41d4-a716-446655440000",
  "category": "连衣裙",
  "season": "夏",
  "gender": "女",
  "tags": ["雪纺", "通勤"],
  "tag_color": ["米白", "雾蓝"],
  "main_image_key": "<tenant_id>/styles/<style_id>/main/image.jpg",
  "remark": "首发款",
  "design_status": "大货"
}
```

**Response 201**:
```json
{
  "id": "...",
  "style_code": "W001",
  "style_name": "波点花边连衣裙",
  "short_name": "波点花边",
  "category": "连衣裙",
  "design_status": "大货",
  "is_active": true,
  "is_deleted": false,
  "main_image_url": "https://cdn.../<tenant_id>/styles/.../image.jpg",
  ...
}
```

**Errors**:
- `409 STYLE_CODE_CONFLICT`：style_code 已存在
- `403 PERMISSION_DENIED`：无 `product:write` 权限
- `422 VALIDATION_ERROR`：字段格式错误

---

### 2.2 EP02-S02 — 创建 SKU

```http
POST /api/skus/
Authorization: Bearer <jwt>

{
  "style_id": "<style_id>",
  "sku_code": "W001-红-M",
  "color": "红",
  "size": "M",
  "cost_price": "100.00",
  "base_price": "200.00",
  "sourcing_type": "自产"
}
```

**Errors**:
- `409 SKU_CODE_CONFLICT`：sku_code 已存在
- `422 INVALID_STYLE_REFERENCE`：style_id 不存在
- `422 SOURCING_PRICE_MISMATCH`：自产但缺 cost_price
- `403 FIELD_PERMISSION_DENIED`：无权写 cost_price（PR / 设计师）

---

### 2.3 EP02-S04 — 编辑 SKU 成本

```http
PUT /api/skus/<sku_id>
Authorization: Bearer <jwt>

{
  "cost_price": "120.00"
}
```

**业务规则**：
- 仅 admin / merchandiser / finance 角色可写 cost_price
- 其他角色返回 `403 FIELD_PERMISSION_DENIED`
- audit_log 记录 `{"cost_price_changed": true}`（不存历史值）

---

### 2.4 EP02-S05 — 按款式查询 SKU

```http
GET /api/skus/by-style/<style_id>?include_inactive=false
Authorization: Bearer <jwt>
```

**Response 200**:
```json
[
  {
    "id": "...",
    "sku_code": "W001-红-M",
    "color": "红",
    "size": "M",
    "cost_price": "100.00",   // PR 角色看不到 → null
    "base_price": "200.00",
    "is_active": true,
    ...
  },
  ...
]
```

最大返回 1000 条（不分页）。空列表返回 200 + `[]`。

---

### 2.5 EP02-S06 — 款号 ↔ 商品简称双向关联

#### 精确反查
```http
GET /api/styles/match?style_code=W001
```

**Response 200**（业务匹配）:
```json
{
  "matched": true,
  "candidates": [
    {
      "id": "...",
      "style_code": "W001",
      "style_name": "波点花边连衣裙",
      "short_name": "波点花边",
      "display_short_name": "波点花边"
    }
  ],
  "total": 1
}
```

**Response 200**（业务未匹配）:
```json
{
  "matched": false,
  "candidates": [],
  "total": 0
}
```

#### 模糊反查
```http
GET /api/styles/match?keyword=波点
```

**Response 200**:
```json
{
  "matched": true,
  "candidates": [...最多 20 项，按 similarity DESC 排序],
  "total": 20
}
```

#### 关键降级语义（FB1）

- **业务未匹配** → 200 + 空候选（`matched=false`），前端允许用户继续手动输入款号字符串
- **系统失败**（DB 异常 / 超时 / RLS 错误）→ 5xx + Sentry，前端展示错误提示要求用户**稍后重试**，**不要伪装成"未匹配"**

---

### 2.6 软删 / 停用 / 恢复款式

| 操作 | 端点 | 业务规则 |
|---|---|---|
| 软删 | `DELETE /api/styles/{id}` | 仅当 active sku=0；返回 409 `STYLE_HAS_ACTIVE_SKU` 否则 |
| 停用 | `POST /api/styles/{id}/disable` | 设 `is_active=false`；保留 SKU 关联 |
| 恢复 | `POST /api/styles/{id}/restore` | 仅 admin；先校验 style_code 未被新款占用 |

---

## 3. 权限映射

U01 已 seed 通配符权限：
- `product.*:*`：admin / merchandiser
- `product.*:read`：所有非访客角色

本 migration（004）追加：
- `brand.*:*`：admin / merchandiser
- `brand.*:read`：8 个角色（含 admin / merchandiser / pr / pr_manager / finance / designer / design_assistant / pattern_maker / operations）

---

## 4. 字段级权限矩阵（U02 过渡，U09 改造）

| 角色 | cost_price | purchase_price | base_price |
|---|---|---|---|
| admin | ✅ | ✅ | ✅ |
| merchandiser | ✅ | ✅ | ✅ |
| finance | ✅ | ✅ | ✅ |
| pr / pr_manager | ❌ | ❌ | ✅ |
| designer / design_assistant | ❌ | ❌ | ✅ |
| pattern_maker / operations | ❌ | ❌ | ✅ |

**实施**：`service.SkuService._to_response` + `_check_price_write_permission`，依赖 `legacy_field_permissions.PRICE_VISIBLE_ROLES`。

---

## 5. 错误码

| HTTP | code | 触发场景 |
|---|---|---|
| 401 | `TOKEN_INVALID` | 无 / 无效 token |
| 403 | `PERMISSION_DENIED` | 缺 module:action 权限 |
| 403 | `FIELD_PERMISSION_DENIED` | 无权写 cost_price / purchase_price |
| 404 | `STYLE_NOT_FOUND` / `SKU_NOT_FOUND` / `BRAND_NOT_FOUND` | 资源不存在 |
| 409 | `STYLE_CODE_CONFLICT` | style_code 已存在 |
| 409 | `SKU_CODE_CONFLICT` | sku_code 已存在 |
| 409 | `BRAND_CODE_CONFLICT` | brand_code 已存在 |
| 409 | `STYLE_HAS_ACTIVE_SKU` | 删 style 时仍有 active sku |
| 409 | `SKU_HAS_REFERENCE` | 软删 sku 但被引用（U02 阶段始终为 0） |
| 422 | `INVALID_STYLE_REFERENCE` | sku.style_id 不存在 |
| 422 | `INVALID_BRAND` | brand_id 不存在 |
| 422 | `SOURCING_PRICE_MISMATCH` | sourcing_type 与价格字段不一致 |
| 422 | `INVALID_PRICE` | 价格 < 0 |
| 422 | `VALIDATION_ERROR` | Pydantic 校验失败 |
