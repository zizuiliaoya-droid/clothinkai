# U10b 技术栈决策（Tech Stack Decisions）

> 单元：U10b — 平台商品映射
> 原则：复用 U01-U10a 技术栈，**零新增运行时依赖**；migration 014 = 1 表 + scope seed

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 状态 |
|---|---|---|
| ORM / CRUD | SQLAlchemy 2.0 async | ✅ 复用 |
| Schema | pydantic 2.x | ✅ |
| 唯一约束 | PG UNIQUE + IntegrityError | ✅ 既有 |

> requirements.txt 不改动。

---

## 2. 组件落点（modules/product 追加）

| 组件 | 路径 |
|---|---|
| 模型 | `modules/product/platform_product_models.py`（PlatformProduct）或并入 models.py |
| Schema | `modules/product/platform_product_schemas.py` |
| Service | `modules/product/platform_product_service.py`（PlatformProductService） |
| Repository | 并入 service 或 `platform_product_repository.py` |
| API | `modules/product/platform_product_api.py`（router prefix /api/platform-products）或并入 product api |
| 权限 scope | 复用 product 模块；新增常量 product.platform:read/write |
| migration | `alembic/versions/014_u10b_create_platform_product.py` |

---

## 3. PlatformProductService 关键方法

```python
class PlatformProductService:
    async def create(self, payload, user) -> PlatformProductResponse: ...
        # 引用校验 + insert；IntegrityError → 409 PlatformProductConflict
    async def create_or_update(self, *, platform, platform_id, style_id, sku_id, title, user) -> PlatformProduct: ...
        # SELECT by (tenant,platform,platform_id) → update/insert（幂等，供 U13/U14）
    async def find_by_platform_id(self, platform, platform_id) -> PlatformProduct | None: ...
    async def update(self, id, payload, user) / delete(self, id, user) / list(...): ...
```

- 引用校验复用 U02 StyleRepository.get_by_id / SkuRepository.get_by_id（RLS 本租户）。
- create 捕获 IntegrityError（UNIQUE 冲突）→ 409，避免 TOCTOU。

---

## 4. migration 014（1 表 + scope seed）

```python
# 014_u10b_create_platform_product.py（接 013）
# platform_product：tenant_id + RLS + UNIQUE(tenant_id, platform, platform_id)
#   + FK(style_id→style RESTRICT) + FK(sku_id→sku SET NULL) + idx(tenant_id, style_id)
# scope seed（幂等）：product.platform:read → operations；product.platform:write → merchandiser
#   （admin 经 * 通配；merchandiser 经 product.*:* 已覆盖，显式 seed 仅为存在性 + operations read）
```

---

## 5. 测试落点

| 文件 | 类型 |
|---|---|
| tests/integration/test_platform_product.py | create/409/create_or_update 幂等/find/引用校验/跨租户/删除 |
| tests/api/test_platform_product_api.py | 鉴权 + OpenAPI |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1 |
| modules/product 追加（不新建独立包） | ✅ §2 |
| create IntegrityError→409 + create_or_update 幂等 | ✅ §3 |
| migration 014 1 表 + scope seed | ✅ §4 |
| 复用 U02 repo 引用校验 | ✅ §3 |
