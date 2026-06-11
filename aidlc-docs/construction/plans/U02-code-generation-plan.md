# U02 代码生成计划（Code Generation Plan）

> 单元：U02 — 商品 / SKU 基础  
> 阶段：MVP 第 2 个单元  
> 上一单元：U01（认证 + 多租户基础设施已就绪）

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 | 说明 |
|---|---|---|
| EP02-S01 | MVP | 跟单创建款式 |
| EP02-S02 | MVP | 跟单创建 SKU |
| EP02-S03 | MVP | 编辑款式信息 |
| EP02-S04 | MVP | 编辑 SKU 成本/价格 |
| EP02-S05 | MVP | 按款式查询 SKU |
| EP02-S06 | MVP | 款号↔商品简称双向关联 |

### 1.2 依赖
- **依赖**：U01 全部组件（已完成）
- **被依赖**：U04 (推广) / U06b (导入) / U09 (字段权限) / U10a (设计) / U16 (订单) / U17 (套装)

### 1.3 工作区根目录
`e:\work\Pycharm_Projection\eCommerce_v4\`

### 1.4 设计文档引用
- functional-design：`U02/functional-design/{domain-entities, business-rules, business-logic-model}.md`
- nfr-requirements：`U02/nfr-requirements/{nfr-requirements, tech-stack-decisions}.md`
- nfr-design：`U02/nfr-design/{nfr-design-patterns, logical-components}.md`
- infrastructure-design：`U02/infrastructure-design/{infrastructure-design, deployment-architecture}.md`
- shared-infrastructure：`aidlc-docs/construction/shared-infrastructure.md`

### 1.5 项目结构（沿用 U01 4 层架构）
```
backend/app/modules/product/         # U02 业务模块（新增）
├── __init__.py
├── enums.py                         # Category/Season/Gender/DesignStatus/SourcingType
├── models.py                        # Style/Sku/Brand/StyleDetailImage ORM
├── schemas.py                       # Style/Sku/Match Pydantic
├── brand_schemas.py                 # Brand Pydantic
├── permissions.py                   # product:* / brand:* 权限声明
├── legacy_field_permissions.py      # 临时硬编码（U09 清理）
├── exceptions.py                    # product 业务异常
├── domain.py                        # 业务规则验证
├── repository.py                    # StyleRepository / SkuRepository
├── brand_repository.py              # BrandRepository
├── service.py                       # StyleService / SkuService
├── brand_service.py                 # BrandService
├── deps.py                          # FastAPI 依赖注入
└── api.py                           # FastAPI Router

backend/app/core/exceptions.py       # 修改：追加 FieldPermissionDenied
backend/app/core/metrics.py          # 修改：追加 2 个 product 指标
backend/app/main.py                  # 修改：注册 product router
backend/app/modules/auth/default_roles.py  # 修改：追加 product/brand 默认角色

backend/alembic/versions/004_u02_create_product_tables.py  # 新增

backend/tests/
├── unit/
│   ├── test_style_domain.py         # 新增
│   ├── test_sku_domain.py           # 新增
│   └── test_field_permissions.py    # 新增（PRICE_VISIBLE_ROLES 矩阵）
├── integration/
│   ├── test_style_crud.py           # 新增
│   ├── test_sku_crud.py             # 新增
│   ├── test_brand_crud.py           # 新增
│   ├── test_style_match.py          # 新增（精确 + 模糊 + 降级语义）
│   └── test_sku_upsert.py           # 新增（FB7 + 并发）
├── api/
│   └── test_product_api.py          # 新增
└── performance/
    └── test_match_perf.py           # 新增（@pytest.mark.performance）

frontend/src/features/product/       # 最简骨架（新增）
├── api.ts                           # axios 包装 product 端点
└── types.ts                         # 类型声明

aidlc-docs/construction/U02/code/
├── README.md                        # 新增（文件清单）
├── api-endpoints.md                 # 新增（13 端点说明）
└── test-coverage.md                 # 新增（测试矩阵）
```

---

## 2. 执行步骤总览

按"业务模块 → 横切修改 → migration → 测试 → 前端 → 文档摘要"顺序执行。每完成一步标记 `[x]`。

### Step 1 — modules/product 基础（枚举 + ORM + Schema）
- [x] 1.1 `__init__.py` 模块导出
- [x] 1.2 `enums.py`（Category / Season / Gender / DesignStatus / SourcingType 5 个 Enum）
- [x] 1.3 `models.py`（Style / Sku / Brand / StyleDetailImage ORM，继承 TenantScopedModel）
- [x] 1.4 `schemas.py`（StyleCreate/Update/Response, SkuCreate/Update/Response, MatchResponse 13 个 Pydantic）
- [x] 1.5 `brand_schemas.py`（BrandCreate/Update/Response 3 个）
- [x] 1.6 `permissions.py`（product:read/write/delete + brand:read/write/delete 字符串常量）
- [x] 1.7 `legacy_field_permissions.py`（PRICE_VISIBLE_ROLES + docstring）
- [x] 1.8 `exceptions.py`（product 业务异常子类）

### Step 2 — modules/product 业务层（Domain + Repository）
- [x] 2.1 `domain.py`（StyleDomain / SkuDomain，业务规则验证 + dict diff + audit_safe_changes）
- [x] 2.2 `repository.py`（StyleRepository / SkuRepository，含 search_by_keyword + upsert_atomic）
- [x] 2.3 `brand_repository.py`（BrandRepository CRUD）

### Step 3 — modules/product 服务层（Service + Deps + API）
- [x] 3.1 `service.py`（StyleService 11 方法 + SkuService 8 方法 + 私有方法）
- [x] 3.2 `brand_service.py`（BrandService 5 方法）
- [x] 3.3 `deps.py`（get_style_service / get_sku_service / get_brand_service）
- [x] 3.4 `api.py`（product_router，13+ 端点）

### Step 4 — 横切修改（U01 文件追加）
- [x] 4.1 修改 `core/exceptions.py` 追加 `FieldPermissionDenied`
- [x] 4.2 修改 `core/metrics.py` 追加 `style_search_results_count` + `sku_upsert_total`
- [x] 4.3 修改 `main.py` 注册 product router
- [x] 4.4 修改 `modules/auth/default_roles.py` 追加 product/brand 默认权限映射

### Step 5 — Alembic 迁移
- [x] 5.1 `alembic/versions/004_u02_create_product_tables.py`（pg_trgm 扩展 + 4 表 + 12 索引 + 4 RLS）
- [x] 5.2 verify upgrade / downgrade 函数对称

### Step 6 — 单元测试（business logic）
- [x] 6.1 `tests/unit/test_style_domain.py`（业务规则单测）
- [x] 6.2 `tests/unit/test_sku_domain.py`（业务规则单测）
- [x] 6.3 `tests/unit/test_field_permissions.py`（PRICE_VISIBLE_ROLES 矩阵）

### Step 7 — 集成测试（CRUD + match + upsert）
- [x] 7.1 `tests/integration/test_style_crud.py`（创建 / 编辑 / 软删 / 列表）
- [x] 7.2 `tests/integration/test_sku_crud.py`（创建 / 编辑 / by-style / 软删 / 引用检查）
- [x] 7.3 `tests/integration/test_brand_crud.py`（CRUD）
- [x] 7.4 `tests/integration/test_style_match.py`（精确 / 模糊 / 业务未匹配 / 系统失败降级语义）
- [x] 7.5 `tests/integration/test_sku_upsert.py`（FB7 + 100 并发 + audit 区分）

### Step 8 — API 端到端测试
- [x] 8.1 `tests/api/test_product_api.py`（13 端点端到端）

### Step 9 — 性能基准测试
- [x] 9.1 `tests/performance/test_match_perf.py`（5 万 style P95 ≤ 300ms）

### Step 10 — Frontend 最简骨架
- [x] 10.1 `frontend/src/features/product/types.ts`
- [x] 10.2 `frontend/src/features/product/api.ts`

### Step 11 — 文档摘要
- [x] 11.1 `aidlc-docs/construction/U02/code/README.md`（文件清单 + 故事追溯矩阵）
- [x] 11.2 `aidlc-docs/construction/U02/code/api-endpoints.md`（13 端点说明）
- [x] 11.3 `aidlc-docs/construction/U02/code/test-coverage.md`（测试矩阵）

### Step 12 — 完成校验
- [x] 12.1 全部 Python 文件诊断器无警告
- [x] 12.2 Plan 全部 [x] 标记
- [x] 12.3 故事追溯：EP02-S01~S06 全部映射

---

## 3. 故事追溯矩阵

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP02-S01 创建款式 | `service.StyleService.create_style` + `api.create_style` | `tests/integration/test_style_crud.py:test_create_style` |
| EP02-S02 创建 SKU | `service.SkuService.create_sku` + `api.create_sku` | `tests/integration/test_sku_crud.py:test_create_sku` |
| EP02-S03 编辑款式 | `service.StyleService.update_style` + `api.update_style` | `tests/integration/test_style_crud.py:test_update_style` |
| EP02-S04 编辑 SKU 成本/价格 | `service.SkuService.update_sku` + 字段权限 | `tests/integration/test_sku_crud.py:test_update_cost_price_*` |
| EP02-S05 按款式查询 SKU | `service.SkuService.list_by_style` + `api.list_by_style` | `tests/integration/test_sku_crud.py:test_by_style` |
| EP02-S06 双向关联 | `service.StyleService.match_by_code/keyword` | `tests/integration/test_style_match.py` |

---

## 4. 关键质量门

- ✅ Pydantic v2 严格模式，所有 Schema 配置 `model_config = ConfigDict(strict=True, ...)`
- ✅ SQLAlchemy 2.0 async + asyncpg
- ✅ 类型注解 100%（mypy strict）
- ✅ ruff S+ASYNC+UP 规则启用
- ✅ 测试覆盖率门槛 ≥70%（service ≥ 80% / domain ≥ 90% / api ≥ 60%）
- ✅ 全部新表继承 TenantScopedModel + RLS 自动启用
- ✅ 字段级权限硬编码 + TODO U09 注释
- ✅ 审计敏感值脱敏（cost_price / purchase_price 仅记 `*_changed: true`）
- ✅ upsert 数据库原子操作 + partial UNIQUE 对齐
- ✅ match 接口降级语义严格区分系统失败 vs 业务未匹配
- ✅ GIN trgm 索引 + 查询表达式严格一致

---

## 5. 生成节奏（与 U01 一致）

**Plan A（推荐）**：分批生成
- 批次 1：Step 1-3（modules/product 全部业务代码，约 13 个文件）
- 批次 2：Step 4-5（横切修改 + alembic migration，约 5 个文件）
- 批次 3：Step 6-9（测试套件，约 8 个文件）
- 批次 4：Step 10-12（前端骨架 + 文档摘要 + 完成校验，约 5 个文件）

每批完成后 review + getDiagnostics 验证，无误后继续下一批。

**Plan B**：一次性生成全部 ~31 文件（仅适用于完全信任设计文档时）

---

## 6. 文件总数预估

| 类别 | 数量 |
|---|---|
| 业务模块 Python（modules/product/） | 14 |
| 横切修改（修改 U01 已有文件） | 4 modified |
| Alembic migration | 1 |
| 单元测试 | 3 |
| 集成测试 | 5 |
| API 测试 | 1 |
| 性能测试 | 1 |
| Frontend 骨架 | 2 |
| 文档摘要 | 3 |
| **新增合计** | **~30 新文件 + 4 修改** |

---

## 7. 与下一阶段的衔接

U02 完成后可选路径：
- **进入 U03**（博主库基础）：与 U02 并行可行（仅依赖 U01）
- **进入 U04**（推广合作）：依赖 U02 + U03，需 U03 先完成
- **MVP-end Build & Test**：阶段末统一跑完整测试
