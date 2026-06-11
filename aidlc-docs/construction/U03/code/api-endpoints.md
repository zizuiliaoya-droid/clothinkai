## U03 API 端点摘要

> 7 个端点；全部端点路径前缀 `/api`，从 `app.modules.blogger.api.router` 注册。

---

## 1. 端点矩阵

| # | 方法 | 路径 | 故事 | 权限 | Schema (request) | Schema (response) |
|---|------|------|------|------|------------------|-------------------|
| 1 | POST | `/api/bloggers/` | EP04-S01 | `blogger:write` | `BloggerCreate` | `BloggerResponse` (201) |
| 2 | GET | `/api/bloggers/` | EP04-S03 | `blogger:read` | query: 多筛选 | `BloggerPage` |
| 3 | GET | `/api/bloggers/{blogger_id}` | — | `blogger:read` | path | `BloggerResponse` |
| 4 | PUT | `/api/bloggers/{blogger_id}` | EP04-S02 | `blogger:write` | `BloggerUpdate` | `BloggerResponse` |
| 5 | DELETE | `/api/bloggers/{blogger_id}` | — | `blogger:delete` | path | (204) |
| 6 | POST | `/api/bloggers/{blogger_id}/disable` | — | `blogger:write` | path | `BloggerResponse` |
| 7 | POST | `/api/bloggers/{blogger_id}/restore` | — | `blogger:delete` | path | `BloggerResponse` |

---

## 2. 关键端点示例

### 2.1 EP04-S01 — 创建博主

```http
POST /api/bloggers/
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "xiaohongshu_id": "XHS123",
  "nickname": "时尚博主",
  "platform": "小红书",
  "wechat": "wx_test",
  "phone": "13800000000",
  "follower_count": 10000,
  "blogger_type": "KOL",
  "gender_target": "女性",
  "category_tags": ["穿搭", "美妆"],
  "quality_tags": [],
  "quote": "500.00",
  "remark": "重点博主"
}
```

**Response 201**：`BloggerResponse`，敏感字段（quote / wechat / phone）按角色过滤。

**Errors**：
- `409 BLOGGER_XHS_ID_CONFLICT`：xiaohongshu_id 已存在；`details.existing_blogger_id` 含已有博主 id 用于前端引导
- `403 PERMISSION_DENIED`：无 `blogger:write`
- `403 FIELD_PERMISSION_DENIED`：角色无权写 quote / wechat / phone（如设计师）
- `422 VALIDATION_ERROR`

#### 重复创建响应示例
```json
{
  "code": "BLOGGER_XHS_ID_CONFLICT",
  "message": "该博主已存在，是否查看？",
  "details": {
    "xiaohongshu_id": "XHS123",
    "existing_blogger_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

---

### 2.2 EP04-S02 — 编辑博主（含 quote audit 脱敏）

```http
PUT /api/bloggers/<blogger_id>
Authorization: Bearer <jwt>

{
  "quote": "800.00"
}
```

**业务规则**：
- 仅 admin / pr / pr_manager 可写 quote（finance 仅读不写）
- audit_log 仅记 `{"quote_changed": true}`，不存历史值

#### Audit 内容示例

修改 quote：
```json
{
  "action": "blogger.update",
  "after": {"quote_changed": true}
}
```

修改 nickname（普通字段）：
```json
{
  "action": "blogger.update",
  "before": {"nickname": "旧昵称"},
  "after": {"nickname": "新昵称"}
}
```

---

### 2.3 EP04-S03 — 搜索筛选

```http
GET /api/bloggers/?keyword=时尚&blogger_type=KOL
                   &follower_count_min=1000&follower_count_max=100000
                   &category_tag=穿搭&quality_tag=高互动
                   &page=1&page_size=20
```

**Response 200**：`BloggerPage`

```json
{
  "items": [
    {
      "id": "...",
      "xiaohongshu_id": "XHS123",
      "nickname": "时尚博主",
      "wechat": null,            // PR 无 CONTACT 权限时为 null
      "quote": null,             // 同上
      ...
    }
  ],
  "total": 50,
  "page": 1,
  "page_size": 20
}
```

#### 关键参数说明

| 参数 | 说明 |
|---|---|
| `keyword` | ILIKE 模糊匹配 nickname / xiaohongshu_id；wechat **仅当用户有 CONTACT_VISIBLE_ROLES 时**参与匹配（防侧信道） |
| `category_tag` / `quality_tag` | JSONB 包含查询（GIN JSONB 索引） |
| `follower_count_min/max` | 范围筛选 |
| `is_active` | 默认 true，传 `?include_inactive=true` 包含停用 |

#### 降级语义

- **业务未匹配** → 200 + `{items: [], total: 0}`
- **系统失败** → 5xx + Sentry，**绝不伪装空候选**

---

## 3. 字段级权限矩阵（U03 过渡，U09 改造）

### 3.1 读权限

| 角色 | quote | wechat | phone |
|---|---|---|---|
| admin | ✅ | ✅ | ✅ |
| pr | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ |
| finance | ✅ | ❌ | ❌ |
| 其他（merchandiser/designer/operations 等） | ❌ | ❌ | ❌ |

### 3.2 写权限

| 角色 | quote | wechat | phone |
|---|---|---|---|
| admin | ✅ | ✅ | ✅ |
| pr | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ |
| finance | ❌ | ❌ | ❌ |
| 其他 | ❌ | ❌ | ❌ |

### 3.3 防侧信道

`/api/bloggers/?keyword=xxx` 中 wechat 字段：
- 默认**仅** nickname / xiaohongshu_id 参与匹配
- 仅当用户具有 CONTACT_VISIBLE_ROLES（admin / pr / pr_manager）时，wechat 才参与匹配
- 防止无 wechat 读权限的角色通过命中行为侧信道泄露

---

## 4. 错误码

| HTTP | code | 触发场景 |
|---|---|---|
| 401 | `TOKEN_INVALID` | 无 / 无效 token |
| 403 | `PERMISSION_DENIED` | 缺 `blogger:*` 权限 |
| 403 | `FIELD_PERMISSION_DENIED` | 无权写 quote / wechat / phone |
| 404 | `BLOGGER_NOT_FOUND` | 博主不存在 |
| 409 | `BLOGGER_XHS_ID_CONFLICT` | xiaohongshu_id 已存在；含 details.existing_blogger_id |
| 409 | `BLOGGER_HAS_REFERENCE` | 软删被引用（U03 阶段不会触发） |
| 422 | `INVALID_QUOTE` | quote < 0 |
| 422 | `INVALID_FOLLOWER_COUNT` | follower_count < 0 |
| 422 | `INVALID_TAG_FORMAT` | tag 项超长 |
| 422 | `VALIDATION_ERROR` | Pydantic 校验失败 |
