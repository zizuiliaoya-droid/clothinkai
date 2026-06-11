# U10b NFR 设计模式（NFR Design Patterns）

> 单元：U10b — 平台商品映射
> 增量模式：P-U10b-01（UNIQUE 冲突 + upsert 幂等 + 引用校验 + 反查）
> 继承：U01 RLS/audit、U02 StyleRepository/SkuRepository

---

## P-U10b-01 — PlatformProductService CRUD + 并发安全 + 幂等 upsert

### create（HTTP POST，严格新建）

```python
async def create(self, payload, user):
    # 1. 引用校验
    style = await self._style_repo.get_by_id(payload.style_id)
    if style is None:
        raise InvalidStyleReferenceError(...)
    if payload.sku_id:
        sku = await self._sku_repo.get_by_id(payload.sku_id)
        if sku is None or sku.style_id != payload.style_id:
            raise InvalidSkuReferenceError(...)

    # 2. 插入（依赖 UNIQUE 兜底并发）
    pp = PlatformProduct(
        platform=payload.platform, platform_id=payload.platform_id,
        style_id=payload.style_id, sku_id=payload.sku_id, title=payload.title,
    )
    self._session.add(pp)
    try:
        await self._session.flush()
    except IntegrityError as exc:
        await self._session.rollback()
        if "uq_platform_product_" in str(exc.orig):
            existing = await self._find(payload.platform, payload.platform_id)
            raise PlatformProductConflictError(
                details={"existing_id": str(existing.id) if existing else None}
            )
        raise

    # 3. audit
    await self._audit.log("platform_product.create", ...)
    await self._session.commit()
    return pp
```

- 与 U06a 导入框架 IntegrityError 模式一致（try-flush-except）。

### create_or_update（内部导入路径，幂等）

```python
async def create_or_update(self, *, platform, platform_id, style_id, sku_id, title, user):
    # 引用校验（同 create）
    existing = await self._find(platform, platform_id)   # SELECT WHERE tenant+platform+platform_id
    if existing:
        existing.style_id = style_id
        existing.sku_id = sku_id
        existing.title = title
        existing.is_active = True
        await self._session.flush()
        await self._audit.log("platform_product.update_via_import", ...)
    else:
        pp = PlatformProduct(...)
        self._session.add(pp)
        await self._session.flush()
        await self._audit.log("platform_product.create_via_import", ...)
    await self._session.commit()
    return existing or pp
```

### find_by_platform_id（反查，U13/U14 用）

```python
async def find_by_platform_id(self, platform: str, platform_id: str) -> PlatformProduct | None:
    stmt = select(PlatformProduct).where(
        PlatformProduct.platform == platform,
        PlatformProduct.platform_id == platform_id,
    )
    return (await self._session.execute(stmt)).scalar_one_or_none()
    # 命中 UNIQUE 索引（tenant+platform+platform_id），O(1)
```

### 引用校验防跨租户

- `get_by_id(style_id)` / `get_by_id(sku_id)` 经 ORM tenant 钩子 + RLS 自动限本租户；跨租户 → None → 422。
- FK(style RESTRICT / sku SET NULL) 数据库层兜底。

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| create IntegrityError→409（不依赖先查后插） | ✅ |
| create_or_update SELECT→insert/update 幂等 | ✅ |
| 引用校验 RLS 防跨租户 | ✅ |
| find_by_platform_id 命中 UNIQUE 索引 | ✅ |
| 与 U06a IntegrityError 模式一致 | ✅ |
