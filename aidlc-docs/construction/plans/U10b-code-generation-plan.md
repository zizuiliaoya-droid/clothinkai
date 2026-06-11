# U10b 代码生成计划（Code Generation Plan）

> 单元：U10b — 平台商品映射（EP02-S07）
> 分批：**单批** + Build & Test（小单元）
> Build & Test：Docker PG16:5553 + Redis7:6408 + Py3.12

---

## 1. 步骤

- [x] 1.1 modules/product/platform_product_models.py（PlatformProduct ORM）
- [x] 1.2 modules/product/platform_product_schemas.py
- [x] 1.3 modules/product/platform_product_service.py（PlatformProductService）
- [x] 1.4 modules/product/platform_product_api.py（router /api/platform-products）
- [x] 1.5 modules/product/permissions.py 追加 SCOPE_PLATFORM_READ/WRITE
- [x] 1.6 app/main.py 注册 platform_product_router
- [x] 1.7 alembic/versions/014_u10b_create_platform_product.py
- [x] 1.8 tests/integration/test_platform_product.py
- [x] 1.9 tests/api/test_platform_product_api.py

### Build & Test
- [x] B.1 Docker PG16:5553 + Redis7:6408；alembic upgrade head（含 014）；U10b 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部步骤 + Build & Test。**
