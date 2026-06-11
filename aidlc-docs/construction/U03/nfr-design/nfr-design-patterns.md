# U03 NFR 设计模式（NFR Design Patterns）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性 NFR 模式 + 复用 U02 已建立模式  
> 阅读：U01 9 个通用模式 + U02 4 个增量模式 + 本文件 2 个 U03 增量

---

## 1. 与 U01 / U02 模式的关系

### 1.1 完全继承（不重新定义）

| U01 模式 | U03 复用方式 |
|---|---|
| Pattern 1 多租户隔离 | Blogger 继承 `TenantScopedModel` |
| Pattern 2 审计日志 | 新增 4 个 action（blogger.create / update / delete / disable / restore + create_via_import / update_via_import） |
| Pattern 4 附件管理 | U03 不使用（无主图字段） |
| Pattern 5 4 层速率限制 | U03 全 API 默认应用 |
| Pattern 6 错误处理 | 沿用 + 6 个新错误码（含 `BLOGGER_XHS_ID_CONFLICT` 含 details.existing_blogger_id） |
| Pattern 7 监控 | U03 新增 1 个自定义指标 + module=blogger tag |
| Pattern 8 备份恢复 | Blogger 表自动纳入 |
| Pattern 9 健康检查 | 沿用 /health + /ready，无新依赖 |

### 1.2 复用 U02 增量模式

| U02 Pattern | U03 适配 |
|---|---|
| **P-U02-01** GIN trgm + 降级语义 | 改为单字段 GIN trgm（数据量小），降级语义不变；增加防侧信道 |
| **P-U02-02** 字段权限硬编码过渡 | 字段对象从 cost_price/purchase_price 改为 quote/wechat/phone；常量改名 QUOTE_VISIBLE_ROLES + CONTACT_VISIBLE_ROLES |
| **P-U02-03** 数据库原子 upsert | 完全镜像，仅更换 ORM 类（Blogger）和业务键（xiaohongshu_id） |
| **P-U02-04** 软删 + 引用检查 | 完全镜像，TODO U04 注入 PromotionRepository |

### 1.3 U03 增量模式（2 个）

| 模式 | 解决的问题 | 章节 |
|---|---|---|
| **P-U03-01** 单字段 GIN trgm + 防侧信道搜索 | 3000 博主关键字搜索 P95 ≤ 150ms + wechat 字段不通过 keyword 泄露 | §2 |
| **P-U03-02** GIN JSONB tag 包含查询 | category_tags / quality_tags 按 tag 筛选 P95 ≤ 100ms | §3 |

---

## 2. Pattern P-U03-01 — 单字段 GIN trgm + 防侧信道

### 2.1 问题
- BR-U03-50 关键字模糊搜索：3000 博主，P95 ≤ 150ms
- BR-U03-50 同时要求：wechat 字段在用户无 CONTACT_VISIBLE_ROLES 时**不参与匹配**
- 仅响应过滤不够（命中行为本身泄露 wechat 信息）

### 2.2 设计

#### 2.2.1 索引层（与 U02 拼接表达式区别）
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- U02 已启用，幂等

CREATE INDEX idx_blogger_nickname_trgm ON blogger
  USING gin (nickname gin_trgm_ops)
  WHERE is_deleted = false;

CREATE INDEX idx_blogger_xhs_id_trgm ON blogger
  USING gin (xiaohongshu_id gin_trgm_ops)
  WHERE is_deleted = false;
```

PostgreSQL planner 在 OR 多字段 ILIKE 时使用 `BitmapOr` 合并多个 GIN 索引扫；3000 行规模性能足够。

#### 2.2.2 service 层防侧信道（核心）
```python
# modules/blogger/service.py
class BloggerService:
    async def list_bloggers(self, filters, page, page_size, user) -> BloggerPage:
        role_codes = await self._roles.list_codes_for_user(user.id)
        can_search_contact = has_contact_visibility(role_codes)
        
        return await self._repo.list(
            filters=filters,
            page=page,
            page_size=page_size,
            include_wechat_in_keyword=can_search_contact,  # ← 防侧信道关键参数
        )
```

```python
# modules/blogger/repository.py
class BloggerRepository:
    async def list(
        self,
        *,
        filters: BloggerListFilters,
        page: int,
        page_size: int,
        include_wechat_in_keyword: bool = False,  # ← 防侧信道关键参数
    ) -> tuple[Sequence[Blogger], int]:
        stmt = select(Blogger).where(Blogger.is_deleted.is_(False))
        ...
        if filters.keyword:
            pattern = f"%{filters.keyword}%"
            clauses = [
                Blogger.nickname.ilike(pattern),
                Blogger.xiaohongshu_id.ilike(pattern),
            ]
            if include_wechat_in_keyword:
                clauses.append(Blogger.wechat.ilike(pattern))
            stmt = stmt.where(or_(*clauses))
```

#### 2.2.3 降级语义（与 P-U02-01 一致）
```python
# api.py / service.py
# 业务未匹配 → 200 + 空数组 (matched=false)
# 系统失败 → 异常自然冒泡 → 5xx + Sentry，绝不伪装空结果
```

### 2.3 测试覆盖（关键）

| 测试 | 验证 |
|---|---|
| `test_keyword_matches_nickname_for_pr` | PR 角色（有 CONTACT 权限）keyword 命中 nickname |
| `test_keyword_matches_wechat_for_pr` | PR 角色 keyword 可命中 wechat |
| `test_keyword_does_not_match_wechat_for_designer` | designer 角色 keyword 即使匹配 wechat 也不命中（防侧信道） |
| `test_db_error_propagates_no_empty_disguise` | 系统失败异常冒泡，绝不返回空数组 |

### 2.4 升级路径
- 突破 1 万行 / 租户 + P95 > 200ms → 升级到 U02 拼接表达式 GIN（与 U02 一致）
- 突破 5 万行 → 评估 tsvector 全文搜索

---

## 3. Pattern P-U03-02 — GIN JSONB tag 包含查询

### 3.1 问题
- `category_tags` / `quality_tags` 是 JSONB 数组，按 tag 筛选频次高（PR 选博主主要靠 tag）
- 全表扫不可接受（即使 3000 行 + JSON 解析）

### 3.2 设计

#### 3.2.1 索引
```sql
CREATE INDEX idx_blogger_category_tags ON blogger USING gin (category_tags);
CREATE INDEX idx_blogger_quality_tags ON blogger USING gin (quality_tags);
```

PostgreSQL JSONB GIN 默认使用 `jsonb_ops` 操作类，支持 `@>` 包含查询命中索引。

#### 3.2.2 service 层
```python
# repository.py
if filters.category_tag:
    stmt = stmt.where(
        Blogger.category_tags.contains(
            cast([filters.category_tag], JSONB)
        )
    )

if filters.quality_tag:
    stmt = stmt.where(
        Blogger.quality_tags.contains(
            cast([filters.quality_tag], JSONB)
        )
    )
```

#### 3.2.3 EXPLAIN ANALYZE 验证
```sql
EXPLAIN ANALYZE
SELECT * FROM blogger
WHERE tenant_id = :t
  AND category_tags @> '["穿搭"]'::jsonb;

-- 期望：Bitmap Index Scan on idx_blogger_category_tags
```

### 3.3 测试覆盖
- `test_category_tag_filter_uses_gin` — EXPLAIN 命中 GIN
- `test_quality_tag_filter` — 单 tag 包含
- `test_multi_tag_and_filter` — 多 tag AND（`@> '["a", "b"]'`）

### 3.4 演进选项
- 客户要求 tag 多语言 / 同义词 → 引入 `blogger_tag_dict` 字典表 + 多对多关联（V2+ 范围）
- 当前 JSONB 数组方案足够 MVP / V1 使用

---

## 4. 复用 U02 模式清单

### 4.1 字段权限硬编码（P-U02-02 适配）

```python
# modules/blogger/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U03.
REMOVE AFTER U09 (字段级权限) is implemented.
"""

QUOTE_VISIBLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager", "finance"}
)

CONTACT_VISIBLE_ROLES: frozenset[str] = frozenset(
    {"admin", "pr", "pr_manager"}
)


def has_quote_visibility(role_codes) -> bool: ...
def has_contact_visibility(role_codes) -> bool: ...
```

读 / 写过滤实施位置：
- `service.BloggerService._to_response`：按角色过滤 quote/wechat/phone → None
- `service.BloggerService._check_sensitive_write_permission`：写操作含敏感字段且无权 → 403

### 4.2 数据库原子 upsert（P-U02-03 镜像）

```python
class BloggerRepository:
    async def upsert_atomic(
        self, *, tenant_id: UUID, values: dict
    ) -> tuple[Blogger, bool]:
        update_fields = {
            k: v for k, v in values.items()
            if k not in {"id", "tenant_id", "created_at",
                         "xiaohongshu_id", "is_deleted"}
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

### 4.3 软删 + 引用检查（P-U02-04 镜像）

```python
class BloggerService:
    async def check_references(self, blogger_id: UUID) -> dict:
        """U03 阶段：promotion 表不存在
        TODO U04: 改为 await self._promotion_repo.count_by_blogger(blogger_id)
        """
        _ = blogger_id
        return {"promotion_count": 0}
    
    async def soft_delete_blogger(self, blogger_id, user) -> None:
        blogger = await self._repo.get_by_id(blogger_id)
        if blogger is None:
            raise BloggerNotFoundError(...)
        refs = await self.check_references(blogger_id)
        if sum(refs.values()) > 0:
            raise BloggerHasReferenceError(...)
        blogger.is_deleted = True
        blogger.is_active = False
        await self._audit.log(action="blogger.delete", ...)
        await self._session.commit()
```

---

## 5. U10b 扩展点（U03 占位）

### 5.1 问题
U10b 阶段需要自动计算：
- `blogger_type`：按 follower_count 自动分类（素人 / KOC / KOL / 明星）
- `quality_tags`：自动追加质量标签（如 "高互动"）
- `is_suspected_fake`：假号自动判定

U03 阶段需要为这些自动化预留接口。

### 5.2 设计：4 个钩子方法占位

```python
# modules/blogger/service.py
class BloggerService:
    async def recompute_blogger_type(self, blogger_id: UUID) -> Blogger:
        """U10b: 按 follower_count 自动重算 blogger_type."""
        # TODO U10b: implement
        raise NotImplementedError("Implemented in U10b BloggerTagService")
    
    async def recompute_quality_tags(self, blogger_id: UUID) -> Blogger:
        """U10b: 按互动率/合作历史自动追加质量标签."""
        # TODO U10b: implement
        raise NotImplementedError("Implemented in U10b BloggerTagService")
    
    async def mark_suspected_fake(
        self, blogger_id: UUID, reason: str
    ) -> Blogger:
        """U10b: 假号嫌疑自动判定（粉丝突增 / 互动率异常）."""
        # TODO U10b: implement
        raise NotImplementedError("Implemented in U10b BloggerTagService")
    
    async def bulk_recompute_tags(self) -> int:
        """U10b: Celery beat 定时批量任务，返回处理博主数."""
        # TODO U10b: implement
        raise NotImplementedError("Implemented in U10b BloggerTagService")
```

### 5.3 测试覆盖
U03 阶段不在测试覆盖（NotImplementedError 抛出即可），U10b 实施时增加测试。

---

## 6. 监控与 SLO

### 6.1 SLI（与 NFR §3.1 一致）

| SLI | SLO 目标 |
|---|---|
| `/api/bloggers/` 列表 P95 | ≤ 200ms |
| `/api/bloggers/?keyword=` P95 | ≤ 150ms |
| `/api/bloggers/?category_tag=` P95 | ≤ 100ms |
| 写请求 P95 | ≤ 150ms |
| 5xx 错误率 | ≤ 1% |

### 6.2 自定义 Prometheus 指标

```python
# core/metrics.py（追加 1 个）
blogger_search_results_count: Histogram = Histogram(
    "blogger_search_results_count",
    "Distribution of blogger search result counts",
    buckets=(0, 1, 5, 20, 100),
)
```

实现位置：`backend/app/core/metrics.py`（U02 已建立的目录）。

### 6.3 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/bloggers.*"}) > 1` 持续 5min | Prometheus alertmanager | SRE |
| 零候选率（零结果搜索）> 30% 持续 30min | Prometheus alertmanager | 业务 + 后端 |
| `/api/bloggers.*` 5xx > 5% 持续 5min | Sentry | 后端 leader |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 单字段 GIN trgm 索引方案与 3000 博主规模匹配 | ✅ |
| 防侧信道：wechat 在 keyword 中按权限决定是否参与匹配 | ✅ |
| 降级语义严格区分系统失败 vs 业务未匹配 | ✅ |
| GIN JSONB 索引支撑 tag 包含查询 ≤ 100ms | ✅ |
| 字段权限模式与 U02 一致（QUOTE/CONTACT_VISIBLE_ROLES 命名清晰） | ✅ |
| 审计敏感值脱敏（quote/wechat/phone 仅记 *_changed: true） | ✅ |
| upsert 数据库原子 + partial UNIQUE 对齐 + 不恢复软删 | ✅ |
| 软删引用检查 U03 占位 + U04 注入路径 | ✅ |
| U10b 4 个钩子方法占位 + NotImplementedError | ✅ |
