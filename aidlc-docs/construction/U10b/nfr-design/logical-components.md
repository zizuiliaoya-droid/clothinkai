# U10b 逻辑组件（Logical Components）

> 单元：U10b — 平台商品映射
> 新建 4 文件（modules/product 追加）+ permissions 追加 + migration 014 + main 注册

---

## 1. 新建组件

| 文件 | 职责 |
|---|---|
| `modules/product/platform_product_models.py` | PlatformProduct ORM（TenantScopedModel + RLS） |
| `modules/product/platform_product_schemas.py` | PlatformProductCreate/Update/Response + ListResponse |
| `modules/product/platform_product_service.py` | PlatformProductService（create/create_or_update/find_by_platform_id/update/delete/list） |
| `modules/product/platform_product_api.py` | router（prefix /api/platform-products，5~6 端点）；挂 tags=["product"] |

## 2. 修改组件

| 组件 | 改动 |
|---|---|
| `modules/product/permissions.py` | +SCOPE_PLATFORM_READ / SCOPE_PLATFORM_WRITE |
| `app/main.py` | +import platform_product_router + include_router |
| `alembic/versions/014_u10b_create_platform_product.py` | 新建（1 表 + RLS + UNIQUE + FK + idx + scope seed） |

## 3. 复用组件

| 复用 | 来源 |
|---|---|
| StyleRepository.get_by_id / SkuRepository.get_by_id | U02（引用校验） |
| AuditService | U01 |
| IntegrityError catch 模式 | U06a |

## 4. 依赖图

```
platform_product_api → PlatformProductService
    → StyleRepository / SkuRepository (U02)
    → AuditService (U01)
    → session (DB)
```
- 无循环依赖；platform_product 仅依赖 product 同模块 + core。

## 5. migration 014

| 表 | 约束 |
|---|---|
| platform_product | tenant_id + RLS + UNIQUE(tenant_id, platform, platform_id) + FK(style_id → style RESTRICT) + FK(sku_id → sku SET NULL) + idx(tenant_id, style_id) |

scope seed：product.platform:read / product.platform:write（绑 merchandiser write + operations read，幂等）。

## 6. 测试文件

| 文件 | 类型 | 覆盖 |
|---|---|---|
| tests/integration/test_platform_product.py | 集成 | create/409/upsert 幂等/find 命中+未命中/引用 422/跨租户/删除 |
| tests/api/test_platform_product_api.py | API | 鉴权 401 + OpenAPI |

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 4 文件 + permissions 追加 + migration 014 + main 注册 | ✅ §1/§2 |
| 复用 U02 repo + U01 audit + U06a IntegrityError | ✅ §3 |
| 无循环依赖 | ✅ §4 |
| 测试覆盖集成 + API | ✅ §6 |
| 与 P-U10b-01 一致 | ✅ |
