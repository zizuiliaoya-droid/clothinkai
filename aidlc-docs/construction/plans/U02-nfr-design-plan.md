# U02 NFR 设计计划（NFR Design Plan）

> 单元：U02 — 商品 / SKU 基础  
> 阶段：MVP 第 2 个单元  
> 范围：U02 特异性 NFR 设计模式 + 逻辑组件；通用模式继承 `U01/nfr-design/`

---

## 1. 单元上下文

### 1.1 与 U01 NFR Design 的关系

U01 已落地 9 个通用 NFR 设计模式（多租户隔离 / 审计日志 / 状态机 / 附件 / 速率限制 / 错误处理 / 监控 / 备份 / 健康检查）和 26 个逻辑组件（Repository / Service / Domain / Audit / RLS / Crypto / StateMachine / etc.）。

U02 不重新设计这些通用部分，本文件仅描述：
- U02 特异性新模式（GIN trgm 模糊搜索 / 字段权限硬编码过渡 / upsert 共用私有方法 / 软删引用检查）
- U02 引入的新逻辑组件（StyleService / SkuService / BrandService / StyleRepository / SkuRepository）
- 这些新组件如何复用 U01 横切组件

### 1.2 输入 / 输出

| 输入文档 | 用途 |
|---|---|
| `U02/functional-design/domain-entities.md` | 4 实体的 ORM 映射 |
| `U02/functional-design/business-rules.md` | 50+ 条规则的实施位置 |
| `U02/functional-design/business-logic-model.md` | 9 个 UC 流程 |
| `U02/nfr-requirements/nfr-requirements.md` | 性能 SLA、监控、字段权限 |
| `U02/nfr-requirements/tech-stack-decisions.md` | 索引、upsert、迁移策略 |
| `U01/nfr-design/nfr-design-patterns.md` | 9 个通用模式（继承） |
| `U01/nfr-design/logical-components.md` | 26 个通用组件（继承） |

| 输出文档 | 内容 |
|---|---|
| `U02/nfr-design/nfr-design-patterns.md` | U02 新增 4 个 NFR 模式 |
| `U02/nfr-design/logical-components.md` | U02 新增 ~10 个组件 + 复用 U01 组件清单 |

---

## 2. 计划步骤

### Step 1 — 分析 NFR 需求
- [x] 1.1 读取 nfr-requirements.md（10 节）
- [x] 1.2 读取 tech-stack-decisions.md（9 节）
- [x] 1.3 与 U01 NFR Design 对齐复用边界

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U02 特异性 NFR 模式
- [x] 2.2 列出新增逻辑组件
- [x] 2.3 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-design-patterns.md
- [x] 3.1 模式 P-U02-01：GIN trgm 模糊搜索（含降级语义）
- [x] 3.2 模式 P-U02-02：字段权限硬编码过渡（PRICE_VISIBLE_ROLES）
- [x] 3.3 模式 P-U02-03：upsert 共用私有方法（_apply_sku_changes）
- [x] 3.4 模式 P-U02-04：软删 + 引用检查（U04/U16 后向扩展）
- [x] 3.5 与 U01 9 个模式的复用清单
- [x] 3.6 监控点 / SLO 实施

### Step 4 — 生成 logical-components.md
- [x] 4.1 新增组件清单（StyleService / SkuService / BrandService / 仓储 / Schema / API）
- [x] 4.2 组件依赖图（Mermaid）
- [x] 4.3 复用 U01 组件的具体方式（@audit / @require_permission / TenantScopedModel / AttachmentService）
- [x] 4.4 错误处理 / 异常映射 / 字段过滤的组件协作

### Step 5 — 提交完成消息 + 等待审批
- [x] 5.1 展示 "🎨 NFR Design Complete - U02"
- [x] 5.2 等待用户 P1/P2 反馈或批准
- [x] 5.3 批准后写入 audit.md

---

## 3. 澄清问题（请填 [Answer]）

> 每问预填合理默认值，作答即代表确认。

### 3.1 GIN trgm 索引使用方式

**Q1**：模糊搜索的查询语句采用哪种风格？

- [ ] **A. 纯 ILIKE 拼接表达式**（与索引表达式一致，让 planner 命中 GIN）
- [ ] **B. ILIKE 三字段 OR + similarity 排序**（与 tech-stack §3.1 索引表达式不匹配，无法命中）
- [ ] **C. `%>` 操作符** + 分别在 style_code/style_name/short_name 三列各建独立 trgm GIN 索引

[Answer]: A — **查询语句必须与索引表达式严格匹配**，否则 GIN 不命中：
```sql
-- 索引（已在 NFR/tech-stack 定义）
CREATE INDEX idx_style_search_trgm ON style
  USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false;

-- 查询（同表达式才能命中索引）
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
- 同步修正 BR-U02-51 SQL（三字段 OR ILIKE → 拼接表达式 ILIKE）
- 同步修正 domain-entities.md 索引说明
- `EXPLAIN ANALYZE` 验证 `Bitmap Index Scan on idx_style_search_trgm`

**Q2**：如果 5 万行实测 P95 仍超 300ms，回退方案？

[Answer]: 立即触发：
1. **EXPLAIN ANALYZE** 验证查询是否命中 `idx_style_search_trgm`（关键：`Bitmap Index Scan` on 该索引；若是 `Seq Scan` 说明查询表达式与索引表达式未对齐 → 检查 SQL 拼接是否一致）
2. **ANALYZE style;**（PostgreSQL planner 依赖统计信息，autovacuum 可能延迟）
3. **检查候选返回行数**：若 `LIMIT 20` 之前候选过多（如几千行），ORDER BY 排序成本会高 → 加 `WHERE sim > 0.1` 阈值过滤（`pg_trgm.similarity_threshold` 主要影响 `%`/`<%>` 操作符，对 ILIKE 不影响）
4. **检查 GIN 索引膨胀**：`pg_indexes_size('style', 'idx_style_search_trgm')` 异常 → SRE 在维护窗口 `REINDEX INDEX CONCURRENTLY`
5. 仍不达标 → SRE 评估迁移到 PostgreSQL tsvector 全文搜索（V1 范围）；不在 U02 阶段落地全文搜索（避免范围蔓延）

### 3.2 字段权限实施细节

**Q3**：`PRICE_VISIBLE_ROLES` 常量定义位置？

- [ ] **A. modules/product/permissions.py**（模块内）
- [ ] **B. modules/product/legacy_field_permissions.py**（模块内独立文件，明确"过渡方案"）
- [ ] **C. core/security/legacy_field_permissions.py**（核心层）

[Answer]: B — `modules/product/legacy_field_permissions.py`（模块内独立文件）：
- **不放 core**：core 层应该是与领域无关的横切组件；`PRICE_VISIBLE_ROLES` 是 product 域的临时业务规则，放 core 会污染核心层并形成反向依赖
- **不与 permissions.py 合并**：避免 U09 落地后清理时误删正常 permission 配置
- file-level docstring 明确：`"""TEMPORARY: Field-level access control hardcoded for U02. Remove after U09 (字段级权限) is implemented."""`
- U09 阶段 grep `legacy_field_permissions.py` 一个文件即可定位全部清理位置

**Q4**：`@audit("sku.update")` 装饰器与字段过滤如何协作？

[Answer]: 严格执行 NFR §5.3 的"不记录敏感字段值"策略：

- **audit_log 写入**：仅记录敏感字段**变更标记**，不记录真实数值
  ```python
  # service._apply_sku_changes
  changes = self._compute_changes(sku, payload)  # 真实 dict diff
  
  # 转为审计安全格式（脱敏）
  audit_safe_changes = {}
  SENSITIVE_VALUE_FIELDS = {"cost_price", "purchase_price"}
  for field, diff in changes.items():
      if field in SENSITIVE_VALUE_FIELDS:
          audit_safe_changes[f"{field}_changed"] = True   # 仅记标记
      else:
          audit_safe_changes[field] = diff                  # 记 before/after
  
  await self.audit.log(action='sku.update', changes=audit_safe_changes)
  ```
- **效果**：audit_log 表中只看到 `{"cost_price_changed": true, "sku_code": {"before": "...", "after": "..."}}`，无法反推出具体价格
- **base_price 不脱敏**：base_price 全角色可见，按正常 before/after 记录
- **audit 查询接口**（U01 已实现）：进一步按角色过滤展示，财务/管理员看到完整审计记录，普通角色看不到 product 模块审计
- **NFR §5.3 一致性**：与"不记录敏感字段值，仅记录变更标记"策略完全对齐，与"防越权读取，不防 DBA 直查"威胁模型自洽（DBA 看 DB 表能看到当前 cost_price，但 audit_log 表本身不存历史值）

### 3.3 upsert 边界

**Q5**：`upsert_sku` 的 race condition 处理 + 软删 sku_code 冲突语义？

- [ ] **A. 应用层先查后写**（race condition 风险）
- [ ] **B. 数据库层 ON CONFLICT (tenant_id, sku_code) WHERE is_deleted=false DO UPDATE**（partial unique 索引匹配）
- [ ] **C. Redis 分布式锁**

[Answer]: B — **数据库层原子 upsert，与软删唯一约束（partial unique）严格对齐**：

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

stmt = (
    pg_insert(Sku)
    .values(**payload.model_dump())
    .on_conflict_do_update(
        index_elements=["tenant_id", "sku_code"],
        index_where=Sku.is_deleted == False,   # 与 UNIQUE INDEX 的 partial 条件一致
        set_={
            "color": stmt.excluded.color,
            "size": stmt.excluded.size,
            "cost_price": stmt.excluded.cost_price,
            # ...（不更新 id / tenant_id / created_at）
        },
    )
    .returning(Sku, sa.text("(xmax = 0) AS is_inserted"))
)
```

**关键澄清（与软删行为对齐）**：
- partial unique `WHERE is_deleted=false`：软删的旧行不参与唯一约束，sku_code "释放"
- 调用 upsert(sku_code="W001-红-M") 场景：
  1. 不存在同 sku_code 行 → INSERT 新行
  2. 存在 `is_deleted=false` 同 sku_code 行 → UPDATE 该行（普通更新语义）
  3. 仅存在 `is_deleted=true` 同 sku_code 软删行 → INSERT 新行（**新建一个 SKU，不"恢复"软删行**），新行 id 与软删行不同
  4. 同时存在 active + 软删行（理论不应出现，因 partial unique 保证 active 唯一）→ 理论场景，归于 (2)
- **业务语义**：upsert 不"恢复"软删行；恢复软删行需走 `POST /api/skus/{id}/restore`（管理员显式操作，BR-U02-22）

**audit action 区分**：通过 `RETURNING (xmax = 0) AS is_inserted` 判断，详见 Q6

**Q6**：`upsert_sku` 的 audit action 名称如何区分新建 vs 更新？

[Answer]: 

**首选方案：CTE + RETURNING 显式判断**（推荐为长期业务语义）

```python
# 用 CTE 在同一语句获取 "是否新建" 的语义清晰返回
stmt = (
    pg_insert(Sku)
    .values(**payload.model_dump())
    .on_conflict_do_update(
        index_elements=["tenant_id", "sku_code"],
        index_where=Sku.is_deleted == False,
        set_={...}
    )
    # RETURNING xmax 是 PostgreSQL 实现细节；显式语义如下：
    .returning(
        Sku.id,
        Sku.created_at,
        Sku.updated_at,
        # 显式比较时间戳更稳：created_at == updated_at 表示首次插入
        # （注意应用层钩子要保证两者在 INSERT 时严格相同，UPDATE 时 updated_at 必变）
    )
)
result = await self.session.execute(stmt)
row = result.one()
is_inserted = row.created_at == row.updated_at
audit_action = "sku.create_via_import" if is_inserted else "sku.update_via_import"
```

**可选实现：xmax=0 系统列**（PostgreSQL 实现细节，仅作短期）
```python
.returning(Sku, sa.text("(xmax = 0) AS is_inserted"))
```
- xmax=0 在 INSERT 路径返回 true，UPDATE 返回该行原 xmax（>0）
- 简洁但依赖 PostgreSQL 内部 MVCC 字段，不属于稳定业务语义
- 可作为短期 fallback，**测试用例必须断言两种实现行为一致**

**测试覆盖**：
- 新建 sku → `audit_log.action='sku.create_via_import'`
- 重复 upsert → `audit_log.action='sku.update_via_import'`
- 并发 100 次 upsert 同 sku_code → 1 个 INSERT + 99 个 UPDATE，audit_log 数量一致

### 3.4 软删引用检查的扩展性

**Q7**：`SkuService.check_references()` 的实施风格？

- [ ] **A. 硬编码查询 + TODO 注释**（U04/U16 启用时直接修改）
- [ ] **B. 注册式接口**（其他模块向 SkuService 注册自己的引用检查器）

[Answer]: A — U02 阶段最简硬编码 + TODO 注释；U04/U16 后再评估是否升级为 B（注册式）：
```python
class SkuService:
    async def check_references(self, sku_id: UUID) -> dict:
        """U02 阶段：promotion/order 表不存在，返回 {refs: 0}
        TODO U04: 改为 await self.promotion_repo.count_by_sku(sku_id)
        TODO U16: 改为 await self.order_repo.count_by_sku(sku_id)"""
        return {"promotion_count": 0, "order_count": 0}
```
- **U02 选 A 理由**：注册器框架开销大于收益，仅 2 个引用源（promotion / order）不值得引入插件机制
- **演化路径**：U04 阶段如发现注册式更清晰（如 V2+ 累积到 5+ 引用源），届时升级为 B；当前选 A 不堵死该路径

### 3.5 性能基准测试

**Q8**：性能基准测试（5 万 style 模糊匹配 P95 ≤ 300ms）的执行频率？

- [ ] **A. 每次 PR 都跑**（CI 强制）
- [ ] **B. nightly 跑**（不阻塞 PR，发现退化告警）
- [ ] **C. 仅 release 前手动跑**

[Answer]: B — nightly 跑（GitHub Actions schedule），失败发 Slack/邮件告警，不阻塞 PR；性能测试需要 mock 5 万行数据，CI 时间过长不适合 PR 阻塞；release 前 SRE 手动复跑确认；测试用例标记 `@pytest.mark.performance`，PR 默认不跑

### 3.6 监控指标命名规范

**Q9**：U02 在 Prometheus 指标的 labeling 方式？

[Answer]: 复用 U01 已部署的 `prometheus-fastapi-instrumentator`，自动暴露 `http_request_duration_seconds`，按 handler 路径自动分桶；额外添加 custom metric：
- `style_search_results_count` (histogram) — 模糊匹配返回的候选数分布（用于发现"零候选"率高的租户）
- `sku_upsert_total{result="created|updated"}` (counter) — upsert 调用统计

放在 `app/core/metrics.py`（U01 创建的目录），U02 模块仅 import 不创建新文件。

### 3.7 与 U01 横切组件的复用

**Q10**：U02 的 Repository / Service / Domain / API 层结构是否完全沿用 U01 的 4 层架构？

[Answer]: 是 — 完全沿用 4 层：
- `models.py`（ORM）
- `schemas.py`（Pydantic）
- `repository.py`（DB 访问，仅 SELECT/INSERT/UPDATE/DELETE）
- `domain.py`（业务规则验证 + 状态机；U02 暂无状态机但保留文件）
- `service.py`（编排：调 domain 校验 → 调 repository 持久化 → @audit → return）
- `permissions.py`（声明本模块的 permission 字符串常量）
- `api.py`（FastAPI Router + 依赖注入）
- `exceptions.py`（本模块业务异常子类，继承 U01 base exceptions）
- `deps.py`（service 工厂依赖注入）

**Q11**：BrandService 是否需要独立目录？

- [ ] **A. modules/brand/**（独立模块）
- [ ] **B. modules/product/brand_service.py**（合并到 product 目录）

[Answer]: B — Brand 是 Style 的依赖字典，逻辑简单（仅 CRUD），合并到 modules/product/ 目录下作为子文件，避免目录过多；只新增 brand_service.py / brand_repository.py / brand_schemas.py 三个文件，permissions / api 与 product 模块共用

### 3.8 错误处理装饰器

**Q12**：FieldPermissionDenied 异常是否新建？

[Answer]: 是 — 在 `core/exceptions.py` (U01 已存在) 新增：
```python
class FieldPermissionDenied(PermissionDeniedError):
    code = "FIELD_PERMISSION_DENIED"
    def __init__(self, field: str):
        super().__init__(f"无权写入字段: {field}", details={"field": field})
```
错误处理器（U01 register_error_handlers）自动映射到 403 + JSON 响应；U02 在 service 层抛出，无需修改 errors.py

---

## 4. 决策摘要（在用户填答后由 AI 整理）

> 此处在用户回复"继续"之后，AI 总结所有 [Answer] 形成最终设计清单，作为 nfr-design-patterns.md / logical-components.md 的输入。
