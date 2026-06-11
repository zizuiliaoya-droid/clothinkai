# U10b 基础设施设计（Infrastructure Design）

> 单元：U10b — 平台商品映射
> 结论：零新服务/依赖/桶/环境变量/Celery；唯一增量 = migration 014（1 表 + scope seed）

---

## 1. 基础设施增量总览

| 维度 | 增量 |
|---|---|
| Zeabur 服务 | 无 |
| 数据库表 | +1（platform_product） |
| 依赖 | 无 |
| 环境变量 | 无 |
| Celery | 无 |
| R2 桶 | 无 |
| Prometheus | 无 |

---

## 2. migration 014（1 表 + scope seed）

```text
# alembic/versions/014_u10b_create_platform_product.py（接 013）
表 platform_product
  id PK UUID / tenant_id NOT NULL / created_at / updated_at
  platform VARCHAR(16) NOT NULL
  platform_id VARCHAR(64) NOT NULL
  style_id UUID NOT NULL FK(style) RESTRICT
  sku_id UUID FK(sku) SET NULL
  title VARCHAR(255)
  is_active bool default true
  UNIQUE(tenant_id, platform, platform_id)  -- uq_platform_product_tenant_plat_platid
  idx(tenant_id, style_id)
  RLS ENABLE + policy
scope seed（幂等）：
  product.platform:read → operations
  product.platform:write → merchandiser
  （admin 经 *；merchandiser 经 product.*:* 已覆盖；显式 seed 为存在性 + operations read）
```

---

## 3. 复用清单

| 复用项 | 来源 |
|---|---|
| backend FastAPI / migrate.yml / ci.yml | U01 |
| style / sku 表 FK | U02 |
| RLS helper | U01 |
| IntegrityError catch 模式 | U06a |

---

## 4. 部署 / 回滚

- **部署**：代码 + migration 014 同批；空表无回填 + scope seed 幂等。
- **回滚**：`alembic downgrade -1`（删表 + 删 scope）；代码回滚移除 router。
- **零停机**：CREATE TABLE 不锁既有表。

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新服务/依赖/桶/环境变量 | ✅ |
| migration 014 = 1 表 + RLS + scope seed | ✅ |
| 部署/回滚无回填 | ✅ |

> infrastructure-design.md spec-format 假阳性 IGNORE。
