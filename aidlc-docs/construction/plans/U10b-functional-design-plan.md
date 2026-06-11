# U10b 功能设计计划（Functional Design Plan）

> 单元：U10b — 平台商品映射（EP02-S07）
> 依赖：U02（Style/Sku）
> 节奏：Functional Design 阶段 = 本计划 + 3 文档，同一轮生成（小单元）

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 模块落点
- [Answer] 追加到 `modules/product`：新建 platform_product 模型 + PlatformProductService + 端点（挂在 product_router 或新 platform_product router）；不新建独立模块包（与 unit-of-work "modules/product 追加" 一致）。

### Q2 — 实体字段
- [Answer] platform_product：id/tenant_id/created_at/updated_at + platform(VARCHAR 16) + platform_id(VARCHAR 64) + style_id(FK style 必填) + sku_id(FK sku 可空，款级或 SKU 级映射) + title(VARCHAR 可空，平台商品标题快照) + is_active(bool)。

### Q3 — 唯一约束
- [Answer] UNIQUE(tenant_id, platform, platform_id)；重复创建 → 409 PLATFORM_PRODUCT_CONFLICT。

### Q4 — platform 取值
- [Answer] VARCHAR 自由值（qianniu/taobao/douyin/wanxiangtai 等），不硬编码 Python Enum（便于 U13 各采集平台扩展）；service 层可选白名单校验（MVP 不强制）。

### Q5 — create vs upsert
- [Answer] create_or_update：按 (tenant, platform, platform_id) upsert（存在则更新 style_id/sku_id/title）；HTTP create 端点用 create（重复 409）；create_or_update 供 U13/U14 导入路径复用（幂等）。

### Q6 — 反查
- [Answer] find_by_platform_id(platform, platform_id) → PlatformProduct|None（关联 style/sku）；供千牛/平台日报导入时关联内部款式（U13/U14）。

### Q7 — 引用校验
- [Answer] 创建/更新时校验 style_id 存在且未软删（同租户）；sku_id 若提供则校验存在 + 属于该 style；跨租户由 RLS + 显式校验返回 422 INVALID_STYLE_REFERENCE / INVALID_SKU_REFERENCE。

### Q8 — 删除
- [Answer] 支持删除映射（硬删，无下游强引用约束 MVP）；admin / 跟单可删。

### Q9 — 权限
- [Answer] 复用 product 模块 scope：product.platform:read / product.platform:write；merchandiser 经 product.*:* 通配命中，admin 经 * 命中；migration 014 seed 这两个 scope（绑 merchandiser + operations read，幂等）。

### Q10 — migration
- [Answer] migration 014（接 013）：platform_product 表（1 表 + RLS + UNIQUE + 2 FK + idx）+ product.platform:read/write scope seed。

---

## 2. 执行步骤

- [x] 2.1 `U10b/functional-design/domain-entities.md`：PlatformProduct 实体 + 字段 + UNIQUE + FK + ER + 与 Style/Sku 关系
- [x] 2.2 `U10b/functional-design/business-rules.md`：BR-U10b-01~ 唯一性/引用校验/upsert 语义/反查/删除/权限/错误码
- [x] 2.3 `U10b/functional-design/business-logic-model.md`：UC（create/create_or_update/find_by_platform_id/list/delete）+ 导入关联契约（U13/U14）+ 跨单元
- [x] 2.4 诊断器无警告 + 与 EP02-S07 + application-design PlatformProductService 一致

---

**等待用户"继续"；本轮直接生成 3 份功能设计文档。**
