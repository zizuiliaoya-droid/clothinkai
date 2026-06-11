# U03 技术栈决策（Tech Stack Decisions）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性技术选型；通用技术栈见 `U01/nfr-requirements/tech-stack-decisions.md` + `U02/nfr-requirements/tech-stack-decisions.md`

---

## 1. 与 U01/U02 技术栈的关系

### 1.1 完全继承

| 类别 | 来源 | 版本 | U03 沿用说明 |
|---|---|---|---|
| Web Framework | U01 | FastAPI 0.115.x | 不变 |
| ORM | U01 | SQLAlchemy 2.0.x async | 不变 |
| DB Driver | U01 | asyncpg 0.30.x | 不变 |
| 数据库 | U01 | PostgreSQL 16.x | 沿用，pg_trgm 已由 U02 启用 |
| Schema 验证 | U01 | Pydantic 2.x（strict） | 不变 |
| 缓存 | U01 | Redis 7.x | 不变（U03 不用） |
| 任务队列 | U01 | Celery 5.x | 不变（U03 无任务） |
| 类型检查 | U01 | mypy strict | 不变 |
| Linter | U01 | ruff | 不变 |
| 测试 | U01 | pytest + pytest-asyncio | 不变 |
| 监控 | U01 | Prometheus + Sentry + Loki | 沿用 + 新增 1 个 metric |
| Migration | U01 | Alembic | 不变 |

### 1.2 复用 U02 的设计模式

| 模式 | 来源 | U03 应用 |
|---|---|---|
| 字段权限硬编码过渡 | U02 P-U02-02 | `modules/blogger/legacy_field_permissions.py` 定义 QUOTE_VISIBLE_ROLES + CONTACT_VISIBLE_ROLES |
| 审计敏感值脱敏 | U02 BR-U02-31 | quote / wechat / phone 仅记 `*_changed: true` |
| 数据库原子 upsert | U02 P-U02-03 | `BloggerRepository.upsert_atomic()` 完全镜像 SkuRepository.upsert_atomic |
| 软删 + 引用检查 | U02 P-U02-04 | `BloggerService.check_references()` 含 TODO U04 |
| match 降级语义 | U02 P-U02-01 | search service 不 try/except DB 异常，业务/系统失败严格区分 |

### 1.3 U03 增量决策

| 决策项 | 选项 | 理由 |
|---|---|---|
| 模糊匹配实现 | **GIN trgm 单字段索引**（不拼接） | §2 |
| JSONB tag 查询 | **GIN JSONB 索引** | §3 |
| 索引策略 | B-tree + GIN trgm + GIN JSONB 混合 | §4 |

---

## 2. 模糊匹配：GIN trgm 单字段（与 U02 拼接表达式区别）

### 2.1 决策
U03 使用 **单字段 GIN trgm 索引**（仅 `nickname` 和 `xiaohongshu_id`），不使用拼接表达式。

### 2.2 与 U02 的对比

| 维度 | U02 style | U03 blogger |
|---|---|---|
| 数据规模 | 5 万 / 租户 | 3000 / 租户 |
| 索引类型 | GIN trgm（拼接表达式） | GIN trgm（单字段） |
| 查询模式 | 拼接表达式 ILIKE | 多字段 OR + 单字段 ILIKE |
| 示例 | `(style_code \|\| ' ' \|\| style_name) ILIKE` | `nickname ILIKE OR xiaohongshu_id ILIKE` |

### 2.3 单字段索引足够的理由
- 数据量小（3000 vs 5 万），全表扫成本可控
- OR 多字段查询时，PostgreSQL planner 会用 BitmapOr 合并多个 GIN 索引扫
- 索引大小小（3000 行 nickname GIN trgm < 1MB）
- 维护成本低（单字段索引比表达式索引更易理解）

### 2.4 定义
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- U02 已启用，幂等

CREATE INDEX idx_blogger_nickname_trgm ON blogger
  USING gin (nickname gin_trgm_ops)
  WHERE is_deleted = false;

CREATE INDEX idx_blogger_xhs_id_trgm ON blogger
  USING gin (xiaohongshu_id gin_trgm_ops)
  WHERE is_deleted = false;
```

### 2.5 升级路径
- 突破 1 万行 / 租户 + P95 > 200ms 持续 1 周 → 改拼接表达式 GIN trgm（与 U02 同模式）
- 突破 5 万行 → 评估 tsvector / Elasticsearch（V2+ 范围）

---

## 3. JSONB tag 查询：GIN JSONB

### 3.1 决策
`category_tags` 和 `quality_tags` 用 JSONB 数组存储 + GIN 索引。

### 3.2 候选方案对比

| 方案 | 性能 | 复杂度 | 选用 |
|---|---|---|---|
| EAV 关联表（blogger_tag） | 高（JOIN） | 中 | ❌（U03 仅 3 个 tag 字段，过度设计） |
| TEXT[] 数组 + GIN | 高 | 中 | ❌（PostgreSQL JSONB 更灵活） |
| **JSONB array + GIN** | 高 | 低 | ✅ |
| 字典表（tag 元数据） | 中 | 高 | ❌（V2+ 视实际需要再升级） |

### 3.3 选用 JSONB 的理由
- 业务上 tag 定义灵活，无需预定义字典
- 后续 U10b 自动计算 quality_tags 时直接 append 数组元素
- GIN 索引支持 `@>` 包含查询性能 < 100ms

### 3.4 定义
```sql
CREATE INDEX idx_blogger_category_tags ON blogger USING gin (category_tags);
CREATE INDEX idx_blogger_quality_tags ON blogger USING gin (quality_tags);
```

### 3.5 查询示例
```sql
-- 按单个 tag 筛选
SELECT * FROM blogger WHERE category_tags @> '["穿搭"]'::jsonb;

-- 按多个 tag 筛选（AND）
SELECT * FROM blogger WHERE quality_tags @> '["高互动", "真实粉丝"]'::jsonb;
```

### 3.6 演进选项（V2+）
- 若客户要求 tag 多语言 / 同义词 / 颜色等元数据 → 引入 `blogger_tag_dict` 字典表
- 改造时通过 alembic migration 数据迁移（与 U02 category 演化路径一致）

---

## 4. 索引策略

### 4.1 完整索引清单（10 个）

| 索引 | 类型 | 列 / 表达式 | 用途 |
|---|---|---|---|
| `uq_blogger_xiaohongshu_id` | B-tree UNIQUE (partial) | `(tenant_id, xiaohongshu_id) WHERE is_deleted=false` | 业务键唯一 |
| `idx_blogger_tenant_active` | B-tree | `(tenant_id, is_active, is_deleted)` | 列表过滤 |
| `idx_blogger_type` | B-tree | `(tenant_id, blogger_type)` | 博主类型筛选 |
| `idx_blogger_follower_count` | B-tree | `(tenant_id, follower_count)` | 范围查询 |
| `idx_blogger_platform` | B-tree | `(tenant_id, platform)` | 跨平台扩展（V1+） |
| `idx_blogger_suspected_fake` | B-tree (partial) | `(tenant_id) WHERE is_suspected_fake=true` | 假号筛选 |
| `idx_blogger_nickname_trgm` | **GIN trgm** (partial) | `(nickname gin_trgm_ops) WHERE is_deleted=false` | 昵称 ILIKE |
| `idx_blogger_xhs_id_trgm` | **GIN trgm** (partial) | `(xiaohongshu_id gin_trgm_ops) WHERE is_deleted=false` | xiaohongshu_id ILIKE |
| `idx_blogger_category_tags` | **GIN JSONB** | `(category_tags)` | tag 包含查询 |
| `idx_blogger_quality_tags` | **GIN JSONB** | `(quality_tags)` | tag 包含查询 |

### 4.2 索引大小预估（3000 博主 / 租户）

| 索引 | 估算大小 |
|---|---|
| GIN trgm（nickname + xhs_id） | < 2 MB |
| GIN JSONB（category + quality tags） | < 1 MB |
| B-tree 索引合计 | < 1 MB |
| **合计** | **< 5 MB / 租户** |

远小于 U02 索引规模（U02 单租户 ~150MB），无内存压力。

---

## 5. 字段级权限（U02 模式延续）

### 5.1 决策
完全复用 U02 P-U02-02 模式：
- 隔离在 `modules/blogger/legacy_field_permissions.py`（不污染 core）
- service 层硬编码角色判断 + TODO U09 注释
- U09 阶段 grep `legacy_field_permissions` 一次清理

### 5.2 临时常量文件

```python
# modules/blogger/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U03.

REMOVE AFTER U09 (字段级权限) is implemented.

To find all usage:
    grep -rn "legacy_field_permissions" backend/

This module is intentionally placed in modules/blogger/ rather than core/
to avoid polluting the core layer with blogger-domain transitional rules.
"""

QUOTE_VISIBLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager", "finance"}
)
"""可见 / 可写 quote 字段的角色。"""

CONTACT_VISIBLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager"}
)
"""可见 / 可写 wechat / phone 字段的角色（finance 不在内）。"""
```

### 5.3 读 / 写权限 helper

```python
def has_quote_visibility(role_codes) -> bool: ...
def has_contact_visibility(role_codes) -> bool: ...
```

---

## 6. upsert 策略（与 U02 完全一致）

### 6.1 决策
完全镜像 U02 SkuRepository.upsert_atomic 模式：

```python
# modules/blogger/repository.py
class BloggerRepository:
    async def upsert_atomic(
        self, *, tenant_id: UUID, values: dict
    ) -> tuple[Blogger, bool]:
        """ON CONFLICT (tenant_id, xiaohongshu_id) WHERE is_deleted=false DO UPDATE
        
        Returns: (blogger, is_inserted) — 是否 INSERT 路径
        """
        update_fields = {
            k: v for k, v in values.items()
            if k not in {
                "id", "tenant_id", "created_at",
                "xiaohongshu_id",  # 业务键不更新
                "is_deleted",
            }
        }
        update_fields["updated_at"] = sa.func.now()
        
        full_values = {"tenant_id": tenant_id, **values}
        stmt = pg_insert(Blogger).values(**full_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Blogger.tenant_id, Blogger.xiaohongshu_id],
            index_where=Blogger.is_deleted.is_(False),
            set_=update_fields,
        ).returning(Blogger, sa.text("(xmax = 0) AS is_inserted"))
        
        result = await self._session.execute(stmt)
        row = result.one()
        return row[0], bool(row.is_inserted)
```

### 6.2 边界（与 U02 完全一致）

| 约束 | 说明 |
|---|---|
| 与 partial UNIQUE 对齐 | `index_where=Blogger.is_deleted.is_(False)` |
| 不"恢复"软删行 | upsert 仅作用于 active 行 |
| 不暴露 HTTP | service 层内部 API，U06c 直接调用 |
| audit 区分入口 | `blogger.create / update / create_via_import / update_via_import` |
| 复用同一套校验 | Pydantic / 字段权限 / 审计 |

---

## 7. 软删 + 引用检查（与 U02 模式一致）

### 7.1 决策
最简硬编码 + TODO 注释（与 U02 Q7=A 决策一致）：

```python
class BloggerService:
    async def check_references(self, blogger_id: UUID) -> dict:
        """U03 阶段：promotion 表不存在，返回零引用
        TODO U04: 改为 await self._promotion_repo.count_by_blogger(blogger_id)
        """
        _ = blogger_id
        return {"promotion_count": 0}
```

U04 阶段直接修改 `BloggerService` 注入 `PromotionRepository`，无需注册器框架。

---

## 8. 依赖项变更

### 8.1 Python 依赖
**无新增**。U01/U02 已涵盖全部依赖。

### 8.2 PostgreSQL 扩展
**无新增**。`pg_trgm` 已由 U02 启用。

### 8.3 其他
**无**。

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 所有技术选型与 U01/U02 兼容 | ✅ |
| pg_trgm 已启用（U02 阶段） | ✅ |
| 索引策略覆盖所有性能 SLA 路径 | ✅ |
| 字段权限模式与 U02 一致（含 TODO U09 清理路径） | ✅ |
| upsert 边界与 U02 完全一致 | ✅ |
| 软删 + 引用检查与 U02 一致 | ✅ |
| 无新依赖 / 无版本冲突 | ✅ |
