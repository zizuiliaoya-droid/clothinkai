# U03 代码生成计划（Code Generation Plan）

> 单元：U03 — 博主库基础  
> 阶段：MVP 第 3 个单元  
> 节奏：**一次性生成**（U03 与 U02 模式高度同构，无新模式风险）

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 | 说明 |
|---|---|---|
| EP04-S01 | MVP | PR 添加博主（xiaohongshu_id 唯一） |
| EP04-S02 | MVP | PR 编辑博主（报价为敏感字段必记 audit） |
| EP04-S03 | MVP | 博主搜索筛选（昵称/类型/粉丝量/质量标签） |

### 1.2 依赖
- **依赖**：U01（基础设施）+ U02（FieldPermissionDenied 异常复用）
- **被依赖**：U04 (推广) / U06c (导入) / U10b (智能标签)

### 1.3 项目结构
```
backend/app/modules/blogger/
├── __init__.py
├── enums.py                         # BloggerType / Platform / GenderTarget
├── models.py                        # Blogger ORM
├── schemas.py                       # Pydantic
├── permissions.py                   # blogger:* 权限字符串
├── legacy_field_permissions.py      # QUOTE/CONTACT_VISIBLE_ROLES（U09 清理）
├── exceptions.py                    # 业务异常 + re-export FieldPermissionDenied
├── domain.py                        # 业务规则验证
├── repository.py                    # BloggerRepository（含 search 防侧信道 + upsert_atomic）
├── service.py                       # BloggerService（含 U10b 4 钩子占位）
├── deps.py                          # FastAPI 依赖注入
└── api.py                           # FastAPI Router

backend/app/core/metrics.py          # 修改：追加 blogger_search_results_count
backend/app/main.py                  # 修改：注册 blogger router

backend/alembic/versions/005_u03_create_blogger_table.py  # 新增

backend/tests/conftest.py            # 修改：追加 pr_manager_role + blogger_factory
backend/tests/
├── unit/
│   ├── test_blogger_domain.py
│   └── test_blogger_field_perms.py
├── integration/
│   ├── test_blogger_crud.py
│   ├── test_blogger_search.py     # 含防侧信道
│   └── test_blogger_upsert.py     # FB7 upsert
├── api/
│   └── test_blogger_api.py
└── performance/
    └── test_blogger_search_perf.py

frontend/src/features/blogger/
├── api.ts
└── types.ts

aidlc-docs/construction/U03/code/
├── README.md
├── api-endpoints.md
└── test-coverage.md
```

---

## 2. 执行步骤

### Step 1 — modules/blogger 业务代码（12 文件）
- [x] 1.1 `__init__.py` / `enums.py`
- [x] 1.2 `permissions.py` / `legacy_field_permissions.py` / `exceptions.py`
- [x] 1.3 `models.py`（Blogger ORM）
- [x] 1.4 `schemas.py`（Pydantic）
- [x] 1.5 `domain.py`（业务规则 + dict diff + audit_safe_changes）
- [x] 1.6 `repository.py`（含 search 防侧信道 + upsert_atomic）
- [x] 1.7 `service.py`（含 U10b 4 钩子 NotImplementedError）
- [x] 1.8 `deps.py` / `api.py`

### Step 2 — 横切修改 + Alembic 迁移
- [x] 2.1 修改 `core/metrics.py` 追加 blogger_search_results_count
- [x] 2.2 修改 `main.py` 注册 blogger router
- [x] 2.3 创建 `005_u03_create_blogger_table.py`

### Step 3 — 测试套件（7 文件）
- [x] 3.1 修改 `conftest.py` 追加 fixture
- [x] 3.2 unit/test_blogger_domain.py
- [x] 3.3 unit/test_blogger_field_perms.py
- [x] 3.4 integration/test_blogger_crud.py
- [x] 3.5 integration/test_blogger_search.py（含防侧信道关键测试）
- [x] 3.6 integration/test_blogger_upsert.py
- [x] 3.7 api/test_blogger_api.py
- [x] 3.8 performance/test_blogger_search_perf.py

### Step 4 — Frontend 骨架 + 文档摘要
- [x] 4.1 frontend/src/features/blogger/types.ts
- [x] 4.2 frontend/src/features/blogger/api.ts
- [x] 4.3 aidlc-docs/U03/code/README.md
- [x] 4.4 aidlc-docs/U03/code/api-endpoints.md
- [x] 4.5 aidlc-docs/U03/code/test-coverage.md

### Step 5 — 完成校验
- [x] 5.1 全部诊断器无警告
- [x] 5.2 Plan 全部 [x]
- [x] 5.3 故事追溯：EP04-S01~S03 全覆盖

---

## 3. 故事追溯矩阵

| 故事 | 实施位置 | 测试位置 |
|---|---|---|
| EP04-S01 创建博主 | `service.create_blogger` + `api.create_blogger` | `test_blogger_crud.py:TestCreateBlogger` |
| EP04-S02 编辑博主（含 quote audit 脱敏） | `service.update_blogger` + `domain.build_blogger_audit_changes` | `test_blogger_crud.py:TestUpdateBlogger` |
| EP04-S03 搜索筛选（含字段权限） | `service.list_bloggers` + `repository.list` | `test_blogger_search.py` |

---

## 4. 关键质量门

- ✅ Pydantic v2 严格模式
- ✅ SQLAlchemy 2.0 async + asyncpg
- ✅ mypy strict / ruff S+ASYNC+UP
- ✅ Blogger 继承 TenantScopedModel + RLS 自动启用
- ✅ pg_trgm 单字段 GIN 索引（与拼接表达式不同，更轻量）
- ✅ GIN JSONB 索引（category_tags / quality_tags）
- ✅ 字段权限隔离（modules/blogger/legacy_field_permissions.py）
- ✅ 审计敏感值脱敏（quote/wechat/phone 仅 *_changed: true）
- ✅ upsert 数据库原子操作 + partial UNIQUE 对齐
- ✅ search 降级语义 + 防侧信道（双层落地）
- ✅ U10b 4 钩子 NotImplementedError 占位
- ✅ FieldPermissionDenied 复用 U02 不重复定义

---

## 5. 文件总数预估

| 类别 | 数量 |
|---|---|
| Python 业务代码 | 12 |
| Python 横切修改 | 2 modified |
| Alembic migration | 1 |
| Python 测试 | 7（2 unit + 3 integration + 1 api + 1 performance） |
| 测试 fixture 修改 | 1 modified |
| TypeScript 前端 | 2 |
| 文档摘要 | 3 |
| **新增合计** | **~25 新文件 + 3 修改** |

---

## 6. 与下一阶段衔接

U03 完成后可选路径：
- **U04（推广合作核心）**：依赖 U02+U03，关键路径核心
- **U06a（手动导入框架）**：依赖 U01，可与 U04 并行（但 U06b/c 适配器需要 U02/U03）
- **MVP-end Build & Test**：阶段末统一跑测试
