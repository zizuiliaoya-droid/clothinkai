## U03 代码生成摘要

> 本文档汇总 U03 单元生成的全部应用代码 + 测试 + 文档清单。  
> 单元：U03 — 博主库基础（MVP 第 3 个单元）  
> 依赖：U01 全部基础设施 + U02（FieldPermissionDenied 异常复用）已就绪

---

## 1. 文件清单

### Backend 业务代码（modules/blogger/，12）
- `__init__.py`、`enums.py`（3 Python Enum）
- `permissions.py`（blogger:* 权限字符串）
- `legacy_field_permissions.py`（QUOTE_VISIBLE_ROLES / CONTACT_VISIBLE_ROLES，TODO U09 清理）
- `exceptions.py`（5 业务异常 + re-export FieldPermissionDenied）
- `models.py`（Blogger ORM）
- `schemas.py`（5 Pydantic v2 strict）
- `domain.py`（dict diff + audit_safe_changes 脱敏）
- `repository.py`（含 search 防侧信道 + upsert_atomic）
- `service.py`（含 U10b 4 钩子占位 NotImplementedError）
- `deps.py`、`api.py`（7 端点）

### 横切修改（U01/U02 文件追加）
- `backend/app/core/metrics.py`：追加 `blogger_search_results_count` Histogram
- `backend/app/main.py`：import + 注册 `blogger_router`

### Alembic 迁移
- `backend/alembic/versions/005_u03_create_blogger_table.py`（pg_trgm 幂等 + 1 表 + 10 索引 + 1 RLS）

### 测试套件（7 + 1 修改）
- `tests/conftest.py`（修改）：追加 pr_manager_role + blogger_factory
- `tests/unit/test_blogger_field_perms.py`（13 用例：QUOTE_VISIBLE / CONTACT_VISIBLE / WRITABLE 矩阵）
- `tests/unit/test_blogger_domain.py`（10 用例：audit 脱敏 + dict diff）
- `tests/integration/test_blogger_crud.py`（8 用例：EP04-S01/S02 + 字段权限）
- `tests/integration/test_blogger_search.py`（13 用例：搜索 + **防侧信道关键测试 3 角色**）
- `tests/integration/test_blogger_upsert.py`（4 用例：FB7 INSERT/UPDATE + 复用校验）
- `tests/api/test_blogger_api.py`（4 用例）
- `tests/performance/test_blogger_search_perf.py`（1 用例：3000 博主 P95 ≤ 200ms）

### Frontend 骨架
- `frontend/src/features/blogger/api.ts`（7 API 方法）
- `frontend/src/features/blogger/types.ts`（5 接口 + 3 枚举类型）

### 文档摘要
- `aidlc-docs/construction/U03/code/README.md`（本文件）
- `aidlc-docs/construction/U03/code/api-endpoints.md`
- `aidlc-docs/construction/U03/code/test-coverage.md`

---

## 2. 文件总数

| 类别 | 数量 |
|---|---|
| Python 业务代码（modules/blogger/） | 12 |
| Python 横切修改 | 2 modified |
| Alembic migration | 1 |
| Python 测试 | 7（2 unit + 3 integration + 1 api + 1 performance） |
| 测试 fixture 修改 | 1 modified |
| TypeScript 前端 | 2 |
| 文档摘要 | 3 |
| **合计** | **~25 新文件 + 3 修改** |

---

## 3. 故事覆盖追溯

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP04-S01 创建博主 | `service.create_blogger` + `api.create_blogger` | `test_blogger_crud.py:TestCreateBlogger` |
| EP04-S02 编辑博主（含 quote audit 脱敏 + 字段权限） | `service.update_blogger` + `domain.build_blogger_audit_changes` | `test_blogger_crud.py:TestUpdateBlogger` |
| EP04-S03 搜索筛选 + 字段权限 | `service.list_bloggers` + `repository.list` | `test_blogger_search.py` |

---

## 4. NFR 模式落地位置

| Pattern | 实施位置 |
|---|---|
| **P-U03-01** 单字段 GIN trgm + 防侧信道 | `repository.list(include_wechat_in_keyword=...)` + `service.list_bloggers` 根据 `has_contact_visibility` 决定 |
| **P-U03-02** GIN JSONB tag 包含查询 | `repository.list` 用 `Blogger.category_tags.contains(cast([tag], JSONB))` |
| 复用 P-U02-02 字段权限硬编码 | `legacy_field_permissions.py` + `service._check_sensitive_write_permission` + `service._to_response` |
| 复用 P-U02-03 数据库原子 upsert | `repository.upsert_atomic`（与 SkuRepository.upsert_atomic 完全镜像） |
| 复用 P-U02-04 软删 + 引用检查 | `service.check_references` + TODO U04 注释 |
| 复用 match 降级语义 | `service.list_bloggers` 不 try/except DB 异常 |

---

## 5. U10b 扩展点（U03 占位）

`service.BloggerService` 提供 4 个钩子方法（`raise NotImplementedError`）：
- `recompute_blogger_type(blogger_id)` — 按 follower_count 自动计算
- `recompute_quality_tags(blogger_id)` — 自动追加质量标签
- `mark_suspected_fake(blogger_id, reason)` — 假号自动判定
- `bulk_recompute_tags()` — 定时批量任务（U10b Celery beat）

U10b 阶段实施时仅替换方法体，service 接口不变。

---

## 6. 关键质量门

- ✅ 全部 Python 文件诊断器无警告
- ✅ Pydantic v2 严格模式
- ✅ SQLAlchemy 2.0 async + asyncpg
- ✅ mypy strict / ruff S+ASYNC+UP
- ✅ Blogger 继承 TenantScopedModel + RLS 自动启用
- ✅ pg_trgm 单字段 GIN 索引（与 U02 拼接表达式不同，更轻量）
- ✅ GIN JSONB 索引（category_tags / quality_tags）
- ✅ 字段权限隔离（`modules/blogger/legacy_field_permissions.py`）
- ✅ FieldPermissionDenied 复用 U02 不重复定义
- ✅ 审计敏感值脱敏（quote / wechat / phone 仅记 `*_changed: true`）
- ✅ upsert 原子操作 + partial UNIQUE 对齐 + 不"恢复"软删
- ✅ search 降级语义 + **防侧信道双层落地**（service + repository）
- ✅ U10b 4 钩子 NotImplementedError 占位

---

## 7. 后续单元的扩展点

| 单元 | 引用本单元方式 |
|---|---|
| **U04 推广合作** | 通过 `service.check_references` 接口接入 promotion 引用查询；快照 `Blogger.quote` 到 promotion 表 |
| **U06c 手动导入** | 调用 `BloggerService.upsert_by_xiaohongshu_id()` 服务方法（不暴露 HTTP），通过 ON CONFLICT 幂等 |
| **U09 字段级权限** | grep `legacy_field_permissions` 替换为 `Permission.field_filter()` 后删除文件；U02 + U03 一次性清理 |
| **U10b 智能标签** | 实施 4 个钩子方法体（recompute_blogger_type / recompute_quality_tags / mark_suspected_fake / bulk_recompute_tags） |

---

## 8. 资源使用预估（与 NFR §9 一致）

- 单租户 3000 博主：< 5MB 行存储 + < 5MB 索引（含 GIN trgm + GIN JSONB）
- 单 backend 实例增量：~ 20MB 内存（ORM + Pydantic）
- 完全在 Zeabur 现有 6 服务承载范围内

---

## 9. 部署步骤摘要

详见 `aidlc-docs/construction/U03/infrastructure-design/deployment-architecture.md`：

1. PR 合并 main → CI 跑通过
2. 手动触发 `migrate.yml`（env=staging）→ alembic 升 005_u03
3. 验证 staging schema（`\dt blogger`、`\d blogger`、EXPLAIN ANALYZE GIN 命中）
4. deploy-staging.yml 自动触发 → 部署应用
5. 业务冒烟测试通过 → 同步 production
