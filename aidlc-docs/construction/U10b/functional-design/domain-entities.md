# U10b 领域实体（平台商品映射）

> 单元：U10b — 平台商品映射（EP02-S07）
> 依赖：U02（Style/Sku）
> 特征：1 张新表 platform_product；追加到 modules/product

---

## 1. 实体总览

| 实体 | 类型 | 来源 |
|---|---|---|
| PlatformProduct | ORM（TenantScopedModel + RLS） | U10b 新建 platform_product |
| Style / Sku | ORM（已存在） | U02；被映射的内部款式/SKU |

---

## 2. PlatformProduct（platform_product）

| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / created_at / updated_at | TenantScopedModel | — |
| platform | VARCHAR(16) NOT NULL | 平台标识（qianniu/taobao/douyin/wanxiangtai…，自由值不硬编码 Enum） |
| platform_id | VARCHAR(64) NOT NULL | 平台商品 ID（如千牛商品 ID 123456） |
| style_id | UUID FK(style) NOT NULL | 关联内部款式 |
| sku_id | UUID FK(sku) NULL | 关联内部 SKU（可空 = 款级映射） |
| title | VARCHAR(255) NULL | 平台商品标题快照（可选） |
| is_active | bool NOT NULL default true | 启用标记 |

### 约束 / 索引
- UNIQUE(tenant_id, platform, platform_id)（BR-U10b-01）
- FK(style_id → style.id) ondelete RESTRICT
- FK(sku_id → sku.id) ondelete SET NULL
- idx(tenant_id, style_id)（按款式反查映射）

---

## 3. ER 图

```mermaid
erDiagram
    STYLE ||--o{ PLATFORM_PRODUCT : mapped_by
    SKU ||--o{ PLATFORM_PRODUCT : mapped_by
    PLATFORM_PRODUCT {
        string platform
        string platform_id
        uuid style_id
        uuid sku_id
        string title
        bool is_active
    }
```

---

## 4. 关系与语义

- 一个内部 style/sku 可对应多个平台商品（不同平台 / 同平台多链接）。
- 一个 (platform, platform_id) 在租户内唯一映射到一个内部款式（UNIQUE）。
- sku_id 可空：部分平台商品仅能映射到款级（无法精确到颜色尺码）。

---

## 5. 演化
- U13 采集 Worker / U14 报表：通过 find_by_platform_id 将平台日报数据关联到内部 style/sku。
- 未来可加 platform_account_id（多店铺）维度。
