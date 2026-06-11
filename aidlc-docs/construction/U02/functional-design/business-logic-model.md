# U02 业务逻辑模型（Business Logic Model）

> 单元：U02 — 商品 / SKU 基础  
> 形式：每个核心 Use Case 一段 ASCII 流程 + 步骤说明 + 错误分支  
> 关联：domain-entities.md / business-rules.md

---

## 1. Use Case 总览

| UC | 名称 | 关联故事 | API |
|---|---|---|---|
| UC-1 | 创建款式（Style） | EP02-S01 | `POST /api/styles/` |
| UC-2 | 创建 SKU | EP02-S02 | `POST /api/skus/` |
| UC-3 | 编辑款式 | EP02-S03 | `PUT /api/styles/{id}` |
| UC-4 | 编辑 SKU 成本/价格 | EP02-S04 | `PUT /api/skus/{id}` |
| UC-5 | 按款式查询 SKU | EP02-S05 | `GET /api/skus/by-style/{style_id}` |
| UC-6 | 款号↔商品简称双向关联 | EP02-S06 | `GET /api/styles/match` |
| UC-7 | Style 列表（含搜索/筛选） | — | `GET /api/styles/` |
| UC-8 | 软删 / 停用 / 恢复 | — | `DELETE /api/styles/{id}`, `POST /api/styles/{id}/restore` |
| UC-9 | Brand 字典 CRUD | — | `/api/brands/*` |

---

## 2. UC-1 创建款式（POST /api/styles/）

### 2.1 流程

```
[前端] POST /api/styles/  (Bearer token, body: StyleCreate)
      │
      ▼
[1] FastAPI Router → 解析 token
      │
      ├──[Q]── 401 if invalid → 终止
      ▼
[2] @require_permission("product:write")
      │
      ├──[Q]── 403 if missing → 终止
      ▼
[3] Pydantic 校验 StyleCreate Schema
      │  - style_code 长度/字符集
      │  - style_name 长度
      │  - category ∈ Category Enum
      │  - tags 数组、season/gender 选填
      ├──[Q]── 422 if invalid → 终止（return ValidationError + details）
      ▼
[4] StyleService.create_style(payload, current_user)
      │
      ├─ 4.1 检查 brand_id 存在 + 同租户 + is_active
      │      └──[Q]── 422 INVALID_BRAND → 终止
      │
      ├─ 4.2 BR-U02-01: 检查 style_code 唯一
      │      SELECT id FROM style
      │       WHERE tenant_id=:t AND style_code=:c
      │         AND is_deleted=false
      │      ├──[Q]── 409 STYLE_CODE_CONFLICT → 终止
      │
      ├─ 4.3 创建 Style 实体
      │      style.tenant_id = ctx.tenant_id (自动)
      │      style.design_status = '大货' (BR-U02-...)
      │      style.is_active = true
      │      style.is_deleted = false
      │
      ├─ 4.4 主图处理（main_image_id 可选）
      │      校验 attachment 存在 + tenant_id 匹配
      │      └──[Q]── 422 INVALID_ATTACHMENT
      │
      ├─ 4.5 详情图处理（detail_image_ids 列表，可选）
      │      为每个 attachment_id 创建 StyleDetailImage 行（sort_order 按列表顺序）
      │
      ├─ 4.6 db.add(style) + db.flush()
      │      ORM 钩子自动写 created_at / updated_at
      │      RLS 策略检查 tenant_id 匹配（U01 实现）
      │
      ├─ 4.7 @audit("style.create") 装饰器
      │      AuditService.log(actor=user, action='style.create',
      │                        resource='style', resource_id=style.id,
      │                        changes={"style_code": ..., "style_name": ..., ...})
      │
      ├─ 4.8 db.commit()
      │
      ▼
[5] 返回 StyleResponse (字段按角色过滤，cost_price/purchase_price 等不在 Style 范围)
      │
      ▼
[前端] 201 Created + Location header
```

### 2.2 错误矩阵

| 步骤 | 触发 | HTTP | code |
|---|---|---|---|
| 1 | token 无效/过期 | 401 | `UNAUTHORIZED` |
| 2 | 无 product:write | 403 | `PERMISSION_DENIED` |
| 3 | Pydantic 校验失败 | 422 | `VALIDATION_FAILED` |
| 4.1 | brand_id 不存在 | 422 | `INVALID_BRAND` |
| 4.2 | style_code 重复 | 409 | `STYLE_CODE_CONFLICT` |
| 4.4 | main_image_id 不存在 | 422 | `INVALID_ATTACHMENT` |

---

## 3. UC-2 创建 SKU（POST /api/skus/）

### 3.1 流程

```
POST /api/skus/  body: SkuCreate
      │
      ▼
[1] @require_permission("product:write")
      │
      ▼
[2] Pydantic SkuCreate
      │ style_id, sku_code, color, size, cost_price?, purchase_price?, base_price?, sourcing_type
      │
      ▼
[3] SkuService.create_sku(payload, current_user)
      │
      ├─ 3.1 BR-U02-12: 校验 style_id 存在 + 同租户 + is_deleted=false
      │      └──[Q]── 422 INVALID_STYLE_REFERENCE
      │
      ├─ 3.2 BR-U02-02: 校验 sku_code 唯一
      │      └──[Q]── 409 SKU_CODE_CONFLICT
      │
      ├─ 3.3 BR-U02-13: sourcing_type 与价格一致性
      │      if sourcing_type='自产' and cost_price IS NULL:
      │         raise ValidationError('SOURCING_PRICE_MISMATCH')
      │      if sourcing_type='外采' and purchase_price IS NULL:
      │         raise ValidationError('SOURCING_PRICE_MISMATCH')
      │      if sourcing_type='混合': pass
      │
      ├─ 3.4 BR-U02-14: 价格非负（在 Pydantic Validator 已校）
      │
      ├─ 3.5 BR-U02-41: 价格字段写权限检查
      │      if user.role NOT IN [admin, follower(跟单), finance]:
      │         if cost_price OR purchase_price IS NOT NULL:
      │            raise FieldPermissionDenied(403)
      │
      ├─ 3.6 创建 Sku 实体 + db.flush()
      │
      ├─ 3.7 @audit("sku.create") — 仅 sensitive 字段（sku_code/cost/purchase/base）
      │
      ├─ 3.8 db.commit()
      │
      ▼
[4] 返回 SkuResponse（字段按角色过滤，cost_price 对 PR 不可见 → None）
```

### 3.2 关键代码位

```python
# modules/product/service.py
class SkuService:
    async def create_sku(self, payload: SkuCreate, user: CurrentUser) -> SkuResponse:
        # 3.1: 验款式存在
        style = await self.style_repo.get_by_id(payload.style_id)
        if not style or style.is_deleted:
            raise ValidationError('INVALID_STYLE_REFERENCE')
        
        # 3.2: 验 sku_code 唯一（DB 层 + 应用层双保险）
        existing = await self.sku_repo.get_by_code(payload.sku_code)
        if existing:
            raise ResourceConflictError('SKU_CODE_CONFLICT')
        
        # 3.3: sourcing_type 与价格一致性
        self._validate_sourcing_price(payload)
        
        # 3.5: 字段写权限
        self._check_price_write_permission(payload, user)  # TODO U09
        
        # 3.6: 创建
        sku = Sku(**payload.model_dump())
        self.session.add(sku)
        await self.session.flush()
        
        # 3.7: 审计
        await self.audit.log_create('sku', sku.id, payload.model_dump())
        
        await self.session.commit()
        return self._to_response(sku, user)
```

---

## 4. UC-3 编辑款式（PUT /api/styles/{id}）

### 4.1 流程

```
PUT /api/styles/{id}  body: StyleUpdate (部分字段)
      │
      ▼
[1] @require_permission("product:write")
      │
      ▼
[2] Pydantic StyleUpdate（所有字段都 Optional）
      │
      ▼
[3] StyleService.update_style(id, payload, user)
      │
      ├─ 3.1 取出 existing style（同租户 RLS 自动）
      │      └──[Q]── 404 if not found
      │
      ├─ 3.2 if style.is_deleted: 422 STYLE_DELETED
      │
      ├─ 3.3 BR-U02-01: 若 payload 含 style_code 且变更 → 唯一性校验
      │
      ├─ 3.4 dict diff: 计算实际变更字段
      │      changes = {field: {"before": old, "after": new} for field in changed_fields}
      │
      ├─ 3.5 if not changes:
      │         (BR-U02-32) 直接返回 existing，不写 audit，updated_at 仍刷新
      │
      ├─ 3.6 应用变更（model.style_name = payload.style_name etc.）
      │      ORM updated_at 钩子触发
      │
      ├─ 3.7 BR-U02-30: 审计仅敏感字段
      │      sensitive_changes = {k: v for k, v in changes.items() if k in {"style_code"}}
      │      if sensitive_changes:
      │         await audit.log_update('style', style.id, sensitive_changes)
      │
      ├─ 3.8 db.commit()
      │
      ▼
[4] 返回 StyleResponse
```

### 4.2 注意事项
- **BR-U02-30 字段白名单**：只有 `style_code` 在敏感名单。其他字段（name, tags, brand_id, category 等）变更不写 audit
- **乐观并发**：暂不在 U02 实施 ETag/version，留 V1 优化
- **detail_images 替换语义**：若 payload.detail_image_ids 传完整列表 → 替换；不传 → 保留原列表（PATCH 思想，但用 PUT 路由）

---

## 5. UC-4 编辑 SKU 成本/价格（PUT /api/skus/{id}）

### 5.1 流程

```
PUT /api/skus/{id}  body: SkuUpdate
      │
      ▼
[1] @require_permission("product:write")
      │
      ▼
[2] SkuService.update_sku(id, payload, user)
      │
      ├─ 2.1 取 existing sku  ──→ 404 if not found / is_deleted
      │
      ├─ 2.2 BR-U02-41: 字段写权限
      │      if user.role NOT IN [admin, follower, finance]:
      │         if 'cost_price' in payload.model_fields_set:
      │            raise FieldPermissionDenied('cost_price 字段写入需要跟单或财务角色')
      │         if 'purchase_price' in payload.model_fields_set:
      │            raise FieldPermissionDenied(...)
      │
      ├─ 2.3 BR-U02-13: sourcing_type 校验（若变更）
      │
      ├─ 2.4 dict diff
      │
      ├─ 2.5 应用变更
      │
      ├─ 2.6 BR-U02-31: 审计敏感字段
      │      sensitive = {"cost_price", "purchase_price", "base_price",
      │                   "sku_code", "sourcing_type"}
      │      sensitive_changes = {k: v for k, v in changes.items() if k in sensitive}
      │      if sensitive_changes:
      │         await audit.log_update('sku', sku.id, sensitive_changes)
      │
      ├─ 2.7 db.commit()
      │
      ▼
[3] 返回 SkuResponse（应用 BR-U02-41 字段过滤）
```

### 5.2 验收映射

- **EP02-S04.given1**：跟单调用成功 + audit_log 写入新旧值
- **EP02-S04.given2**：设计师调用 PUT cost_price 返回 403

---

## 6. UC-5 按款式查询 SKU（GET /api/skus/by-style/{style_id}）

### 6.1 流程

```
GET /api/skus/by-style/{style_id}
      │
      ▼
[1] @require_permission("product:read")
      │
      ▼
[2] SkuService.list_by_style(style_id, user)
      │
      ├─ 2.1 验 style 存在（RLS 自动跨租户 → 404）
      │
      ├─ 2.2 SELECT * FROM sku
      │      WHERE tenant_id=:t AND style_id=:s
      │        AND is_deleted=false
      │      ORDER BY size, color, sku_code
      │      LIMIT 1000
      │
      ├─ 2.3 默认 is_active=true（可通过 ?include_inactive=true 包含停用）
      │
      ├─ 2.4 应用 BR-U02-41 字段过滤（cost_price 按角色置 None）
      │
      ▼
[3] 返回 List[SkuResponse]（不分页，因为单款 SKU 数有上限）
```

### 6.2 验收映射

- **EP02-S05.given1**：6 个 SKU 全部返回
- **EP02-S05.given2**：空数组 + 200（不返回 404）

---

## 7. UC-6 款号↔商品简称双向关联（GET /api/styles/match）

### 7.1 流程

```
GET /api/styles/match?style_code=W001
GET /api/styles/match?keyword=波点花边
      │
      ▼
[1] @require_permission("product:read")
      │
      ▼
[2] StyleService.match()
      │
      ├─ 2.1 互斥参数校验
      │      style_code 和 keyword 至少有一个，但不能同时
      │
      ├─ 2.2 if style_code:                       (BR-U02-50 精确反查)
      │         SELECT id, style_code, style_name, short_name
      │         FROM style
      │         WHERE tenant_id=:t AND style_code=:c
      │           AND is_deleted=false AND is_active=true
      │         LIMIT 1
      │      
      │         if not found:
      │            return { matched: false, candidates: [] }
      │         
      │         return {
      │            matched: true,
      │            style: {...},
      │            display_short_name: short_name OR style_name      (BR-U02-53)
      │         }
      │
      ├─ 2.3 elif keyword:                        (BR-U02-51 模糊反查)
      │         查询拼接表达式必须与索引表达式严格一致：
      │         
      │         SELECT id, style_code, style_name, short_name,
      │                similarity(
      │                  style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''),
      │                  :keyword
      │                ) AS sim
      │         FROM style
      │         WHERE tenant_id=:t AND is_deleted=false AND is_active=true
      │           AND (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
      │                ILIKE '%' || :keyword || '%'
      │         ORDER BY
      │           (CASE WHEN short_name ILIKE :exact THEN 1
      │                 WHEN style_name ILIKE :exact THEN 2
      │                 ELSE 3 END),
      │           sim DESC,
      │           created_at DESC
      │         LIMIT 20
      │
      │         return {
      │            matched: candidates.length > 0,
      │            candidates: [...],
      │            total: candidates.length
      │         }
      │
      ▼
[3] 返回 MatchResponse
```

### 7.2 性能要点 + 降级语义

- **不打 audit**：查询场景，频次高
- **不写 cache**：列表小（≤20）+ GIN trgm 索引足够 + 数据写少读多
- **索引保障**：`idx_style_search_trgm`（GIN trgm，U02 必建）支撑 5 万行 / 租户 P95 ≤ 300ms
- **降级语义（关键）**：
  - 业务未匹配（合法查询、零结果）→ `200 OK` + `{ matched: false / candidates: [] }`，前端允许用户手动输入
  - 系统失败（DB 异常 / 超时 / RLS / 权限）→ `5xx / 403`，**绝不返回空候选**，前端展示错误提示要求用户稍后重试
- **未来升级**：单租户 style ≥ 50 万行或 P95 > 500ms 持续 1 周 → 迁移到 PostgreSQL 全文搜索（tsvector + GIN）或独立 Elasticsearch（V2+）

---

## 8. UC-7 Style 列表（GET /api/styles/）

### 8.1 流程

```
GET /api/styles/?page=1&page_size=20&keyword=&brand_id=&category=&season=&is_active=true
      │
      ▼
[1] @require_permission("product:read")
      │
      ▼
[2] StyleService.list(filters, page, page_size, order_by)
      │
      ├─ 2.1 BR-U02-60 验证参数
      │      page ≥ 1, page_size ∈ [1, 100]
      │
      ├─ 2.2 build_query()
      │      base = SELECT * FROM style WHERE tenant_id=:t AND is_deleted=false
      │      if not include_inactive: base = base.where(is_active=true)
      │      
      │      if keyword:
      │         base = base.where(style_code ILIKE :p OR style_name ILIKE :p OR short_name ILIKE :p)
      │      if brand_id:
      │         base = base.where(brand_id=:b)
      │      if category:
      │         base = base.where(category=:c)
      │      ...
      │
      ├─ 2.3 总数：SELECT COUNT(*) FROM (...) sub
      │
      ├─ 2.4 数据：base ORDER BY created_at DESC LIMIT page_size OFFSET (page-1) * page_size
      │
      ├─ 2.5 应用字段过滤（虽 Style 无 cost_price 等，规则统一）
      │
      ▼
[3] 返回 Page<StyleResponse>
```

---

## 9. UC-8 软删 / 停用 / 恢复

### 9.1 软删款式 `DELETE /api/styles/{id}`
```
[1] @require_permission("product:delete") — 仅 admin
[2] 取 style ──→ 404 if not found
[3] BR-U02-21: 检查关联 SKU
    SELECT COUNT(*) FROM sku
    WHERE tenant_id=:t AND style_id=:s
      AND is_deleted=false AND is_active=true
    
    if count > 0:
       409 STYLE_HAS_ACTIVE_SKU
[4] style.is_deleted = true
[5] @audit("style.delete")
[6] commit
[7] 返回 204
```

### 9.2 停用款式 `POST /api/styles/{id}/disable`
```
[1] @require_permission("product:write")
[2] style.is_active = false
[3] @audit("style.disable") — 视为敏感操作
[4] return StyleResponse
```

### 9.3 恢复款式 `POST /api/styles/{id}/restore`
```
[1] @require_permission("product:delete")  # admin
[2] BR-U02-22: 校验 style_code 是否被新款占用
    if exists: 409 STYLE_CODE_CONFLICT
[3] style.is_deleted = false; is_active = true
[4] @audit("style.restore")
[5] return StyleResponse
```

### 9.4 软删 SKU `DELETE /api/skus/{id}`
```
[1] @require_permission("product:delete")
[2] BR-U02-20: 调用 service.check_sku_references(sku_id)
    U02 阶段无引用源（promotion/order 表不存在），始终返回 references=0
    U04 启用后，service.check_sku_references 查 promotion 表
[3] if references > 0: 409 SKU_HAS_REFERENCE
[4] sku.is_deleted = true
[5] @audit
[6] return 204
```

---

## 10. UC-9 Brand 字典 CRUD

简单 CRUD，遵循通用模式。

| API | 方法 | 权限 | 说明 |
|---|---|---|---|
| `GET /api/brands/` | GET | brand:read | 列表 |
| `GET /api/brands/{id}` | GET | brand:read | 详情 |
| `POST /api/brands/` | POST | brand:write (仅 admin) | 创建，BR-U02-03 唯一 |
| `PUT /api/brands/{id}` | PUT | brand:write | 编辑 |
| `DELETE /api/brands/{id}` | DELETE | brand:delete | 软删（is_active=false），不允许引用时硬删 |

不写 audit（字典维护频次低，且无敏感字段）。

---

## 11. 端到端时序

```
跟单录入 W001 款式 + 创建 6 个 SKU + 编辑成本价 → PR 录推广时按款号反查 → 推广录入完成
```

```
跟单 ──创建Style──> StyleService.create_style ──> DB ──> audit_log
                                                              │
                跟单 ──创建SKU x6──> SkuService.create_sku ──> DB
                                                              │
                跟单 ──编辑cost_price──> SkuService.update_sku ──> audit_log (变更字段)
                                                              │
PR 在推广录入页输入 W001 ──> StyleService.match ──> 返回 short_name="波点花边"
                                                              │
PR 看到 6 个 SKU 选 红-M ──> SkuService.list_by_style ──> 返回 SKU列表
                                                              │
PR 创建 promotion (sku_id=W001-红-M.id) ──> [U04 范围]
```

---

## 12. 一致性校验

| 校验 | 结果 |
|---|---|
| 9 个 Use Case 覆盖 EP02-S01~S06 全部验收 | ✅ |
| 错误流程明确，每个 422/409/403 都有触发条件 | ✅ |
| 审计仅敏感字段，与 BR-U02-30/31 一致 | ✅ |
| 字段级权限以硬编码 + TODO U09 形式落地 | ✅ |
| 软删/恢复/停用三种状态有明确语义 | ✅ |
| match 接口流程精确反查 + 模糊反查双场景闭环 | ✅ |
| 引用检查为 U04/U16 留接口 | ✅ |
