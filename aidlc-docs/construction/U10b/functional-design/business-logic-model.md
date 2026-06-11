# U10b 业务逻辑模型（平台商品映射）

> 单元：U10b — EP02-S07；5 个 Use Case + 导入关联契约

---

## 1. 用例总览

| UC | 动作 | 端点 / 入口 | 故事 |
|---|---|---|---|
| UC-1 | create | POST /api/platform-products | EP02-S07 |
| UC-2 | update | PUT /api/platform-products/{id} | EP02-S07 |
| UC-3 | create_or_update | 内部调用（U13/U14 导入） | EP02-S07 |
| UC-4 | find_by_platform_id | GET /api/platform-products/lookup?platform&platform_id | EP02-S07 |
| UC-5 | list / delete | GET /api/platform-products；DELETE /{id} | EP02-S07 |

---

## 2. 核心用例流程

### UC-1 create（S07）
```
1. 校验 product.platform:write 权限
2. 校验 style_id 存在未软删（否则 422 INVALID_STYLE_REFERENCE）
3. sku_id 若提供 → 校验存在 + 属于 style（否则 422 INVALID_SKU_REFERENCE）
4. 插入 platform_product；UNIQUE(tenant, platform, platform_id) 冲突 → 409 PLATFORM_PRODUCT_CONFLICT（返回已存在 id）
5. audit platform_product.create
```

### UC-3 create_or_update（导入路径，幂等）
```
1. 按 (tenant, platform, platform_id) 查找
2. 存在 → 更新 style_id/sku_id/title/is_active（audit update_via_import）
   不存在 → 插入（audit create_via_import）
3. 引用校验同 create
4. 不暴露 HTTP；U13/U14 通过 from app.modules.product...service import PlatformProductService 调用
```

### UC-4 find_by_platform_id（反查）
```
1. 校验 product.platform:read
2. SELECT platform_product WHERE tenant + platform + platform_id
3. 返回映射（含 style/sku）或 None（无映射不报错，导入侧记录未关联）
```

### UC-5 delete
```
1. 校验 product.platform:write
2. 加载 by id（不存在 → 404）
3. 硬删 + audit
```

---

## 3. 导入关联契约（U13/U14）

```
千牛日报行含 platform_product_id=123456
  → PlatformProductService.find_by_platform_id("qianniu", "123456")
      → 命中：关联 row.style_id / row.sku_id → 写入报表/采集表
      → 未命中：记录 unmatched（platform_id 原样保留，style_id NULL），不阻塞导入
```

- 与 U02 款号↔简称匹配（BR-U02-50/51）同理念：未匹配警告不阻塞。

---

## 4. 跨单元契约

| 依赖 | 复用 | 用途 |
|---|---|---|
| U02 StyleRepository / SkuRepository | get_by_id | 引用校验 |
| U01 audit | AuditService.log | 写操作审计 |
| U13/U14 | find_by_platform_id / create_or_update | 平台日报关联内部款式 |

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 覆盖 EP02-S07（UNIQUE + 重复 409 + 导入关联） | ✅ §1/§3 |
| 与 application-design PlatformProductService（create_or_update + find_by_platform_id）一致 | ✅ |
| 引用校验 + upsert 幂等 + 反查不阻塞 | ✅ §2/§3 |
| 复用 U02/U01，无新依赖 | ✅ §4 |
