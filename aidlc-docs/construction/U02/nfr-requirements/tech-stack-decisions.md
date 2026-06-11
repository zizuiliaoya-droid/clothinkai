# U02 技术栈决策（Tech Stack Decisions）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 特异性技术选型；通用技术栈见 `aidlc-docs/construction/U01/nfr-requirements/tech-stack-decisions.md`

---

## 1. 与 U01 技术栈的关系

### 1.1 完全继承（不重新选型）

| 类别 | U01 已选 | 版本 | U02 沿用说明 |
|---|---|---|---|
| Web Framework | FastAPI | 0.115.x | 不变 |
| ORM | SQLAlchemy | 2.0.x async | 不变 |
| DB Driver | asyncpg | 0.30.x | 不变 |
| 数据库 | PostgreSQL | 16.x | 沿用 + U02 启用 pg_trgm 扩展 |
| Schema 验证 | Pydantic | 2.x（strict） | 不变 |
| 缓存 | Redis | 7.x | 不变（U02 不新增缓存键空间） |
| 任务队列 | Celery | 5.x | 不变（U02 无新任务） |
| 类型检查 | mypy | strict | 不变 |
| Linter | ruff | latest（S+ASYNC+UP 规则） | 不变 |
| 测试 | pytest + pytest-asyncio | latest | 不变 |
| 监控 | Prometheus + Sentry + Loki | 同 U01 | 不变 |
| Migration | Alembic | latest | 不变 |

### 1.2 U02 增量决策

| 决策项 | 选项 | 理由 |
|---|---|---|
| 模糊匹配实现 | **PostgreSQL pg_trgm GIN** | §2 |
| 索引策略 | **B-tree + GIN trgm 混合** | §3 |
| 软删 vs 硬删 | **软删 + 引用检查** | §4 |
| 字段级权限实施 | **service 层硬编码 + TODO U09** | §5 |
| upsert 策略 | **service 层 _apply_sku_changes 共用** | §6 |
| 数据迁移 | **migration job 专用通道** | §7 |

---

## 2. 模糊匹配实现：PostgreSQL pg_trgm GIN

### 2.1 候选方案对比

| 方案 | 性能（5 万行 P95） | 复杂度 | 运维成本 | 选用 |
|---|---|---|---|---|
| 纯 ILIKE（无索引） | ~800ms | 极低 | 0 | ❌ |
| pg_trgm + GIN | ≤ 200ms | 低 | 极低（DB 内置） | ✅ |
| PostgreSQL 全文搜索（tsvector） | ~150ms | 中 | 低 | 备选（V2+） |
| 独立 Elasticsearch | ~50ms | 高 | 高（独立服务） | 排除（V2+ 评估） |

### 2.2 选用 pg_trgm 的理由

1. **PostgreSQL 内置扩展**：无需额外服务，无运维成本
2. **支持 ILIKE `%keyword%` 索引**：planner 自动选用（无需改 SQL 形式）
3. **支持 similarity() 相关性排序**：候选列表可按匹配度优先
4. **5 万行场景 P95 ≤ 200ms**：覆盖 U02 SLA 要求
5. **升级路径平滑**：单租户突破 50 万行时切到 tsvector 或 ES，service 层接口不变

### 2.3 索引定义
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_style_search_trgm ON style
  USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false;
```

### 2.4 SQLAlchemy 定义
```python
# modules/product/models.py
from sqlalchemy import Index, text

class Style(TenantScopedModel):
    __tablename__ = "style"
    # ... 字段定义
    
    __table_args__ = (
        Index(
            "idx_style_search_trgm",
            text(
                "(style_code || ' ' || style_name || ' ' || COALESCE(short_name, '')) "
                "gin_trgm_ops"
            ),
            postgresql_using="gin",
            postgresql_where=text("is_deleted = false"),
        ),
        # ... 其他索引
    )
```

---

## 3. 索引策略

### 3.1 完整索引清单

| 索引名 | 类型 | 列 | 用途 | 必建 |
|---|---|---|---|---|
| `idx_style_tenant_active` | B-tree | (tenant_id, is_active, is_deleted) | 列表过滤 | ✅ |
| `idx_style_brand` | B-tree | (tenant_id, brand_id) | 按品牌筛选 | ✅ |
| `idx_style_category` | B-tree | (tenant_id, category) | 按大类筛选 | ✅ |
| `idx_style_search_trgm` | **GIN trgm** | 复合表达式（含 trgm 字段） | 模糊搜索 | ✅ |
| `uq_style_code` | B-tree UNIQUE | (tenant_id, style_code) WHERE is_deleted=false | 业务键唯一 | ✅ |
| `idx_sku_tenant_style` | B-tree | (tenant_id, style_id) | 按款式查 SKU | ✅ |
| `idx_sku_tenant_active` | B-tree | (tenant_id, is_active, is_deleted) | 列表过滤 | ✅ |
| `uq_sku_code` | B-tree UNIQUE | (tenant_id, sku_code) WHERE is_deleted=false | 业务键唯一 | ✅ |
| `uq_brand_code` | B-tree UNIQUE | (tenant_id, brand_code) | 业务键唯一 | ✅ |
| `idx_sdi_style` | B-tree | (tenant_id, style_id, sort_order) | 详情图按顺序读取 | ✅ |

### 3.2 索引大小预估（5 万 style + 50 万 sku）

| 索引 | 估算大小 | 内存压力 |
|---|---|---|
| idx_style_search_trgm | ~50MB | 中（GIN 索引较大） |
| 其余 B-tree 索引 | < 100MB 总计 | 低 |
| 合计 | ~150MB | 可接受（Zeabur PG 实例 16GB） |

### 3.3 索引维护
- **VACUUM**：依赖 PostgreSQL autovacuum（无需手动）
- **重建**：U02 阶段不需要；若未来发现 GIN 膨胀，由 SRE 在维护窗口 `REINDEX INDEX CONCURRENTLY` 重建

---

## 4. 软删 vs 硬删

### 4.1 决策：软删 + 引用检查

| 表 | 删除策略 | 标记字段 | 释放业务键 |
|---|---|---|---|
| `style` | 软删 + 引用检查 | `is_deleted` | UNIQUE (tenant_id, style_code) WHERE is_deleted=false |
| `sku` | 软删 + 引用检查 | `is_deleted` | UNIQUE (tenant_id, sku_code) WHERE is_deleted=false |
| `brand` | 软删（无引用检查） | `is_active=false` | 无（brand_code 永久占用） |
| `style_detail_image` | 硬删 | — | — |

### 4.2 引用检查接口
```python
# modules/product/service.py
class SkuService:
    async def check_references(self, sku_id: UUID) -> dict:
        """U02 阶段：promotion/order 表不存在，返回 {refs: 0}
        U04 启用：查 promotion 表
        U16 启用：查 order 表"""
        return {"promotion_count": 0, "order_count": 0}
    
    async def soft_delete(self, sku_id: UUID, user: CurrentUser) -> None:
        refs = await self.check_references(sku_id)
        if sum(refs.values()) > 0:
            raise ResourceConflictError(
                code="SKU_HAS_REFERENCE",
                message=f"该 SKU 已被 {refs['promotion_count']} 个推广记录引用"
            )
        # 软删
        sku.is_deleted = True
        await self.audit.log_delete(...)
```

### 4.3 备份保留
- 软删数据保留在表中，不影响 PostgreSQL 物理备份
- audit_log 通过 ORM 钩子永久保留删除事件
- 完全删除（DBA 介入）需要 SRE + 业务方双重审批，U02 范围不实施

---

## 5. 字段级权限实施（U02 过渡）

### 5.1 决策：service 层硬编码 + TODO U09（位于 modules/product/legacy_field_permissions.py）

```python
# modules/product/legacy_field_permissions.py
"""TEMPORARY: Field-level access control hardcoded for U02.
Remove after U09 (字段级权限) is implemented.
搜索 'legacy_field_permissions' 一词可定位全部清理点。"""

PRICE_VISIBLE_ROLES = frozenset(["admin", "follower", "finance"])  # 跟单 / 财务 / 管理员
```

```python
# modules/product/service.py
from app.modules.product.legacy_field_permissions import PRICE_VISIBLE_ROLES

class SkuService:
    def to_response(self, sku: Sku, user: CurrentUser) -> SkuResponse:
        """U02 字段过滤"""
        # TODO U09: 改为基于 Permission.field_filter() 的字段级权限
        data = {
            "id": sku.id,
            "style_id": sku.style_id,
            "sku_code": sku.sku_code,
            "color": sku.color,
            "size": sku.size,
            "base_price": sku.base_price,
            "sourcing_type": sku.sourcing_type,
            "is_active": sku.is_active,
            # ...
        }
        if user.has_any_role(PRICE_VISIBLE_ROLES):  # TODO U09
            data["cost_price"] = sku.cost_price
            data["purchase_price"] = sku.purchase_price
        return SkuResponse(**data)
```

### 5.2 写权限检查
```python
# modules/product/service.py
from app.modules.product.legacy_field_permissions import PRICE_VISIBLE_ROLES
from app.core.exceptions import FieldPermissionDenied

def _check_price_write_permission(self, payload: SkuUpdate, user: CurrentUser) -> None:
    """U02 字段写权限"""
    # TODO U09: 改为基于 Permission.field_writable() 检查
    if not user.has_any_role(PRICE_VISIBLE_ROLES):
        if "cost_price" in payload.model_fields_set:
            raise FieldPermissionDenied(field="cost_price")
        if "purchase_price" in payload.model_fields_set:
            raise FieldPermissionDenied(field="purchase_price")
```

### 5.3 U09 切换路径
1. U09 完成 `Permission.field_filter()` / `Permission.field_writable()`
2. grep `legacy_field_permissions` 找全部硬编码引用位置（一个文件 + 引用方）
3. 逐个替换为通用 API 调用
4. 删除 `modules/product/legacy_field_permissions.py` 文件
5. 回归测试通过后合并

---

## 6. upsert 策略（U02 预留 / U06b 使用）

### 6.1 决策：三方法共用 _apply_sku_changes 私有方法 + 数据库原子 upsert

```python
# modules/product/service.py
from sqlalchemy.dialects.postgresql import insert as pg_insert
import sqlalchemy as sa

class SkuService:
    async def create_sku(
        self, payload: SkuCreate, user: CurrentUser
    ) -> SkuResponse:
        """普通创建路径"""
        await self._validate_sourcing_price(payload)
        self._check_price_write_permission(payload, user)
        sku = Sku()
        await self._apply_sku_changes(
            sku, payload, user, audit_action="sku.create", is_new=True
        )
        return self.to_response(sku, user)
    
    async def update_sku(
        self, sku_id: UUID, payload: SkuUpdate, user: CurrentUser
    ) -> SkuResponse:
        """普通编辑路径"""
        sku = await self.get_or_404(sku_id)
        await self._validate_sourcing_price(payload, base=sku)
        self._check_price_write_permission(payload, user)
        await self._apply_sku_changes(
            sku, payload, user, audit_action="sku.update", is_new=False
        )
        return self.to_response(sku, user)
    
    async def upsert_sku(
        self, payload: SkuCreate, user: CurrentUser
    ) -> SkuResponse:
        """U06b 导入路径（数据库原子 upsert，无 race condition）
        必须复用同一套校验、权限、审计、唯一约束处理"""
        await self._validate_sourcing_price(payload)
        self._check_price_write_permission(payload, user)
        
        # 数据库原子 upsert，与 partial unique 严格对齐
        stmt = pg_insert(Sku).values(
            **payload.model_dump(),
            tenant_id=current_tenant_id(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "sku_code"],
            index_where=Sku.is_deleted.is_(False),  # 与 partial UNIQUE 一致
            set_={
                "color": stmt.excluded.color,
                "size": stmt.excluded.size,
                "cost_price": stmt.excluded.cost_price,
                "purchase_price": stmt.excluded.purchase_price,
                "base_price": stmt.excluded.base_price,
                "sourcing_type": stmt.excluded.sourcing_type,
                "is_active": stmt.excluded.is_active,
                "updated_at": sa.func.now(),
            },
        ).returning(Sku, sa.text("(xmax = 0) AS is_inserted"))
        
        result = await self.session.execute(stmt)
        row = result.one()
        sku = row[0]
        is_inserted = bool(row.is_inserted)
        
        # 审计区分入口
        audit_action = (
            "sku.create_via_import" if is_inserted else "sku.update_via_import"
        )
        await self._log_audit_for_upsert(sku, payload, user, audit_action)
        
        await self.session.commit()
        return self.to_response(sku, user)
    
    async def _apply_sku_changes(
        self,
        sku: Sku,
        payload: SkuUpsert,
        user: CurrentUser,
        *,
        audit_action: str,
        is_new: bool,
    ) -> None:
        """共用：业务规则 + ORM + 审计（脱敏）
        所有 create/update 路径都走这里；upsert 因 SQL 原子操作单独处理审计"""
        # 业务规则 (BR-U02-12 检查 style_id, BR-U02-13 检查 sourcing_type 一致性)
        await self._validate_business_rules(payload, base=None if is_new else sku)
        
        # 应用变更（dict diff）
        changes = self._compute_changes(sku, payload)
        for field, new_value in changes.items():
            setattr(sku, field, new_value)
        
        if is_new:
            self.session.add(sku)
        await self.session.flush()
        
        # 审计仅敏感字段，敏感值脱敏（与 NFR §5.3 对齐）
        SENSITIVE_FIELDS = {"sku_code", "cost_price", "purchase_price",
                            "base_price", "sourcing_type"}
        SENSITIVE_VALUE_FIELDS = {"cost_price", "purchase_price"}
        audit_safe_changes = {}
        for field, diff in changes.items():
            if field not in SENSITIVE_FIELDS:
                continue
            if field in SENSITIVE_VALUE_FIELDS:
                audit_safe_changes[f"{field}_changed"] = True
            else:
                audit_safe_changes[field] = diff  # before/after
        
        if audit_safe_changes:
            await self.audit.log(
                action=audit_action,
                resource="sku",
                resource_id=sku.id,
                changes=audit_safe_changes,
            )
        
        await self.session.commit()
```

### 6.2 边界约束（强制）
1. **三方法共享底层（create/update 走 _apply_sku_changes；upsert 因 SQL 原子操作单独处理审计但用同一脱敏策略）**：禁止旁路
2. **upsert 不暴露 HTTP**：仅 service 层内部 API，U06b 通过 `from app.modules.product.service import SkuService` 直接调用
3. **审计区分入口 + 敏感值脱敏**：`sku.create / sku.update / sku.create_via_import / sku.update_via_import` 4 个 action 名；cost_price / purchase_price 仅记录 `*_changed: true` 标记，不存历史值
4. **upsert 与 partial UNIQUE 对齐**：`on_conflict_do_update(index_elements=[...], index_where=Sku.is_deleted.is_(False))` 严格匹配 `WHERE is_deleted=false` 部分索引
5. **不"恢复"软删行**：upsert 仅作用于 active 行；恢复软删 SKU 走 `POST /api/skus/{id}/restore` 显式接口（管理员）
6. **测试覆盖**：
   - FB7 集成测试：重复调用 upsert_sku → 第一次创建（is_inserted=True） / 第二次更新（is_inserted=False） / 验证 audit 区分 action / 验证字段过滤 / 验证脱敏
   - 并发测试：100 个并发 upsert 同 sku_code → 1 个 INSERT + 99 个 UPDATE，无重复行

---

## 7. 数据迁移通道

### 7.1 决策：专用 migration job

执行流程（与 U01 Q11=B 决策完全一致）：

```
1. PR 合并到 main 分支
2. 手动触发 .github/workflows/migrate.yml (workflow_dispatch)
3. job 执行：
   - 拉取最新 alembic/versions/
   - alembic upgrade head（先 staging 再 production）
   - 失败时 alembic downgrade -1
4. migrate 成功 → 触发 deploy-prod.yml 部署应用
5. migrate 失败 → 应用层不部署
```

### 7.2 U02 单次 migration 内容

文件：`backend/alembic/versions/004_u02_create_product_tables.py`

```python
def upgrade():
    # 1. 启用 pg_trgm 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    
    # 2. 创建 brand 表
    op.create_table("brand", ...)
    op.execute(rls_template("brand"))
    
    # 3. 创建 style 表
    op.create_table("style", ...)
    op.create_index("idx_style_tenant_active", ...)
    op.create_index("idx_style_brand", ...)
    op.create_index("idx_style_category", ...)
    op.create_index(
        "idx_style_search_trgm",
        sa.text("(style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))"),
        postgresql_using="gin",
        postgresql_ops={"...": "gin_trgm_ops"},
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.execute(rls_template("style"))
    
    # 4. 创建 sku 表
    op.create_table("sku", ...)
    op.create_index("idx_sku_tenant_style", ...)
    op.create_index("idx_sku_tenant_active", ...)
    op.execute(rls_template("sku"))
    
    # 5. 创建 style_detail_image 表
    op.create_table("style_detail_image", ...)
    op.execute(rls_template("style_detail_image"))


def downgrade():
    op.drop_table("style_detail_image")
    op.drop_table("sku")
    op.drop_table("style")
    op.drop_table("brand")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
```

### 7.3 数据初始化
- U02 不预置示例数据
- 租户首次使用时为 0 数据起步

---

## 8. 依赖项变更

### 8.1 Python 依赖
**无新增**。U01 已涵盖：
- `fastapi==0.115.x`
- `sqlalchemy==2.0.x`
- `asyncpg==0.30.x`
- `pydantic==2.x`
- `alembic`（最新）

### 8.2 PostgreSQL 扩展
**新增 1 个**：
- `pg_trgm`：模糊搜索 trgm GIN 索引（PostgreSQL 16 内置，无需额外安装）

启用位置：`alembic/versions/004_u02_create_product_tables.py` 第一步。

### 8.3 其他
**无**。

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 所有技术选项与 U01 兼容（FastAPI / SQLAlchemy / asyncpg） | ✅ |
| pg_trgm 扩展在 PostgreSQL 16 内置 | ✅ |
| 索引策略覆盖所有性能 SLA 路径 | ✅ |
| 字段级权限实施有 TODO U09 切换路径 | ✅ |
| upsert 边界约束完全文档化 | ✅ |
| migration 通过专用 job（与 U01 一致） | ✅ |
| 无新依赖项 / 无版本冲突 | ✅ |
