# U03 逻辑组件（Logical Components）

> 单元：U03 — 博主库基础  
> 范围：U03 新增组件 + 复用 U01/U02 组件清单  
> 配合 nfr-design-patterns.md 阅读

---

## 1. 组件总览

### 1.1 U03 新增组件清单

| # | 组件 | 类型 | 文件位置 | 复用 |
|---|---|---|---|---|
| 1 | `Blogger` ORM | Domain | `modules/blogger/models.py` | TenantScopedModel (U01) |
| 2 | `BloggerType` / `Platform` / `GenderTarget` Enum | Domain | `modules/blogger/enums.py` | — |
| 3 | `BloggerCreate` / `BloggerUpdate` / `BloggerResponse` Pydantic | Schema | `modules/blogger/schemas.py` | Pydantic v2 |
| 4 | `BloggerListFilters` dataclass | Schema | `modules/blogger/repository.py` | — |
| 5 | `BloggerPage` Pydantic | Schema | `modules/blogger/schemas.py` | — |
| 6 | `BloggerRepository` | Repository | `modules/blogger/repository.py` | TenantScopedModel + Session |
| 7 | `BloggerService` | Service | `modules/blogger/service.py` | @audit / @require_permission |
| 8 | `BloggerDomain` | Domain | `modules/blogger/domain.py` | — |
| 9 | `QUOTE_VISIBLE_ROLES` + `CONTACT_VISIBLE_ROLES` 常量 | Legacy / Transition | `modules/blogger/legacy_field_permissions.py` | 待 U09 清理 |
| 10 | `blogger_permissions` 声明 | Permission | `modules/blogger/permissions.py` | U01 PermissionRegistry |
| 11 | `Blogger*Error` 异常 | Exception | `modules/blogger/exceptions.py` | 继承 U01 base |
| 12 | `blogger_search_results_count` Histogram | Metric | `core/metrics.py`（修改） | prometheus-client |
| 13 | `blogger_router` | API | `modules/blogger/api.py` | FastAPI Router |
| 14 | `get_blogger_service` | Dependency | `modules/blogger/deps.py` | U01 Session 注入 |

### 1.2 复用 U01 / U02 组件（不重复定义）

| 组件 | 来源 | U03 复用方式 |
|---|---|---|
| `TenantScopedModel` 基类 | U01 | Blogger 继承 |
| `AsyncSession` 依赖注入 | U01 | BloggerRepository 注入 |
| `AuditService` + `@audit` 装饰器 | U01 | service 层方法装饰 |
| `Permission` + `@require_permission` | U01 | API 端点装饰 |
| `core/errors.register_error_handlers` | U01 | 全局异常映射 |
| `tenancy.current_tenant_id()` | U01 | service / repository 取租户 |
| `RateLimiter` | U01 | API 默认应用 |
| `RequestIdMiddleware` / `TenancyMiddleware` | U01 | 透明继承 |
| `structlog logger` | U01 | 业务日志 |
| `prometheus-fastapi-instrumentator` | U01 | API 指标自动暴露 |
| `Sentry SDK` | U01 | 异常自动捕获 + tag |
| `FieldPermissionDenied` 异常 | **U02** | 直接复用（已在 modules/product/exceptions.py），通过 `from app.modules.product.exceptions import FieldPermissionDenied` 导入 |
| 4 层架构 | **U02** | 完全沿用 |
| 字段权限硬编码模式 | **U02** P-U02-02 | 适配字段名 |
| 数据库原子 upsert 模式 | **U02** P-U02-03 | 完全镜像 |
| 软删引用检查模式 | **U02** P-U02-04 | 完全镜像 |
| match 降级语义 | **U02** P-U02-01 | 完全镜像 |

### 1.3 关于 FieldPermissionDenied 异常的复用决策

**决策**：直接从 `modules/product/exceptions` 导入，不重复定义。

理由：
- `FieldPermissionDenied(field: str)` 是通用语义（字段级权限拒绝），与 product 域无强绑定
- 重复定义会违反 DRY 原则
- U09 字段级权限落地后，统一移到 `core/exceptions.py`（届时一次性迁移）

实施：
```python
# modules/blogger/exceptions.py
from app.modules.product.exceptions import FieldPermissionDenied  # noqa: F401, re-export
```

---

## 2. 组件依赖图

```mermaid
graph TD
    subgraph "Layer: API"
        BloggerRouter[blogger_router<br/>api.py]
    end
    
    subgraph "Layer: Service"
        BloggerService[BloggerService]
    end
    
    subgraph "Layer: Domain"
        BloggerDomain[BloggerDomain<br/>校验+业务规则]
    end
    
    subgraph "Layer: Repository"
        BloggerRepo[BloggerRepository<br/>含 upsert_atomic + 防侧信道 list]
    end
    
    subgraph "Layer: Models / Schemas"
        Models[Blogger ORM]
        Schemas[Pydantic Schemas]
        Enums[BloggerType / Platform / GenderTarget]
    end
    
    subgraph "Cross-cutting (U01)"
        Audit[AuditService + @audit]
        Perms[@require_permission]
        DB[AsyncSession + RLS]
        Tenancy[tenancy.current_tenant_id]
        Errors[core/exceptions]
        Metrics[core/metrics]
    end
    
    subgraph "Reused from U02"
        FieldPermDenied[FieldPermissionDenied 异常]
        UpsertPattern[upsert pattern]
    end
    
    subgraph "Transition (U03 → U09 清理)"
        LegacyPerms[legacy_field_permissions<br/>QUOTE_VISIBLE_ROLES<br/>CONTACT_VISIBLE_ROLES]
    end
    
    subgraph "U10b 占位"
        Stubs[recompute_blogger_type<br/>recompute_quality_tags<br/>mark_suspected_fake<br/>bulk_recompute_tags<br/>NotImplementedError]
    end
    
    BloggerRouter --> BloggerService
    BloggerService --> BloggerDomain
    BloggerService --> BloggerRepo
    BloggerService --> Audit
    BloggerService --> LegacyPerms
    BloggerService --> Stubs
    BloggerService --> FieldPermDenied
    
    BloggerDomain --> Schemas
    BloggerRepo --> DB
    BloggerRepo --> Tenancy
    BloggerRepo --> UpsertPattern
    Models --> DB
    
    BloggerRouter --> Perms
    BloggerRouter --> Errors
    BloggerRouter --> Metrics
```

---

## 3. 4 层架构（沿用 U01 / U02）

### 3.1 API Layer (`api.py`)
- FastAPI Router
- 装饰器：`@require_permission("blogger:read/write/delete")`
- 输入：Pydantic Schema 校验
- 输出：Response Schema
- 错误处理：抛出业务异常 → 全局 error handler

### 3.2 Service Layer (`service.py`)
- 协调 Domain + Repository + Audit
- 业务编排
- 字段权限过滤（`_to_response` + `_check_sensitive_write_permission`）
- **防侧信道实现**：在调用 repository 前检查 `has_contact_visibility(role_codes)` 决定 `include_wechat_in_keyword` 参数
- 4 个 U10b 钩子方法占位（NotImplementedError）

### 3.3 Domain Layer (`domain.py`)
- 业务规则验证：`validate_quote_non_negative` 等
- 计算 dict diff：`compute_blogger_changes`
- 审计字段安全转换：`build_blogger_audit_changes`（quote/wechat/phone 脱敏）

### 3.4 Repository Layer (`repository.py`)
- DB 操作
- 自动应用 RLS
- `list()` 方法支持 `include_wechat_in_keyword` 参数（防侧信道）
- `upsert_atomic()` 数据库原子操作（与 U02 SkuRepository 同模式）

---

## 4. 关键组件细节

### 4.1 BloggerService（核心 Service）

```python
class BloggerService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._audit = AuditService(session)
        # 注：U04 时增加 promotion_repo: PromotionRepository
    
    # 公开方法
    async def create_blogger(self, payload: BloggerCreate, user: User) -> BloggerResponse: ...
    async def update_blogger(self, id, payload, user) -> BloggerResponse: ...
    async def upsert_by_xiaohongshu_id(self, payload: BloggerCreate, user: User) -> BloggerResponse:
        """U06c 导入路径，不暴露 HTTP"""
        ...
    async def soft_delete_blogger(self, id, user) -> None: ...
    async def disable_blogger(self, id, user) -> BloggerResponse: ...
    async def restore_blogger(self, id, user) -> BloggerResponse: ...
    async def get_blogger(self, id, user) -> BloggerResponse: ...
    async def list_bloggers(self, filters, page, page_size, user) -> BloggerPage:
        """含防侧信道（include_wechat_in_keyword 按角色决定）"""
        ...
    async def check_references(self, blogger_id) -> dict: ...
    
    # U10b 钩子（U03 占位）
    async def recompute_blogger_type(self, blogger_id) -> Blogger:
        raise NotImplementedError("Implemented in U10b")
    async def recompute_quality_tags(self, blogger_id) -> Blogger:
        raise NotImplementedError("Implemented in U10b")
    async def mark_suspected_fake(self, blogger_id, reason) -> Blogger:
        raise NotImplementedError("Implemented in U10b")
    async def bulk_recompute_tags(self) -> int:
        raise NotImplementedError("Implemented in U10b")
    
    # 私有
    async def _check_sensitive_write_permission(self, payload, user) -> None:
        """检查 quote / wechat / phone 写权限"""
        ...
    async def _to_response(self, blogger, user) -> BloggerResponse:
        """按角色过滤敏感字段"""
        ...
```

### 4.2 BloggerRepository（含防侧信道 + upsert）

```python
class BloggerRepository:
    async def get_by_id(self, blogger_id, *, include_deleted: bool = False) -> Blogger | None: ...
    async def get_by_xiaohongshu_id(self, xhs_id) -> Blogger | None: ...
    async def code_exists(self, xiaohongshu_id) -> bool: ...
    
    async def list(
        self,
        *,
        filters: BloggerListFilters,
        page: int,
        page_size: int,
        include_wechat_in_keyword: bool = False,  # ← 防侧信道
    ) -> tuple[Sequence[Blogger], int]: ...
    
    async def upsert_atomic(
        self, *, tenant_id: UUID, values: dict
    ) -> tuple[Blogger, bool]:
        """ON CONFLICT (tenant_id, xiaohongshu_id) WHERE is_deleted=false DO UPDATE
        Returns: (blogger, is_inserted)"""
        ...
    
    def add(self, blogger: Blogger) -> None: ...
```

### 4.3 BloggerListFilters

```python
@dataclass(frozen=True)
class BloggerListFilters:
    keyword: str | None = None
    blogger_type: str | None = None
    follower_count_min: int | None = None
    follower_count_max: int | None = None
    category_tag: str | None = None
    quality_tag: str | None = None
    platform: str | None = None
    is_suspected_fake: bool | None = None
    is_active: bool | None = True
    include_inactive: bool = False
```

### 4.4 自定义 Prometheus 指标

```python
# core/metrics.py（追加，与 U02 已有 sku_upsert_total 共存）
blogger_search_results_count: Histogram = Histogram(
    "blogger_search_results_count",
    "Distribution of blogger search result counts",
    buckets=(0, 1, 5, 20, 100),
)
```

`BloggerService.list_bloggers` 在返回前调用 `blogger_search_results_count.observe(total)`。

---

## 5. 错误处理 / 异常映射

### 5.1 U03 新增异常

| 异常类 | code | HTTP | 抛出场景 |
|---|---|---|---|
| `BloggerXhsIdConflictError` | `BLOGGER_XHS_ID_CONFLICT` | 409 | xiaohongshu_id 重复（含 details.existing_blogger_id） |
| `BloggerNotFoundError` | `BLOGGER_NOT_FOUND` | 404 | blogger 不存在 |
| `BloggerHasReferenceError` | `BLOGGER_HAS_REFERENCE` | 409 | 软删被引用（U03 阶段不会触发） |
| `InvalidQuoteError` | `INVALID_QUOTE` | 422 | quote < 0 或超精度 |
| `InvalidFollowerCountError` | `INVALID_FOLLOWER_COUNT` | 422 | follower_count < 0 |
| `InvalidTagFormatError` | `INVALID_TAG_FORMAT` | 422 | tag 项超长 |

### 5.2 复用 U02 异常
- `FieldPermissionDenied` —— 直接 import from `modules.product.exceptions`
- `ResourceConflictError` / `ValidationError` / `PermissionDeniedError` —— from `core.exceptions`

### 5.3 自动映射
继承 U01 `register_error_handlers(app)`：
- 全局异常 → JSON `{code, message, details}`
- Sentry 自动捕获 5xx
- structlog 自动记录所有错误

---

## 6. 测试组件（与 U02 测试体系一致）

### 6.1 测试 fixtures（追加到 conftest.py）
- `pr_role` — 已在 U02 测试 fixture 中定义，复用
- `pr_manager_role` — 新增
- `blogger_factory` — 测试数据工厂

### 6.2 测试目录结构
```
backend/tests/
├── unit/
│   ├── test_blogger_domain.py        # 业务规则单测
│   └── test_blogger_field_perms.py   # QUOTE_VISIBLE_ROLES / CONTACT_VISIBLE_ROLES 矩阵
├── integration/
│   ├── test_blogger_crud.py          # CRUD 集成
│   ├── test_blogger_search.py        # 搜索 + 防侧信道
│   ├── test_blogger_field_perms.py   # 角色 × 字段矩阵
│   └── test_blogger_upsert.py        # FB7 upsert + 并发
├── api/
│   └── test_blogger_api.py           # 端到端
└── performance/
    └── test_blogger_search_perf.py   # 3000 博主 P95 ≤ 200ms
```

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 4 层架构与 U01/U02 一致 | ✅ |
| 全部新增组件复用 U01/U02 横切组件 | ✅ |
| `legacy_field_permissions` 隔离在模块内不污染 core | ✅ |
| `FieldPermissionDenied` 复用 U02 不重复定义 | ✅ |
| 防侧信道在 service 层 + repository 层双层落地 | ✅ |
| 自定义 Prometheus 指标加入 core/metrics | ✅ |
| 错误码体系与 U01/U02 完全兼容 | ✅ |
| U10b 4 钩子方法占位 + NotImplementedError | ✅ |
| 测试结构与 U02 一致（unit/integration/api/performance） | ✅ |
