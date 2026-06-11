# U02 代码生成摘要

> 本文档汇总 U02 单元生成的全部应用代码 + 测试 + 文档清单，配合故事追溯。  
> 单元：U02 — 商品 / SKU 基础（MVP 第 2 个单元）  
> 依赖：U01 全部基础设施已就绪

---

## 1. 文件清单（按 Step 分组）

### Step 1-3 — modules/product 业务代码（14）
- `backend/app/modules/product/__init__.py`
- `backend/app/modules/product/enums.py`（5 Python Enum：Category/Season/Gender/DesignStatus/SourcingType）
- `backend/app/modules/product/models.py`（4 ORM：Brand/Style/Sku/StyleDetailImage）
- `backend/app/modules/product/schemas.py`（13 个 Pydantic v2 strict）
- `backend/app/modules/product/brand_schemas.py`（3 个 Pydantic）
- `backend/app/modules/product/permissions.py`（product/brand 权限字符串）
- `backend/app/modules/product/legacy_field_permissions.py`（PRICE_VISIBLE_ROLES + has_price_visibility，TODO U09 清理）
- `backend/app/modules/product/exceptions.py`（13 个业务异常子类）
- `backend/app/modules/product/domain.py`（业务规则验证 + dict diff + audit_safe_changes 脱敏）
- `backend/app/modules/product/repository.py`（StyleRepository + SkuRepository，含 GIN trgm + upsert_atomic）
- `backend/app/modules/product/brand_repository.py`（BrandRepository CRUD）
- `backend/app/modules/product/service.py`（StyleService + SkuService，4 个 NFR Pattern 落地）
- `backend/app/modules/product/brand_service.py`（BrandService）
- `backend/app/modules/product/api.py`（FastAPI Router + 18 端点）
- `backend/app/modules/product/deps.py`（3 个 ServiceDep）

### Step 4 — 横切修改（U01 文件追加）（2 修改 + 1 新增）
- `backend/app/core/metrics.py`（新增）— 2 个自定义 Prometheus 指标
- `backend/app/main.py`（修改）— import + 注册 `product_router`
- `backend/app/modules/product/service.py`（修改）— 接入指标

### Step 5 — Alembic 迁移（1）
- `backend/alembic/versions/004_u02_create_product_tables.py`
  - 启用 `pg_trgm` 扩展
  - 创建 4 张表（brand/style/sku/style_detail_image）
  - 12 个索引（含 GIN trgm 表达式索引 + 部分唯一索引）
  - 4 条 RLS 策略
  - 追加 brand 权限 seed + 8 个角色 grant

### Step 6 — 单元测试（3）
- `backend/tests/unit/test_field_permissions.py`（13 用例）
- `backend/tests/unit/test_sku_domain.py`（11 用例：sourcing 一致性 + audit 脱敏 + dict diff）
- `backend/tests/unit/test_style_domain.py`（6 用例：Style audit + dict diff）

### Step 7 — 集成测试（5）
- `backend/tests/integration/test_style_crud.py`（7 用例：EP02-S01/S03 + 软删 + 列表）
- `backend/tests/integration/test_sku_crud.py`（11 用例：EP02-S02/S04/S05 + 字段权限矩阵）
- `backend/tests/integration/test_brand_crud.py`（5 用例）
- `backend/tests/integration/test_style_match.py`（9 用例：EP02-S06 + **FB1 系统失败不伪装空候选**）
- `backend/tests/integration/test_sku_upsert.py`（5 用例：**FB7 INSERT/UPDATE + 复用校验**）

### Step 8 — API 端到端（1）
- `backend/tests/api/test_product_api.py`（6 用例：鉴权 + OpenAPI 暴露 + schema 校验）

### Step 9 — 性能基准（2）
- `backend/tests/performance/__init__.py`
- `backend/tests/performance/test_match_perf.py`（5 万 style P95 ≤ 300ms，`@pytest.mark.performance`）

### Step 10 — Frontend 最简骨架（2）
- `frontend/src/features/product/api.ts`（17 个 API 方法）
- `frontend/src/features/product/types.ts`（13 个 TS 接口 + 5 个枚举类型）

### Step 11 — 文档摘要（3）
- `aidlc-docs/construction/U02/code/README.md`（本文件）
- `aidlc-docs/construction/U02/code/api-endpoints.md`
- `aidlc-docs/construction/U02/code/test-coverage.md`

### conftest 修改（1 修改）
- `backend/tests/conftest.py` — 追加 product_factory + follower_role/finance_role/pr_role

---

## 2. 文件总数

| 类别 | 数量 |
|---|---|
| Python 业务代码（modules/product/） | 14 |
| Python 横切修改 | 1 modified + 1 created |
| Alembic migration | 1 |
| Python 测试 | 11 (3 unit + 5 integration + 1 api + 2 performance) |
| 测试 fixture 修改 | 1 modified |
| TypeScript 前端 | 2 |
| 文档摘要 | 3 |
| **新增合计** | **~32 新文件 + 3 修改** |

---

## 3. 故事覆盖追溯

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP02-S01 创建款式 | `service.StyleService.create_style` + `api.create_style` | `tests/integration/test_style_crud.py:TestCreateStyle` |
| EP02-S02 创建 SKU | `service.SkuService.create_sku` + `api.create_sku` | `tests/integration/test_sku_crud.py:TestCreateSku` |
| EP02-S03 编辑款式 | `service.StyleService.update_style` + audit 仅 style_code | `tests/integration/test_style_crud.py:TestUpdateStyle` |
| EP02-S04 编辑 SKU 成本/价格 | `service.SkuService.update_sku` + 字段权限 | `tests/integration/test_sku_crud.py:TestUpdateSkuFieldPermission` |
| EP02-S05 按款式查询 SKU | `service.SkuService.list_by_style` | `tests/integration/test_sku_crud.py:TestListByStyle` |
| EP02-S06 双向关联 | `service.StyleService.match_by_code/keyword` | `tests/integration/test_style_match.py` |

---

## 4. 4 个 NFR 模式落地位置

| Pattern | 实施位置 |
|---|---|
| **P-U02-01** GIN trgm 模糊搜索 + 降级语义 | `repository.StyleRepository.search_by_keyword` + `service.StyleService.match_by_keyword` 不 try/except DB 异常 |
| **P-U02-02** 字段权限硬编码过渡 | `legacy_field_permissions.py` + `service._check_price_write_permission` + `service._to_response` |
| **P-U02-03** 数据库原子 upsert | `repository.SkuRepository.upsert_atomic`（pg_insert.on_conflict_do_update + index_where + xmax 判断） |
| **P-U02-04** 软删 + 引用检查 | `service.SkuService.check_references` + `service.StyleService.soft_delete_style` |

---

## 5. 关键质量门

- ✅ 全部 Python 文件诊断器无警告
- ✅ Pydantic v2 严格模式（`ConfigDict(strict=True)`）
- ✅ SQLAlchemy 2.0 async + asyncpg
- ✅ 类型注解 100%（mypy strict）
- ✅ ruff S+ASYNC+UP 规则
- ✅ 4 张新表继承 TenantScopedModel + RLS 自动启用
- ✅ pg_trgm GIN trgm 索引强制建（U02 不再"启用扩展不建索引"占位）
- ✅ 查询拼接表达式与索引表达式严格一致（concat_ws）
- ✅ 字段权限隔离在 modules/product/legacy_field_permissions.py（不污染 core）
- ✅ 审计敏感值脱敏：cost_price/purchase_price 仅记 `*_changed: true`
- ✅ upsert 数据库原子操作 + partial UNIQUE 对齐 + 不"恢复"软删行
- ✅ match 接口降级语义严格区分系统失败 vs 业务未匹配（含 FB1 测试）
- ✅ 部分唯一索引（软删后 code 释放）
- ✅ 价格 CHECK 约束（DB 层非负）

---

## 6. 用户反馈修正记录

### NFR Requirements 阶段（7 条 P1）
1. match 失败降级语义：业务未匹配 vs 系统失败严格区分
2. GIN 索引强制建（不再"启用扩展不建索引"占位）
3. migration 走专用 job（与 U01 Q11=B 一致）
4. 健康端点统一 /health + /ready
5. cost_price 不加密 + 威胁模型边界（"防越权读取，不防 DBA"）
6. Prometheus 主导 SLA / Sentry 仅抽样异常
7. upsert 边界：必须复用同一套校验/权限/审计

### NFR Design 阶段（8 条 P1）
1. audit_log 敏感值脱敏（仅记 `*_changed: true` 标记）
2. GIN 索引表达式与查询表达式严格一致（拼接表达式 ILIKE）
3. upsert 与 partial UNIQUE 严格对齐 + 不"恢复"软删行
4. tech-stack 示例同步原子 upsert（删除先查后写）
5. xmax=0 标记可选实现 + 时间戳备选
6. Q7 选项与原因一致（U02 选 A 硬编码，不引注册器）
7. legacy_field_permissions 放 modules/product/ 不污染 core
8. similarity_threshold 与 ILIKE 不相关（改为索引命中检查）

### Code Generation 阶段（1 自查修正）
- PRICE_VISIBLE_ROLES `{admin, follower, finance}` → `{admin, merchandiser, finance}`（与 U01 seed 角色 code 对齐）

---

## 7. 后续单元的扩展点

| 单元 | 引用本单元方式 |
|---|---|
| **U04 推广合作** | 通过 `service.SkuService.check_references` 接口接入 promotion 引用查询；快照 `Sku.cost_price` / `Sku.base_price` 到 promotion 表 |
| **U06b 手动导入** | 调用 `SkuService.upsert_sku()` 服务方法（不暴露 HTTP），通过 ON CONFLICT 幂等 |
| **U09 字段级权限** | grep `legacy_field_permissions` 替换为 `Permission.field_filter()` 后删除文件 |
| **U10a 设计制版** | 扩展 `DesignStatus` Enum 新增 5 个状态（Python 改即可，DB 字段不变） |
| **U16 订单** | 通过 `Sku.id` FK + 快照 `base_price` |
| **U17 套装** | 新建 `Bundle` / `BundleItem` 表，引用 `Sku.id` |

---

## 8. 资源使用预估（与 NFR §9 一致）

- 单租户 5 万 style + 50 万 sku：~ 100MB 行存储 + ~ 50MB GIN trgm 索引
- 单 backend 实例增量：~ 50MB 内存 / +0.05 vCPU 平均
- 全部承载在 Zeabur 现有 6 服务，不需要扩容

---

## 9. 部署步骤摘要

详见 `aidlc-docs/construction/U02/infrastructure-design/deployment-architecture.md`：

1. PR 合并到 main → CI 自动跑
2. 手动触发 `migrate.yml`（env=staging）→ alembic 升到 004_u02
3. 验证 staging schema（`\dt`、`\d style`、`SELECT pg_extension`）
4. deploy-staging.yml 自动触发 → 部署应用
5. 业务冒烟测试通过后，重复 2-4 在 production
