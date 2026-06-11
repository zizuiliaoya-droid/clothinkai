# U02 领域实体（Domain Entities）

> 单元：U02 — 商品 / SKU 基础  
> 范围：Style / Sku / Brand / Category（枚举）/ StyleAttachment 关系  
> 不含：PlatformProduct（U09 / V1）、Bundle（U17 / V2）

---

## 1. 实体清单

| # | 实体 | 类型 | 多租户 | 说明 |
|---|---|---|---|---|
| 1 | `Style` | TenantScopedModel | ✅ | 款式（设计/商品的根） |
| 2 | `Sku` | TenantScopedModel | ✅ | 款式下的最小销售单元 |
| 3 | `Brand` | TenantScopedModel | ✅ | 品牌字典表（每租户自维护） |
| 4 | `StyleDetailImage` | TenantScopedModel | ✅ | 款式详情图（顺序敏感的多图） |
| 5 | `Category` | Python Enum | — | 大类硬编码（U09 改字典表） |
| 6 | `Season` | Python Enum | — | 季节硬编码 |
| 7 | `Gender` | Python Enum | — | 性别硬编码 |
| 8 | `DesignStatus` | Python Enum | — | 2 值（大货 / 设计中），U10a 扩为 7 值 |
| 9 | `SourcingType` | Python Enum | — | SKU 来源类型（自产 / 外采 / 混合） |

---

## 2. ER 图（Mermaid）

```mermaid
erDiagram
    Tenant ||--o{ Style : owns
    Tenant ||--o{ Sku : owns
    Tenant ||--o{ Brand : owns
    Brand ||--o{ Style : "categorized by"
    Style ||--o{ Sku : "has variants"
    Style ||--o{ StyleDetailImage : "has details"
    Style }o--|| Attachment : "main_image (U01)"
    StyleDetailImage }o--|| Attachment : "image (U01)"
    User ||--o{ Style : "owner_id (U01)"

    Style {
        UUID id PK
        UUID tenant_id FK
        string style_code "租户内唯一"
        string style_name
        string short_name "可选 简称"
        UUID brand_id FK
        Category category
        Season season
        Gender gender
        string[] tags
        string[] tag_color
        UUID main_image_id FK "→ U01.attachment.id"
        text remark
        UUID owner_id FK "→ U01.user.id"
        DesignStatus design_status "默认 大货"
        boolean is_active
        boolean is_deleted
        timestamp created_at
        timestamp updated_at
    }

    Sku {
        UUID id PK
        UUID tenant_id FK
        UUID style_id FK
        string sku_code "租户内唯一"
        string color
        string size
        decimal cost_price "DECIMAL(10,2) 自产成本"
        decimal purchase_price "DECIMAL(10,2) 外采价"
        decimal base_price "DECIMAL(10,2) 基本售价"
        SourcingType sourcing_type
        boolean is_active
        boolean is_deleted
        timestamp created_at
        timestamp updated_at
    }

    Brand {
        UUID id PK
        UUID tenant_id FK
        string brand_code "租户内唯一"
        string brand_name
        boolean is_active
        timestamp created_at
        timestamp updated_at
    }

    StyleDetailImage {
        UUID id PK
        UUID tenant_id FK
        UUID style_id FK
        UUID attachment_id FK "→ U01.attachment.id"
        int sort_order
        timestamp created_at
    }
```

---

## 3. 实体详细字段

### 3.1 Style — 款式

| 字段 | 类型 | 必填 | 唯一 | 默认 | 说明 |
|---|---|---|---|---|---|
| `id` | UUID | ✅ | ✅ | `gen_random_uuid()` | 主键 |
| `tenant_id` | UUID | ✅ | (id, tenant_id) | 来自 ctx | 租户外键（继承自 TenantScopedModel） |
| `style_code` | VARCHAR(64) | ✅ | (tenant_id, style_code) UNIQUE | — | 款号（业务键） |
| `style_name` | VARCHAR(255) | ✅ | — | — | 款式全称 |
| `short_name` | VARCHAR(64) | ❌ | — | NULL | 商品简称（推广用） |
| `brand_id` | UUID | ❌ | — | NULL | 品牌外键（→ Brand.id） |
| `category` | VARCHAR(32) | ✅ | — | — | 大类枚举值 |
| `season` | VARCHAR(16) | ❌ | — | NULL | 季节枚举值 |
| `gender` | VARCHAR(8) | ❌ | — | NULL | 性别枚举值 |
| `tags` | JSONB / TEXT[] | ❌ | — | `[]` | 自由标签数组 |
| `tag_color` | JSONB / TEXT[] | ❌ | — | `[]` | 色系数组（如 ["米白", "雾蓝"]） |
| `main_image_id` | UUID | ❌ | — | NULL | 主图（→ U01 attachment.id） |
| `remark` | TEXT | ❌ | — | NULL | 备注 |
| `owner_id` | UUID | ❌ | — | NULL | 跟单负责人（→ U01 user.id） |
| `design_status` | VARCHAR(16) | ✅ | — | `'大货'` | DesignStatus 枚举（U02 仅 2 值） |
| `is_active` | BOOLEAN | ✅ | — | `true` | 启用标记 |
| `is_deleted` | BOOLEAN | ✅ | — | `false` | 软删标记 |
| `created_at` | TIMESTAMPTZ | ✅ | — | `now()` | 创建时间 |
| `updated_at` | TIMESTAMPTZ | ✅ | — | `now()` | 更新时间（ORM 钩子自动） |

**索引**：
- `idx_style_tenant_active`：`(tenant_id, is_active, is_deleted)` — 列表过滤
- `idx_style_brand`：`(tenant_id, brand_id)` — 按品牌筛选
- `idx_style_category`：`(tenant_id, category)` — 按大类筛选
- `idx_style_search_trgm`：**GIN trgm 索引**（U02 强制建立，非可选）
  ```sql
  CREATE EXTENSION IF NOT EXISTS pg_trgm;
  CREATE INDEX idx_style_search_trgm ON style
    USING gin (
      (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
      gin_trgm_ops
    ) WHERE is_deleted = false;
  ```
  支撑 BR-U02-51 模糊匹配 P95 ≤ 300ms / 5 万行
- `uq_style_code`：UNIQUE `(tenant_id, style_code) WHERE is_deleted = false` — 业务键唯一

**RLS**：继承 `TenantScopedModel`，自动启用 `tenant_isolation` 策略（U01 实现）。

---

### 3.2 Sku — SKU

| 字段 | 类型 | 必填 | 唯一 | 默认 | 说明 |
|---|---|---|---|---|---|
| `id` | UUID | ✅ | ✅ | `gen_random_uuid()` | 主键 |
| `tenant_id` | UUID | ✅ | (id, tenant_id) | 来自 ctx | 租户外键 |
| `style_id` | UUID | ✅ | — | — | 所属款式（→ Style.id） |
| `sku_code` | VARCHAR(64) | ✅ | (tenant_id, sku_code) UNIQUE | — | SKU 编码（业务键） |
| `color` | VARCHAR(64) | ✅ | — | — | 颜色（自由字符串） |
| `size` | VARCHAR(32) | ✅ | — | — | 尺码（自由字符串） |
| `cost_price` | DECIMAL(10,2) | ❌ | — | NULL | 自产成本价（与 purchase_price 至少有一个） |
| `purchase_price` | DECIMAL(10,2) | ❌ | — | NULL | 外采采购价 |
| `base_price` | DECIMAL(10,2) | ❌ | — | NULL | 基本售价 |
| `sourcing_type` | VARCHAR(8) | ✅ | — | `'自产'` | SourcingType 枚举 |
| `is_active` | BOOLEAN | ✅ | — | `true` | 启用 |
| `is_deleted` | BOOLEAN | ✅ | — | `false` | 软删 |
| `created_at` | TIMESTAMPTZ | ✅ | — | `now()` | — |
| `updated_at` | TIMESTAMPTZ | ✅ | — | `now()` | — |

**索引**：
- `idx_sku_tenant_style`：`(tenant_id, style_id)` — 按款式查 SKU 列表（EP02-S05）
- `idx_sku_tenant_active`：`(tenant_id, is_active, is_deleted)` — 列表过滤
- `uq_sku_code`：UNIQUE `(tenant_id, sku_code)` — 业务键唯一

**外键约束**：
- `fk_sku_style`：`(style_id, tenant_id) REFERENCES style(id, tenant_id)` — 同租户引用 + ON DELETE RESTRICT（避免误删款式）

**RLS**：继承 `TenantScopedModel`。

---

### 3.3 Brand — 品牌字典

| 字段 | 类型 | 必填 | 唯一 | 默认 | 说明 |
|---|---|---|---|---|---|
| `id` | UUID | ✅ | ✅ | `gen_random_uuid()` | — |
| `tenant_id` | UUID | ✅ | — | 来自 ctx | — |
| `brand_code` | VARCHAR(32) | ✅ | (tenant_id, brand_code) UNIQUE | — | 品牌缩写 |
| `brand_name` | VARCHAR(128) | ✅ | — | — | 品牌全称 |
| `is_active` | BOOLEAN | ✅ | — | `true` | 停用标记 |
| `created_at` | TIMESTAMPTZ | ✅ | — | `now()` | — |
| `updated_at` | TIMESTAMPTZ | ✅ | — | `now()` | — |

**索引**：`uq_brand_code`：UNIQUE `(tenant_id, brand_code)`。  
**RLS**：继承 `TenantScopedModel`。

---

### 3.4 StyleDetailImage — 款式详情图（顺序敏感）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | UUID | ✅ | `gen_random_uuid()` | — |
| `tenant_id` | UUID | ✅ | 来自 ctx | — |
| `style_id` | UUID | ✅ | — | → Style.id |
| `attachment_id` | UUID | ✅ | — | → U01 attachment.id |
| `sort_order` | INT | ✅ | `0` | 0..N（前端顺序） |
| `created_at` | TIMESTAMPTZ | ✅ | `now()` | — |

**外键**：
- `fk_sdi_style`：`(style_id, tenant_id) REFERENCES style(id, tenant_id) ON DELETE CASCADE`（款式删则关联清理）
- `fk_sdi_attachment`：`(attachment_id) REFERENCES attachment(id) ON DELETE RESTRICT`

**RLS**：继承 `TenantScopedModel`。

---

## 4. 枚举定义（硬编码 Python Enum）

### 4.1 Category — 商品大类（女装）
```python
class Category(str, Enum):
    DRESS = "连衣裙"
    TOP = "上衣"
    PANTS = "裤装"
    SKIRT = "裙装"
    OUTERWEAR = "外套"
    SET = "套装"
    ACCESSORY = "配饰"
```
> U09 阶段改为字典表（Migration 重命名为 `category_legacy_enum`，新表 `category` + 数据迁移）。

### 4.2 Season — 季节
```python
class Season(str, Enum):
    SPRING = "春"
    SUMMER = "夏"
    AUTUMN = "秋"
    WINTER = "冬"
    ALL = "四季"
```

### 4.3 Gender — 性别
```python
class Gender(str, Enum):
    FEMALE = "女"
    MALE = "男"
    UNISEX = "中性"
    KIDS = "童"
```

### 4.4 DesignStatus — 设计状态（U02 仅 2 值，U10a 扩 7 值）
```python
class DesignStatus(str, Enum):
    DESIGNING = "设计中"
    BULK = "大货"
    # U10a 阶段补充：PATTERN_MAKING、CRAFT、PRICING、SAMPLING、CONFIRMING
```

### 4.5 SourcingType — SKU 来源类型
```python
class SourcingType(str, Enum):
    SELF_PRODUCED = "自产"      # 用 cost_price
    EXTERNAL_PURCHASE = "外采"  # 用 purchase_price
    MIXED = "混合"              # 两个都可填
```

---

## 5. 与 U01 的关系

| 关系 | 来源单元 | 引用方式 |
|---|---|---|
| `tenant_id` | U01 | 继承 `TenantScopedModel`，自动注入与 RLS |
| `owner_id` | U01 | FK → `user.id`（不级联，删用户时 `SET NULL`） |
| `main_image_id`, `attachment_id` | U01 | FK → `attachment.id` |
| 审计 | U01 | `@audit("style.create")` 装饰器 + AuditService |
| 权限装饰器 | U01 | `@require_permission("product:write")` |
| 错误码 | U01 | `ResourceConflictError(409)`, `ValidationError(422)`, `PermissionDeniedError(403)` |

---

## 6. 数据约束（数据库层）

### 6.1 业务键唯一性
- `style_code` 在租户内唯一：`UNIQUE (tenant_id, style_code) WHERE is_deleted = false`（部分索引，软删后释放编码）
- `sku_code` 在租户内唯一：同上
- `brand_code` 在租户内唯一：`UNIQUE (tenant_id, brand_code)`

### 6.2 引用完整性
- `sku.style_id` ON DELETE RESTRICT（不允许通过删 style 级联删 sku，必须先处理 sku）
- `style_detail_image.style_id` ON DELETE CASCADE（删 style 时清理详情图关联，附件本身保留）
- `style.owner_id` ON DELETE SET NULL（删 user 时 owner_id 置空）

### 6.3 价格非负
- CHECK 约束：`cost_price >= 0`, `purchase_price >= 0`, `base_price >= 0`（NULL 允许）

### 6.4 sourcing_type 与价格字段一致性（业务规则，不在 DB CHECK）
- `sourcing_type='自产'` 应至少填 `cost_price`
- `sourcing_type='外采'` 应至少填 `purchase_price`
- `sourcing_type='混合'` 两个都可填
- 在 service 层验证（不在 DB 层强制，便于历史数据导入）

---

## 7. 演化路线图（与后续单元对齐）

| 阶段 | 单元 | 演化项 |
|---|---|---|
| MVP | U02（本单元） | 上述全部字段 |
| MVP | U04 | promotion 表 FK 到 sku.id；创建 promotion 时**快照** sku.cost_price 到 promotion.cost_price_snapshot |
| MVP | U06b | StyleSkuImportAdapter 按 style_code/sku_code 幂等 upsert |
| V1 | U09 | 字段级权限落地：`cost_price`, `purchase_price` 按角色屏蔽；移除 service 层 hardcode；`category` 改字典表 |
| V1 | U10a | DesignStatus 扩为 7 状态 + 状态机（设计→打版→工艺→核价→打样→确认→大货） |
| V1 | U09 | PlatformProduct 表加入（千牛商品 ID 映射） |
| V2 | U16 | order 表 FK 到 sku.id；快照 base_price |
| V2 | U17 | Bundle / BundleItem 表（套装组合） |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 全部实体继承 `TenantScopedModel`（U01 已实现） | ✅ |
| 所有 FK 都带 `tenant_id` 同租户校验 | ✅ |
| 所有业务键 UNIQUE 都含 `tenant_id` | ✅ |
| 软删 + 部分 UNIQUE 索引（避免软删后 code 占位） | ✅ |
| 价格类型统一 DECIMAL(10,2) | ✅ |
| 与 U10a / U16 / U17 演化路径预留 | ✅ |
