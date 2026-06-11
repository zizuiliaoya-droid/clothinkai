# U10b 业务规则（平台商品映射）

> 单元：U10b — EP02-S07

---

## 1. 唯一性（BR-U10b-01~02）

- **BR-U10b-01**：UNIQUE(tenant_id, platform, platform_id)；同租户同平台同商品 ID 仅一条映射。
- **BR-U10b-02**：HTTP create 端点遇重复 → 409 PLATFORM_PRODUCT_CONFLICT（返回已存在记录 id 供前端跳转）。

## 2. 引用校验（BR-U10b-10~12）

- **BR-U10b-10**：创建/更新校验 style_id 存在且未软删（同租户，RLS）；否则 422 INVALID_STYLE_REFERENCE。
- **BR-U10b-11**：sku_id 若提供，校验存在 + 未软删 + 属于该 style_id；否则 422 INVALID_SKU_REFERENCE。
- **BR-U10b-12**：sku_id 可空（款级映射）。

## 3. upsert 语义（BR-U10b-20~22）

- **BR-U10b-20**：create_or_update 按 (tenant, platform, platform_id) 查找；存在则更新 style_id/sku_id/title/is_active，不存在则插入（幂等）。
- **BR-U10b-21**：create_or_update 供 U13/U14 导入路径复用（不暴露 HTTP，内部调用）；区分 audit（platform_product.create_via_import / update_via_import）。
- **BR-U10b-22**：HTTP create 严格新建（重复 409）；HTTP update（PUT by id）更新映射字段。

## 4. 反查（BR-U10b-30）

- **BR-U10b-30**：find_by_platform_id(platform, platform_id) → PlatformProduct|None；千牛/平台日报导入时用于关联内部 style/sku（无映射 → 返回 None，导入侧记录未关联，不阻塞）。

## 5. 删除（BR-U10b-40）

- **BR-U10b-40**：硬删映射（DELETE）；admin / 跟单可删；删除不影响 style/sku 本身。

## 6. 权限（BR-U10b-50~51）

| 动作 | scope | 角色 |
|---|---|---|
| 创建/更新/删除 | product.platform:write | merchandiser（product.*:* 通配）/ admin（*） |
| 查询/反查 | product.platform:read | merchandiser / operations / admin + product.*:read 角色 |

- **BR-U10b-50**：migration 014 seed product.platform:read / product.platform:write，绑 merchandiser(write) + operations(read)；admin 经 * 通配。
- **BR-U10b-51**：所有写操作写 audit（platform/platform_id/style_id）。

## 7. 错误码矩阵

| 场景 | code | HTTP |
|---|---|---|
| (platform, platform_id) 重复 | PLATFORM_PRODUCT_CONFLICT | 409 |
| style_id 不存在/软删 | INVALID_STYLE_REFERENCE | 422 |
| sku_id 不存在/不属于 style | INVALID_SKU_REFERENCE | 422 |
| 映射不存在（get/update/delete by id） | PLATFORM_PRODUCT_NOT_FOUND | 404 |
| 无权限 | PERMISSION_DENIED | 403 |

## 8. 多租户

- platform_product TenantScopedModel + RLS；列表/反查显式 tenant 过滤（防御 + 测试确定性，同既有约定）。
