# U02 NFR 设计模式（NFR Design Patterns）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 特异性 NFR 模式；通用模式继承 `aidlc-docs/construction/U01/nfr-design/nfr-design-patterns.md`  
> 阅读：U01 通用模式 + 本文件 4 个增量模式

---

## 1. 与 U01 NFR 模式的关系

### 1.1 完全继承（不重新定义）

| U01 模式 | U02 复用方式 |
|---|---|
| Pattern 1：多租户隔离（ORM Session + RLS） | Style/Sku/Brand/StyleDetailImage 继承 `TenantScopedModel` |
| Pattern 2：审计日志（@audit + AuditService） | U02 新增 5 个 action（style.create/update/delete/disable/restore + sku.create/update/delete + sku.create_via_import / update_via_import + brand.* + style.disable_with_skus） |
| Pattern 3：状态机 | U02 不使用（design_status 单字段无状态机，U10a 引入） |
| Pattern 4：附件管理 | Style.main_image_id + StyleDetailImage 复用 AttachmentService |
| Pattern 5：4 层速率限制 | U02 全 API 默认应用 IP / user / write 三级限流 |
| Pattern 6：错误处理 | 沿用 U01 ResourceConflictError / ValidationError / PermissionDeniedError + 新增 FieldPermissionDenied |
| Pattern 7：监控（Prometheus + Sentry + Loki） | U02 增加 module=product tag + 2 个自定义 Counter/Histogram |
| Pattern 8：备份恢复 | U02 4 张新表自动纳入备份 |
| Pattern 9：健康检查 | 沿用 /health + /ready，无新依赖 |

### 1.2 U02 增量模式（4 个）

| 模式 | 解决的问题 | 章节 |
|---|---|---|
| **P-U02-01** GIN trgm 模糊搜索 + 降级语义 | 5 万行模糊匹配 P95 ≤ 300ms + 系统失败/业务未匹配区分 | §2 |
| **P-U02-02** 字段权限硬编码过渡 | U09 前的字段级权限替代方案 + U09 清理路径 | §3 |
| **P-U02-03** 数据库原子 upsert | upsert 并发安全 + 与软删 partial UNIQUE 对齐 + audit 区分入口 | §4 |
| **P-U02-04** 软删 + 引用检查 | sku 软删时 promotion/order 引用未拉起 → U02 占位 + U04/U16 注入 | §5 |

---

## 2. Pattern P-U02-01 — GIN trgm 模糊搜索 + 降级语义

### 2.1 问题
- BR-U02-51 模糊匹配需求：5 万行 / 租户场景，模糊查询 P95 ≤ 300ms
- 纯 ILIKE 无索引：5 万行 P95 ~800ms（超 SLA）
- 必须严格区分"业务未匹配"和"系统失败"，避免前端误导用户

### 2.2 设计

#### 2.2.1 索引层
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_style_search_trgm ON style
  USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false;
```

#### 2.2.2 查询层（必须与索引表达式严格一致）
```sql
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
LIMIT 20;
```

#### 2.2.3 降级语义（区分两类错误）

```python
# modules/product/api.py
@router.get("/styles/match")
async def match_styles(
    style_code: str | None = None,
    keyword: str | None = None,
    service: StyleService = Depends(get_style_service),
) -> MatchResponse:
    try:
        if style_code:
            return await service.match_by_code(style_code)
        if keyword:
            return await service.match_by_keyword(keyword)
        raise ValidationError("style_code 或 keyword 至少一个必填")
    except ResourceNotFound:
        # 业务未匹配 → 200 + 空候选（前端允许手动输入）
        return MatchResponse(matched=False, candidates=[], total=0)
    # 系统级 5xx 异常 → 自然冒泡到全局 error handler → 5xx + Sentry
    # 不在此处捕获 SQLAlchemyError / TimeoutError → 绝不伪装空候选
```

#### 2.2.4 性能验证（CI 基准测试）
```python
# tests/performance/test_match_perf.py
@pytest.mark.performance
@pytest.mark.asyncio
async def test_match_perf_with_5w_styles(generate_50k_styles):
    """5 万 style 模糊匹配 P95 ≤ 300ms"""
    durations = []
    for keyword in random_keywords(100):
        start = time.perf_counter()
        await client.get(f"/api/styles/match?keyword={keyword}")
        durations.append(time.perf_counter() - start)
    p95 = sorted(durations)[94]
    assert p95 < 0.3, f"P95={p95}s 超 300ms"
```

### 2.3 监控点

| 指标 | 类型 | 阈值 / 告警 |
|---|---|---|
| `http_request_duration_seconds{handler="/api/styles/match",method="GET"}` | histogram | P95 > 1s 持续 5min → SRE 告警 |
| `style_search_results_count` | histogram | total=0 比例 > 30% → 业务告警（可能是"商品库没有这些款"或"前端在传错误关键字"） |

### 2.4 不达标时的诊断顺序
1. `EXPLAIN ANALYZE`：必须是 `Bitmap Index Scan on idx_style_search_trgm`，不是 `Seq Scan`（若是后者说明查询表达式与索引表达式未对齐）
2. `ANALYZE style;`：刷新 planner 统计信息
3. 检查候选返回行数：`LIMIT 20` 之前若候选 > 几千行，加 `WHERE sim > 0.1` 阈值过滤
4. 检查 GIN 索引膨胀：`pg_indexes_size('style', 'idx_style_search_trgm')` 异常 → SRE `REINDEX INDEX CONCURRENTLY`
5. 仍不达标 → 升级到 PostgreSQL tsvector 全文搜索（V1 范围）

### 2.5 升级路径
| 触发 | 方案 |
|---|---|
| 单租户 style ≥ 50 万行 或 P95 > 500ms 持续 1 周 | 评估 tsvector + GIN 全文搜索（service 接口不变） |
| 多语言搜索需求（如英文产品） | tsvector + 多语言词典 |
| 商品图相似度搜索 | 独立 vector DB（V2+ 范围） |

---

## 3. Pattern P-U02-02 — 字段权限硬编码过渡

### 3.1 问题
- 字段级权限正式落地在 U09（V1 阶段）
- U02 已有 cost_price / purchase_price 敏感字段，必须有过渡方案
- 过渡方案必须满足：
  - **不污染 core 层**（PRICE_VISIBLE_ROLES 是产品域临时规则，不应放 core）
  - **明确清理路径**（U09 落地时一行命令找全部硬编码位置）
  - **审计与展示分层**（DBA 通过 audit_log 看不到敏感值，与威胁模型一致）

### 3.2 设计

#### 3.2.1 临时常量文件（隔离）

```python
# modules/product/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U02.

REMOVE AFTER U09 (字段级权限) is implemented.

To find all usage:
  grep -rn "legacy_field_permissions" backend/
"""

PRICE_VISIBLE_ROLES = frozenset(["admin", "follower", "finance"])
"""可见 / 可写 cost_price 与 purchase_price 的角色（管理员 / 跟单 / 财务）。
其他角色看不到这两个字段，PUT 时含这两个字段会返回 403。
base_price 全角色可见可写，不在此列。"""
```

#### 3.2.2 service 字段读过滤

```python
# modules/product/service.py
from app.modules.product.legacy_field_permissions import PRICE_VISIBLE_ROLES

class SkuService:
    def to_response(self, sku: Sku, user: CurrentUser) -> SkuResponse:
        """U02 字段过滤
        TODO U09: 改为基于 Permission.field_filter() 的字段级权限"""
        data = {
            "id": sku.id,
            "style_id": sku.style_id,
            "sku_code": sku.sku_code,
            "color": sku.color,
            "size": sku.size,
            "base_price": sku.base_price,
            "sourcing_type": sku.sourcing_type,
            "is_active": sku.is_active,
        }
        if user.has_any_role(PRICE_VISIBLE_ROLES):
            data["cost_price"] = sku.cost_price
            data["purchase_price"] = sku.purchase_price
        return SkuResponse(**data)
```

#### 3.2.3 service 字段写权限

```python
def _check_price_write_permission(self, payload: SkuUpdate, user: CurrentUser) -> None:
    """TODO U09: 改为基于 Permission.field_writable() 检查"""
    if not user.has_any_role(PRICE_VISIBLE_ROLES):
        if "cost_price" in payload.model_fields_set:
            raise FieldPermissionDenied(field="cost_price")
        if "purchase_price" in payload.model_fields_set:
            raise FieldPermissionDenied(field="purchase_price")
```

#### 3.2.4 审计层敏感值脱敏

```python
SENSITIVE_VALUE_FIELDS = {"cost_price", "purchase_price"}  # 不存历史值
SENSITIVE_FIELDS = {"sku_code", "cost_price", "purchase_price",
                    "base_price", "sourcing_type"}         # 才写 audit

def _build_audit_changes(self, changes: dict) -> dict:
    """构造审计安全的 changes（敏感值字段仅记标记）"""
    audit_safe = {}
    for field, diff in changes.items():
        if field not in SENSITIVE_FIELDS:
            continue
        if field in SENSITIVE_VALUE_FIELDS:
            audit_safe[f"{field}_changed"] = True
        else:
            audit_safe[field] = diff
    return audit_safe
```

### 3.3 U09 切换路径

```bash
# Step 1: 找全部需要清理的位置
grep -rn "legacy_field_permissions" backend/

# Step 2: 把每个引用替换为 Permission.field_filter() / field_writable()
# Step 3: 删除 modules/product/legacy_field_permissions.py
# Step 4: 跑回归测试
```

### 3.4 测试覆盖

| 场景 | 期望 |
|---|---|
| 跟单 GET /api/skus/{id} | 返回完整字段（含 cost_price / purchase_price） |
| 设计师 GET /api/skus/{id} | cost_price / purchase_price 缺失（None / exclude_none） |
| 跟单 PUT /api/skus/{id} body 含 cost_price | 200 + audit_log {cost_price_changed: true} |
| 设计师 PUT /api/skus/{id} body 含 cost_price | 403 FIELD_PERMISSION_DENIED |
| 财务 GET /api/skus/{id} | 含完整字段 |

---

## 4. Pattern P-U02-03 — 数据库原子 upsert

### 4.1 问题
- U06b 导入路径需要 upsert（同 sku_code 重复导入幂等）
- 应用层先查后写有 race condition：并发导入 100 条同 sku_code 会产生重复行 / 唯一约束冲突
- 必须与软删 partial UNIQUE 对齐：`UNIQUE (tenant_id, sku_code) WHERE is_deleted=false`
- audit_log 必须区分新建（`sku.create_via_import`）vs 更新（`sku.update_via_import`）

### 4.2 设计

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert
import sqlalchemy as sa

class SkuService:
    async def upsert_sku(
        self, payload: SkuCreate, user: CurrentUser
    ) -> SkuResponse:
        """U06b 导入路径（数据库原子 upsert）
        
        必须复用同一套校验、权限、审计、唯一约束处理"""
        # 业务规则校验（与 create_sku 完全相同）
        await self._validate_sourcing_price(payload)
        self._check_price_write_permission(payload, user)
        await self._validate_business_rules(payload, base=None)
        
        # 数据库原子 upsert
        stmt = pg_insert(Sku).values(
            tenant_id=current_tenant_id(),
            **payload.model_dump(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku_code"],
            index_where=Sku.is_deleted.is_(False),  # 与 partial UNIQUE 对齐
            set_={
                "color": stmt.excluded.color,
                "size": stmt.excluded.size,
                "cost_price": stmt.excluded.cost_price,
                "purchase_price": stmt.excluded.purchase_price,
                "base_price": stmt.excluded.base_price,
                "sourcing_type": stmt.excluded.sourcing_type,
                "is_active": stmt.excluded.is_active,
                "updated_at": sa.func.now(),
                # 不更新：id / tenant_id / created_at / sku_code / style_id / is_deleted
            },
        ).returning(
            Sku,
            sa.text("(xmax = 0) AS is_inserted"),  # 系统列辅助判断
        )
        
        result = await self.session.execute(stmt)
        row = result.one()
        sku = row[0]
        is_inserted = bool(row.is_inserted)
        
        # 审计（脱敏）
        audit_action = (
            "sku.create_via_import" if is_inserted else "sku.update_via_import"
        )
        await self._log_audit_for_upsert(sku, payload, user, audit_action)
        
        await self.session.commit()
        return self.to_response(sku, user)
```

### 4.3 边界

| 约束 | 说明 |
|---|---|
| 与 create_sku 共用校验 | 业务规则 / 权限 / 字段约束完全一致，禁止旁路 |
| 与 partial UNIQUE 对齐 | `index_where=is_deleted.is_(False)` 严格一致 |
| 不"恢复"软删行 | 调 upsert 仅作用于 active 行；恢复软删 → BR-U02-22 显式接口 |
| 不暴露 HTTP | service 层内部 API，U06b 通过 `from app.modules.product.service import SkuService` 直接调用 |
| audit 区分入口 | 4 个 action 名（create / update / create_via_import / update_via_import）便于追踪 |
| 敏感值脱敏 | cost_price / purchase_price 写 audit 时仅记 `*_changed: true` |

### 4.4 is_inserted 判断（双方案）

#### 4.4.1 主方案：xmax = 0（短期）
```python
.returning(Sku, sa.text("(xmax = 0) AS is_inserted"))
```
- xmax 是 PostgreSQL 系统列：INSERT 后该行 xmax=0，UPDATE 后 xmax>0（旧版本号）
- 简洁、零开销
- 风险：依赖 PostgreSQL MVCC 内部细节
- 状态：**作为短期方案使用**，配套测试用例验证

#### 4.4.2 备选方案：created_at == updated_at（长期业务语义）
```python
.returning(Sku.id, Sku.created_at, Sku.updated_at)
# 在 _on_create / _on_update ORM 钩子中保证：
#   - INSERT 时 created_at == updated_at（精确到毫秒）
#   - UPDATE 时 updated_at = func.now()（与 created_at 不同）
```
- 业务语义清晰
- 风险：依赖 ORM 钩子精确实现，可能在毫秒级精度下出现 created_at == updated_at 的更新场景误判
- 状态：**作为长期演进选项**，U02 不切换，待发现 xmax 风险时改用

#### 4.4.3 测试用例（验证两实现行为一致）
```python
@pytest.mark.parametrize("is_inserted_method", ["xmax", "timestamps"])
async def test_upsert_is_inserted_consistency(is_inserted_method):
    """两种 is_inserted 实现行为一致"""
    # 第一次调用（INSERT）
    response_1 = await service.upsert_sku(payload)
    assert get_audit_action() == "sku.create_via_import"
    
    # 第二次调用（UPDATE）
    payload.color = "蓝"
    response_2 = await service.upsert_sku(payload)
    assert get_audit_action() == "sku.update_via_import"
    assert response_1.id == response_2.id  # 同一行
```

### 4.5 并发测试
```python
@pytest.mark.asyncio
async def test_upsert_concurrent_safe():
    """100 个并发 upsert 同 sku_code → 1 个 INSERT + 99 个 UPDATE"""
    payload = SkuCreate(sku_code="CONCURRENT-001", ...)
    tasks = [service.upsert_sku(payload) for _ in range(100)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 全部成功
    assert all(not isinstance(r, Exception) for r in results)
    # 同一 sku id
    assert len({r.id for r in results}) == 1
    # 1 个 create_via_import + 99 个 update_via_import
    audit_actions = await get_audit_actions(sku_id=results[0].id)
    assert audit_actions.count("sku.create_via_import") == 1
    assert audit_actions.count("sku.update_via_import") == 99
```

---

## 5. Pattern P-U02-04 — 软删 + 引用检查

### 5.1 问题
- BR-U02-20：sku 软删前必须检查是否被 promotion / order 引用
- U02 阶段 promotion / order 表不存在
- 必须为 U04 / U16 留扩展点，且不引入注册器框架（开销大于收益）

### 5.2 设计

```python
class SkuService:
    async def check_references(self, sku_id: UUID) -> dict:
        """检查 sku 是否被其他模块引用。
        U02 阶段：promotion / order 表不存在，返回零引用
        TODO U04: 改为 await self.promotion_repo.count_by_sku(sku_id)
        TODO U16: 改为 await self.order_repo.count_by_sku(sku_id)
        """
        return {"promotion_count": 0, "order_count": 0}
    
    async def soft_delete(self, sku_id: UUID, user: CurrentUser) -> None:
        sku = await self.get_or_404(sku_id)
        refs = await self.check_references(sku_id)
        total_refs = sum(refs.values())
        if total_refs > 0:
            raise ResourceConflictError(
                code="SKU_HAS_REFERENCE",
                message=f"该 SKU 已被引用（{refs}），仅可停用",
                details=refs,
            )
        sku.is_deleted = True
        await self.audit.log(action="sku.delete", resource_id=sku.id)
        await self.session.commit()
```

### 5.3 U04 / U16 启用路径（直接修改 SkuService 文件）

```python
# 演化到 U04 阶段
class SkuService:
    def __init__(self, ..., promotion_repo: PromotionRepository):
        # 注入新仓储
        self.promotion_repo = promotion_repo
    
    async def check_references(self, sku_id: UUID) -> dict:
        return {
            "promotion_count": await self.promotion_repo.count_by_sku(sku_id),
            "order_count": 0,  # TODO U16
        }
```

### 5.4 与 Style 不可直接删的关系（BR-U02-21）
```python
class StyleService:
    async def soft_delete(self, style_id: UUID, user: CurrentUser) -> None:
        active_skus = await self.sku_repo.count_by_style(
            style_id, is_active=True, is_deleted=False
        )
        if active_skus > 0:
            raise ResourceConflictError(
                code="STYLE_HAS_ACTIVE_SKU",
                message=f"款式下还有 {active_skus} 个启用 SKU，请先停用或删除",
            )
        # 软删 style
        ...
```

### 5.5 后续单元应用此模式
- U04 / U16：作为引用源
- U10a 设计单元：增加 design 表对 style 的引用，扩展 `StyleService.check_references()`
- U18 AI 建议：复用模式但用于多对多场景

---

## 6. 监控与 SLO

### 6.1 SLI（服务级指标）

| SLI | 定义 | SLO 目标 |
|---|---|---|
| 列表请求 P95 延迟 | `histogram_quantile(0.95, http_request_duration_seconds_bucket{handler="/api/styles/",method="GET"})` | ≤ 200ms |
| match 请求 P95 延迟 | 同上 handler="/api/styles/match" | ≤ 300ms |
| 写请求 P95 延迟 | 同上 method=~"POST|PUT|DELETE" | ≤ 200ms |
| 错误率 | `rate(http_requests_total{handler=~"/api/styles.*",status=~"5.."}) / rate(http_requests_total{handler=~"/api/styles.*"})` | ≤ 1% |

### 6.2 自定义 Prometheus 指标
- `style_search_results_count` (histogram, buckets: [0, 1, 5, 10, 20]) — 候选数分布
- `sku_upsert_total` (counter, labels: result=created\|updated) — upsert 调用统计

实现位置：`backend/app/core/metrics.py`（U01 已建立）。

### 6.3 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| match 接口 P95 > 1s 持续 5min | Prometheus alertmanager → 企微 | SRE |
| match 零候选率 > 30% 持续 30min | 同上 | 业务 + 后端 |
| 任意 5xx 错误率 > 5% 持续 5min | Sentry → 企微 | 后端 leader |
| upsert audit_action 错误率（理论应 0） | Sentry | 后端 |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| GIN trgm 查询表达式与索引表达式严格一致 | ✅ |
| match 降级语义严格区分系统失败 vs 业务未匹配 | ✅ |
| 字段权限硬编码隔离在 modules/product/legacy_field_permissions.py | ✅ |
| 审计敏感值脱敏（仅 *_changed: true 标记，与威胁模型一致） | ✅ |
| upsert 数据库原子 + partial UNIQUE 对齐 + 不恢复软删 | ✅ |
| upsert audit 区分 create_via_import / update_via_import | ✅ |
| upsert is_inserted 双方案 + 测试覆盖一致性 | ✅ |
| 引用检查 U02 占位 + U04/U16 直接修改路径 | ✅ |
| 全部模式都有监控指标和告警 | ✅ |
