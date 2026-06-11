# U02 逻辑组件（Logical Components）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 新增组件 + 复用 U01 组件清单  
> 配合 nfr-design-patterns.md 阅读

---

## 1. 组件总览

### 1.1 U02 新增组件清单

| # | 组件 | 类型 | 文件位置 | 复用 U01 |
|---|---|---|---|---|
| 1 | `Style` ORM | Domain | `modules/product/models.py` | TenantScopedModel |
| 2 | `Sku` ORM | Domain | 同上 | TenantScopedModel |
| 3 | `Brand` ORM | Domain | 同上 | TenantScopedModel |
| 4 | `StyleDetailImage` ORM | Domain | 同上 | TenantScopedModel + Attachment 关联 |
| 5 | `Category` Enum / `Season` Enum / `Gender` Enum / `DesignStatus` Enum / `SourcingType` Enum | Domain | `modules/product/enums.py` | — |
| 6 | `StyleCreate` / `StyleUpdate` / `StyleResponse` Pydantic | Schema | `modules/product/schemas.py` | Pydantic v2 配置（U01） |
| 7 | `SkuCreate` / `SkuUpdate` / `SkuResponse` Pydantic | Schema | 同上 | 同上 |
| 8 | `BrandCreate` / `BrandUpdate` / `BrandResponse` Pydantic | Schema | `modules/product/brand_schemas.py` | 同上 |
| 9 | `MatchResponse` / `MatchCandidate` Pydantic | Schema | `modules/product/schemas.py` | 同上 |
| 10 | `StyleRepository` | Repository | `modules/product/repository.py` | TenantScopedModel + Session（U01） |
| 11 | `SkuRepository` | Repository | 同上 | 同上 |
| 12 | `BrandRepository` | Repository | `modules/product/brand_repository.py` | 同上 |
| 13 | `StyleService` | Service | `modules/product/service.py` | @audit / @require_permission / AttachmentService |
| 14 | `SkuService` | Service | 同上 | 同上 |
| 15 | `BrandService` | Service | `modules/product/brand_service.py` | 同上 |
| 16 | `StyleDomain` (验证 + 业务规则) | Domain | `modules/product/domain.py` | — |
| 17 | `SkuDomain` (验证 + 业务规则) | Domain | 同上 | — |
| 18 | `PRICE_VISIBLE_ROLES` 常量 | Legacy / Transition | `modules/product/legacy_field_permissions.py` | 待 U09 清理 |
| 19 | `product_permissions` 声明 | Permission | `modules/product/permissions.py` | U01 PermissionRegistry |
| 20 | `FieldPermissionDenied` 异常 | Exception | `core/exceptions.py`（修改） | 继承 U01 PermissionDeniedError |
| 21 | `style_search_results_count` Histogram | Metric | `core/metrics.py`（修改） | prometheus-fastapi-instrumentator |
| 22 | `sku_upsert_total` Counter | Metric | 同上 | 同上 |
| 23 | `product_router` | API | `modules/product/api.py` | FastAPI Router + U01 中间件 |
| 24 | `get_style_service` / `get_sku_service` / `get_brand_service` | Dependency | `modules/product/deps.py` | U01 Session 注入 |
| 25 | `product_exceptions` | Exception | `modules/product/exceptions.py` | 继承 U01 base |

### 1.2 复用 U01 组件（不重复定义）

| U01 组件 | U02 复用方式 |
|---|---|
| `TenantScopedModel` 基类 | Style / Sku / Brand / StyleDetailImage 继承 |
| `AsyncSession` 依赖注入 | StyleRepository / SkuRepository 注入 |
| `AsyncSessionBypass` (RLS bypass) | 不在 U02 直接使用（仅 system 任务用） |
| `AuditService` + `@audit` 装饰器 | service 层方法装饰 |
| `Permission` + `@require_permission` 装饰器 | API 端点装饰 |
| `EffectivePermissions.has` 通配符匹配 | API 端点权限校验 |
| `AttachmentService` | Style.main_image_id + StyleDetailImage 关联 |
| `core/errors.register_error_handlers` | 自动映射所有异常到 JSON 响应 |
| `tenancy.current_tenant_id()` | service / repository 取当前租户 |
| `system_context()` | 不在 U02 直接使用 |
| `RateLimiter` (slowapi) | API 默认应用通用限流 |
| `RequestIdMiddleware` | request_id 自动注入日志 |
| `TenancyMiddleware` | tenant_id 自动注入 contextvar |
| `structlog logger` | service / repository 日志输出 |
| `prometheus-fastapi-instrumentator` | API 性能指标自动暴露 |
| `Sentry SDK` | 异常自动捕获 + tag 标记 |

---

## 2. 组件依赖图

```mermaid
graph TD
    subgraph "Layer: API"
        ProductRouter[product_router<br/>api.py]
    end
    
    subgraph "Layer: Service"
        StyleService[StyleService]
        SkuService[SkuService]
        BrandService[BrandService]
    end
    
    subgraph "Layer: Domain"
        StyleDomain[StyleDomain<br/>校验+业务规则]
        SkuDomain[SkuDomain<br/>校验+业务规则]
    end
    
    subgraph "Layer: Repository"
        StyleRepo[StyleRepository]
        SkuRepo[SkuRepository]
        BrandRepo[BrandRepository]
    end
    
    subgraph "Layer: Models / Schemas"
        Models[Style/Sku/Brand/StyleDetailImage<br/>ORM]
        Schemas[Pydantic Schemas]
        Enums[Category/Season/Gender/<br/>DesignStatus/SourcingType]
    end
    
    subgraph "Cross-cutting (U01)"
        Audit[AuditService + @audit]
        Perms[@require_permission +<br/>EffectivePermissions]
        Attach[AttachmentService]
        DB[AsyncSession + RLS]
        Tenancy[tenancy.current_tenant_id]
        Errors[core/exceptions]
        Metrics[core/metrics<br/>prometheus]
    end
    
    subgraph "Transition (U02 → U09 清理)"
        LegacyPerms[legacy_field_permissions<br/>PRICE_VISIBLE_ROLES]
    end
    
    ProductRouter --> StyleService
    ProductRouter --> SkuService
    ProductRouter --> BrandService
    
    StyleService --> StyleDomain
    StyleService --> StyleRepo
    StyleService --> Audit
    StyleService --> Attach
    
    SkuService --> SkuDomain
    SkuService --> SkuRepo
    SkuService --> Audit
    SkuService --> LegacyPerms
    
    BrandService --> BrandRepo
    BrandService --> Audit
    
    StyleDomain --> Schemas
    SkuDomain --> Schemas
    
    StyleRepo --> DB
    StyleRepo --> Tenancy
    SkuRepo --> DB
    SkuRepo --> Tenancy
    BrandRepo --> DB
    BrandRepo --> Tenancy
    
    Models --> DB
    
    ProductRouter --> Perms
    ProductRouter --> Errors
    ProductRouter --> Metrics
```

---

## 3. 4 层架构（沿用 U01）

### 3.1 API Layer (`api.py`)
- FastAPI Router 注册：`@router.post`, `@router.get`, `@router.put`, `@router.delete`
- 装饰器：`@require_permission("product:write")`, `@audit_endpoint("style.create")`（可选）
- 输入：Pydantic Schema 校验
- 输出：Response Schema
- 错误处理：抛出业务异常 → 全局 error handler 映射 JSON
- 不写业务逻辑，仅做编排

### 3.2 Service Layer (`service.py`, `brand_service.py`)
- 协调 Domain + Repository + Audit + Attachment
- 业务编排：取数据 → Domain 校验 → Repository 持久化 → Audit 记录 → Response 转换
- 字段权限过滤（U02 占位）：`to_response()` 内根据 user.role 过滤

### 3.3 Domain Layer (`domain.py`)
- 业务规则验证：`validate_sourcing_price_consistency()`, `validate_style_code_format()`
- 计算 dict diff：`compute_changes(old, new)`
- 审计字段安全转换：`build_audit_changes(changes)`
- 不依赖 DB / Session

### 3.4 Repository Layer (`repository.py`, `brand_repository.py`)
- DB 操作：CRUD + 复杂查询
- 不写业务规则
- 自动应用 RLS（依赖 Session 注入 tenant_id）
- 暴露异步方法：`async def get_by_id() / list() / create() / update() / soft_delete()`

---

## 4. 关键组件细节

### 4.1 StyleService（核心 Service）

```python
class StyleService:
    def __init__(
        self,
        session: AsyncSession,
        repo: StyleRepository,
        sku_repo: SkuRepository,
        attachment_service: AttachmentService,
        audit: AuditService,
    ):
        self.session = session
        self.repo = repo
        self.sku_repo = sku_repo
        self.attachment_service = attachment_service
        self.audit = audit
    
    async def create_style(self, payload: StyleCreate, user: CurrentUser) -> StyleResponse: ...
    async def update_style(self, id: UUID, payload: StyleUpdate, user: CurrentUser) -> StyleResponse: ...
    async def soft_delete_style(self, id: UUID, user: CurrentUser) -> None: ...
    async def disable_style(self, id: UUID, user: CurrentUser) -> StyleResponse: ...
    async def restore_style(self, id: UUID, user: CurrentUser) -> StyleResponse: ...
    async def disable_with_skus(self, id: UUID, user: CurrentUser) -> StyleResponse: ...
    async def list_styles(self, filters: StyleFilters, page: int, page_size: int) -> Page[StyleResponse]: ...
    async def get_style(self, id: UUID, user: CurrentUser) -> StyleResponse: ...
    async def match_by_code(self, style_code: str) -> MatchResponse: ...
    async def match_by_keyword(self, keyword: str) -> MatchResponse: ...
```

### 4.2 SkuService（核心 Service）

```python
class SkuService:
    def __init__(
        self,
        session: AsyncSession,
        repo: SkuRepository,
        style_repo: StyleRepository,
        audit: AuditService,
    ):
        # 注：U04 时增加 promotion_repo: PromotionRepository
        # 注：U16 时增加 order_repo: OrderRepository
        ...
    
    async def create_sku(self, payload: SkuCreate, user: CurrentUser) -> SkuResponse: ...
    async def update_sku(self, id: UUID, payload: SkuUpdate, user: CurrentUser) -> SkuResponse: ...
    async def upsert_sku(self, payload: SkuCreate, user: CurrentUser) -> SkuResponse:
        """U06b 导入路径，不暴露 HTTP，与 partial UNIQUE 对齐"""
        ...
    async def soft_delete_sku(self, id: UUID, user: CurrentUser) -> None: ...
    async def list_by_style(self, style_id: UUID, user: CurrentUser) -> list[SkuResponse]: ...
    async def get_sku(self, id: UUID, user: CurrentUser) -> SkuResponse: ...
    
    # 引用检查（U04/U16 扩展）
    async def check_references(self, sku_id: UUID) -> dict: ...
    
    # 私有
    async def _validate_sourcing_price(self, payload, base=None) -> None: ...
    async def _validate_business_rules(self, payload, base=None) -> None: ...
    def _check_price_write_permission(self, payload, user) -> None: ...
    async def _apply_sku_changes(self, sku, payload, user, *, audit_action, is_new) -> None: ...
    async def _log_audit_for_upsert(self, sku, payload, user, audit_action) -> None: ...
    def to_response(self, sku, user) -> SkuResponse: ...
```

### 4.3 BrandService（简化 CRUD）

```python
class BrandService:
    async def create_brand(...) -> BrandResponse: ...
    async def update_brand(...) -> BrandResponse: ...
    async def soft_delete_brand(...) -> None: ...
    async def list_brands(...) -> list[BrandResponse]: ...
    async def get_brand(...) -> BrandResponse: ...
```

### 4.4 StyleRepository（数据访问）

```python
class StyleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
    
    async def get_by_id(self, id: UUID) -> Style | None: ...
    async def get_by_code(self, style_code: str) -> Style | None: ...
    async def list(self, filters: dict, page: int, page_size: int) -> tuple[list[Style], int]: ...
    async def search_by_keyword(self, keyword: str, limit: int = 20) -> list[StyleSearchResult]:
        """模糊搜索（GIN trgm + similarity 排序）"""
        sql = sa.text("""
            SELECT id, style_code, style_name, short_name,
                   similarity(
                     style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''),
                     :keyword
                   ) AS sim
            FROM style
            WHERE tenant_id = :tenant_id
              AND is_deleted = false
              AND is_active = true
              AND (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
                   ILIKE '%' || :keyword || '%'
            ORDER BY
              CASE
                WHEN short_name ILIKE :exact THEN 1
                WHEN style_name ILIKE :exact THEN 2
                ELSE 3
              END,
              sim DESC,
              created_at DESC
            LIMIT :limit
        """)
        ...
    async def create(self, style: Style) -> Style: ...
    async def update(self, style: Style) -> Style: ...
```

### 4.5 SkuRepository

```python
class SkuRepository:
    async def get_by_id(self, id: UUID) -> Sku | None: ...
    async def get_by_code(self, sku_code: str) -> Sku | None: ...
    async def list_by_style(self, style_id: UUID, is_active: bool = True) -> list[Sku]: ...
    async def count_by_style(self, style_id: UUID, is_active: bool, is_deleted: bool) -> int: ...
    async def upsert_atomic(self, payload: dict) -> tuple[Sku, bool]:
        """ON CONFLICT DO UPDATE
        返回 (sku, is_inserted)"""
        ...
```

### 4.6 FieldPermissionDenied 异常

```python
# core/exceptions.py（修改）
class PermissionDeniedError(BaseAppException):
    code = "PERMISSION_DENIED"
    http_status = 403

class FieldPermissionDenied(PermissionDeniedError):
    """字段级权限拒绝"""
    code = "FIELD_PERMISSION_DENIED"
    
    def __init__(self, field: str):
        super().__init__(f"无权写入字段: {field}", details={"field": field})
        self.field = field
```

### 4.7 自定义 Prometheus 指标

```python
# core/metrics.py（修改）
from prometheus_client import Counter, Histogram

style_search_results_count = Histogram(
    "style_search_results_count",
    "Distribution of search result counts (per /api/styles/match call)",
    buckets=[0, 1, 5, 10, 20],
)

sku_upsert_total = Counter(
    "sku_upsert_total",
    "Total upsert calls (categorized by result)",
    labelnames=["result"],  # created / updated
)
```

---

## 5. 错误处理 / 异常映射

### 5.1 U02 新增异常

| 异常类 | code | HTTP | 抛出场景 |
|---|---|---|---|
| `ResourceConflictError(STYLE_CODE_CONFLICT)` | `STYLE_CODE_CONFLICT` | 409 | style_code 已存在 |
| `ResourceConflictError(SKU_CODE_CONFLICT)` | `SKU_CODE_CONFLICT` | 409 | sku_code 已存在 |
| `ResourceConflictError(BRAND_CODE_CONFLICT)` | `BRAND_CODE_CONFLICT` | 409 | brand_code 已存在 |
| `ResourceConflictError(STYLE_HAS_ACTIVE_SKU)` | `STYLE_HAS_ACTIVE_SKU` | 409 | 删 style 时仍有 active sku |
| `ResourceConflictError(SKU_HAS_REFERENCE)` | `SKU_HAS_REFERENCE` | 409 | sku 被 promotion / order 引用 |
| `ValidationError(INVALID_STYLE_REFERENCE)` | `INVALID_STYLE_REFERENCE` | 422 | sku.style_id 不存在 |
| `ValidationError(SOURCING_PRICE_MISMATCH)` | `SOURCING_PRICE_MISMATCH` | 422 | sourcing_type 与价格字段不一致 |
| `FieldPermissionDenied("cost_price")` | `FIELD_PERMISSION_DENIED` | 403 | 无权写敏感字段 |
| `ResourceNotFound("style")` | `NOT_FOUND` | 404 | style 不存在 / 跨租户访问 |

### 5.2 自动映射
继承 U01 `register_error_handlers(app)`：
- 全局异常 → JSON `{code, message, details}`
- Sentry 自动捕获 5xx
- structlog 自动记录所有错误

---

## 6. 测试组件（与 U01 测试体系一致）

### 6.1 测试 fixtures（复用 U01）
- `tenant_a` / `tenant_b` — 双租户测试
- `factory.style()` / `factory.sku()` / `factory.brand()` — 测试数据工厂（U02 新增）
- `admin_role` / `follower_role` / `pr_role` / `designer_role` — 角色 fixture
- `client_as(role)` — 模拟特定角色调用

### 6.2 测试目录结构
```
backend/tests/
├── unit/
│   ├── test_style_domain.py        # 业务规则单测
│   ├── test_sku_domain.py
│   └── test_field_permissions.py   # PRICE_VISIBLE_ROLES 矩阵测
├── integration/
│   ├── test_style_crud.py          # CRUD 集成
│   ├── test_sku_crud.py
│   ├── test_brand_crud.py
│   ├── test_style_match.py         # match 接口
│   ├── test_sku_upsert.py          # FB7 + 并发
│   └── test_field_permission_matrix.py  # 角色 × 字段矩阵
├── api/
│   ├── test_product_api.py         # 端到端
│   └── test_brand_api.py
└── performance/
    └── test_match_perf.py          # @pytest.mark.performance
```

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 4 层架构与 U01 一致 | ✅ |
| 全部新增组件复用 U01 横切组件 | ✅ |
| Brand 合并到 modules/product/，避免目录碎片 | ✅ |
| legacy_field_permissions 隔离在模块内不污染 core | ✅ |
| FieldPermissionDenied 继承 PermissionDeniedError | ✅ |
| 自定义 Prometheus 指标加入 core/metrics | ✅ |
| 错误码体系与 U01 完全兼容 | ✅ |
| 测试结构与 U01 一致（unit/integration/api/performance） | ✅ |
