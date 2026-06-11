# U02 业务规则（Business Rules）

> 单元：U02 — 商品 / SKU 基础  
> 形式：BR-U02-NN，按场景分组  
> 与 domain-entities.md 配合阅读

---

## 1. 标识与唯一性规则

### BR-U02-01 — style_code 租户内唯一
- **约束**：`UNIQUE (tenant_id, style_code) WHERE is_deleted = false`
- **触发**：`POST /api/styles/`、`PUT /api/styles/{id}` 修改 style_code
- **错误**：违反时 `409 ResourceConflictError`，message=`"款式编码 W001 已被使用"`
- **验收映射**：EP02-S01.given2

### BR-U02-02 — sku_code 租户内唯一
- 同 BR-U02-01，作用于 sku
- **验收映射**：EP02-S02.given2

### BR-U02-03 — brand_code 租户内唯一
- **约束**：`UNIQUE (tenant_id, brand_code)`
- **触发**：`POST /api/brands/`、`PUT /api/brands/{id}`
- **错误**：`409 ResourceConflictError`

### BR-U02-04 — 软删后业务键释放
- 部分唯一索引含 `WHERE is_deleted = false`，软删后同 code 可重用（业务显示不允许，但底层不阻塞）
- 前端建议在创建前提示 "已存在被停用的同编码款式，是否恢复？"（U02 范围内不强制实现，作为 V1 优化项）

---

## 2. 必填与引用规则

### BR-U02-10 — Style 必填字段
- `style_code` (≤64 字符，仅允许字母/数字/连字符/下划线)、`style_name` (≤255)、`category` (枚举值)
- **错误**：缺失或格式错误返回 `422 ValidationError`，details 列出失败字段

### BR-U02-11 — Sku 必填字段
- `style_id`、`sku_code` (同上 ≤64)、`color` (≤64)、`size` (≤32)
- 价格字段全部可选，但需满足 BR-U02-13

### BR-U02-12 — sku.style_id 引用必须存在
- **校验**：service 层检查 style 存在且未软删
- **错误**：`422 ValidationError`，message=`"style_id 对应的款式不存在或已删除"`
- **验收映射**：EP02-S02.given3

### BR-U02-13 — Sku 价格与 sourcing_type 一致性
- `sourcing_type='自产'` → 至少有 `cost_price`（非 NULL）
- `sourcing_type='外采'` → 至少有 `purchase_price`
- `sourcing_type='混合'` → cost_price 和 purchase_price 都可以为 NULL（外部导入历史数据时容错）
- **错误**：违反时 `422 ValidationError`
- **优先级**：MVP 阶段 SoftCheck（仅 service 层，DB 不加 CHECK），便于历史数据导入

### BR-U02-14 — 价格非负
- `cost_price >= 0`、`purchase_price >= 0`、`base_price >= 0`
- DB 层 CHECK 约束 + service 层校验
- **错误**：`422 ValidationError`，message=`"价格不能为负数"`

### BR-U02-15 — DECIMAL 精度
- 全部价格字段 DECIMAL(10,2)，超出范围（如 99,999,999.99 以上）返回 422
- 客户端如传 3 位小数：自动 ROUND_HALF_EVEN 到 2 位（Pydantic v2 配置）

---

## 3. 删除与生命周期规则

### BR-U02-20 — Sku 软删 + 引用检查
- **检查**：删除 sku 前查询 promotion / order / import_batch 表中是否有 `sku_id` 引用
- **未引用**：允许 `is_deleted=true`（软删）
- **已引用**：返回 `409 ResourceConflictError`，message=`"该 SKU 已被推广记录引用，仅可停用（is_active=false）"`
- **实施**：U02 阶段 promotion/order 表尚不存在；以接口形式预留 `service.check_sku_references()`，U04/U16 启用时填充实际查询

### BR-U02-21 — Style 不可直接删
- 删除 style 前必须满足：所有关联 sku 处于 `is_deleted=true` 或 `is_active=false`
- **错误**：`409 ResourceConflictError`，message=`"款式下还有 N 个启用 SKU，请先停用或删除"`
- **替代操作**：管理员可调用 `POST /api/styles/{id}/disable-with-skus`（一键停用款式 + 所有关联 SKU），但不软删

### BR-U02-22 — 软删可逆
- 软删的款式/SKU 通过 `POST /api/styles/{id}/restore` 恢复（is_deleted=false）
- 仅 admin 角色可操作
- 同 style_code 在恢复时若已被新款占用，返回 `409`，要求先改名

### BR-U02-23 — 停用（is_active=false）
- 停用的款式不出现在 `GET /api/styles/?is_active=true` 列表
- 停用不影响已有的 promotion / order 引用查询（这些只看 sku_id 不看 is_active）

---

## 4. 编辑与审计规则

### BR-U02-30 — 编辑款式触发审计的字段（敏感字段白名单）
- **写 audit_log**：`style_code` 改名（极少发生）
- **不写**：`style_name`, `short_name`, `tags`, `tag_color`, `season`, `gender`, `brand_id`, `category`, `remark`, `main_image_id`, `is_active`
- **目的**：避免日志噪音（设计师/跟单常修改名称、标签）
- **验收映射**：EP02-S03.given2

### BR-U02-31 — 编辑 SKU 触发审计的字段（敏感字段白名单 + 敏感值脱敏）
- **写 audit_log**：`cost_price`, `purchase_price`, `base_price`, `sku_code`, `sourcing_type`
- **不写**：`color`, `size`, `is_active`
- **敏感值脱敏（与 NFR §5.3 + 威胁模型一致）**：
  - `cost_price` / `purchase_price`：仅记录 `cost_price_changed: true` / `purchase_price_changed: true` 标记，**不存真实数值**
  - `base_price`：全角色可见，按正常 before/after 记录
  - 其他字段（sku_code / sourcing_type）：正常 before/after
- **格式示例**：`audit_log.changes = {"sku_code": {"before": "old", "after": "new"}, "cost_price_changed": true, "sourcing_type": {"before": "自产", "after": "外采"}}`
- **验收映射**：EP02-S04.given1（"audit_log 记录新旧值" → 敏感字段记标记，非敏感字段记新旧值）

### BR-U02-32 — 字段未变化不写 audit
- 即使在敏感字段白名单内，若 before == after 也跳过 audit
- 在 service 层做 dict diff（仅记录真正变更的字段）

### BR-U02-33 — `updated_at` 永远更新
- 即使本次更新所有字段值都和原始一致，`updated_at` 仍刷新（ORM 默认行为）
- 这是为了让前端"乐观锁"机制有依据

---

## 5. 权限规则（U02 占位 / U09 落细）

### BR-U02-40 — 模块权限矩阵（MVP）

| 角色 | product:read | product:write | product:delete | brand:* |
|---|---|---|---|---|
| 管理员 | ✅ | ✅ | ✅ | ✅ |
| 跟单 | ✅ | ✅ | ❌ | 仅 read |
| PR 主管 | ✅ | ❌ | ❌ | 仅 read |
| PR | ✅ | ❌ | ❌ | 仅 read |
| 设计师 | ✅ | ❌ | ❌ | 仅 read |
| 设计助理 | ✅ | ❌ | ❌ | 仅 read |
| 版师 | ✅ | ❌ | ❌ | 仅 read |
| 财务 | ✅ | ❌ | ❌ | 仅 read |
| 运营 | ✅ | ❌ | ❌ | 仅 read |

### BR-U02-41 — 价格字段角色硬编码可见性（U09 前的过渡方案）

> **U02 阶段说明**：以下硬编码在 service 层（含 `# TODO U09: 改为字段级权限` 注释）。U09 落地后清理。

| 角色 | cost_price | purchase_price | base_price |
|---|---|---|---|
| 管理员 | ✅ | ✅ | ✅ |
| 跟单 | ✅ | ✅ | ✅ |
| 财务 | ✅ | ✅ | ✅ |
| PR 主管 | ❌ | ❌ | ✅ |
| PR | ❌ | ❌ | ✅ |
| 设计师 | ❌ | ❌ | ✅ |
| 设计助理 | ❌ | ❌ | ✅ |
| 版师 | ❌ | ❌ | ✅ |
| 运营 | ❌ | ❌ | ✅ |

- **实施**：在 `SkuService.to_response()` 内根据 `current_user.role_codes` 判断，将不可见字段置 `None` 后由 Pydantic v2 `exclude_none=True` 序列化排除
- **写权限同理**：仅跟单 / 财务 / 管理员能写 cost_price / purchase_price，其他角色 PUT 请求带这些字段返回 `403 PermissionDeniedError`

### BR-U02-42 — 列表与详情接口字段一致
- `GET /api/skus/` 列表与 `GET /api/skus/{id}` 详情应用同一套字段过滤逻辑（避免列表能看到详情看不到的怪现象）

---

## 6. 款号 ↔ 商品简称双向关联规则（EP02-S06）

### BR-U02-50 — 款号精确反查 short_name / style_name
- **流程**：PR 在前端推广录入页输入 `style_code="W001"`
- **后端**：`GET /api/styles/match?style_code=W001`
- **返回**：`{ style_id, style_code, style_name, short_name }`，前端用 `short_name`（若为 NULL 则 `style_name`）填充"商品简称"输入框
- **未匹配**：返回 `404`，但前端按 BR-U02-52 处理（不阻塞）

### BR-U02-51 — 商品简称模糊反查款号（候选列表）
- **接口**：`GET /api/styles/match?keyword=波点花边长袖`
- **关键约束**：查询表达式必须与 `idx_style_search_trgm` 索引表达式严格一致才能命中 GIN
- **算法**：
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
  LIMIT 20
  ```
  - 拼接表达式与索引表达式严格一致（同 `style_code || ' ' || style_name || ' ' || COALESCE(short_name, '')`）
  - `:exact = keyword || '%'`（前缀匹配 short_name / style_name 优先）
- **返回**：候选数组 + total（封顶 20）
- **索引**：U02 强制建 `idx_style_search_trgm`（GIN trgm + partial WHERE is_deleted=false），PostgreSQL planner 在拼接表达式 ILIKE 上自动选用
- **验证**：`EXPLAIN ANALYZE` 必须显示 `Bitmap Index Scan on idx_style_search_trgm`
- **性能**：5 万行 / 租户 P95 ≤ 300ms（基准测试 `test_match_perf_with_5w_styles`）
- **降级语义**：业务未匹配返回 200 + 空候选（前端继续输入）；系统失败（DB 异常 / 超时）返回 5xx + Sentry，**不返回空候选**（避免误导）
- **未来升级**：单租户 style ≥ 50 万行或 P95 > 500ms 持续 1 周时，迁移到 PostgreSQL 全文搜索（tsvector + GIN）或独立 Elasticsearch（V2+ 范围）

### BR-U02-52 — 款号未匹配的容错策略
- 前端用户输入未匹配的 style_code（如笔误 "W0001"）
- 系统返回 `404` + 友好 message=`"未找到款号 W0001"`
- 前端**不阻塞**继续填表，将原始字符串存到 promotion.style_code（字符串字段），promotion.style_id 留 NULL
- 跟单或 PR 主管后续在 `unmatched promotion list` 页面手动关联或删除
- **U02 范围内**：仅提供 `GET /api/styles/match` 接口，不实施 promotion 端逻辑（U04 实施）
- **验收映射**：EP02-S06.given3

### BR-U02-53 — short_name 选填 + 自动回退
- 创建 / 编辑 Style 时 short_name 选填
- 推广录入填充时：`short_name IS NULL` → 用 `style_name` 填充

---

## 7. 列表查询与分页规则

### BR-U02-60 — Style 列表分页
- **请求参数**：`page` (默认 1)、`page_size` (默认 20，最大 100)
- **排序**：`order_by` 默认 `created_at DESC`，可选 `style_code ASC`、`updated_at DESC`
- **筛选**：
  - `keyword` (ILIKE on style_code/style_name/short_name)
  - `brand_id` (UUID)
  - `category` (枚举值)
  - `season` / `gender`
  - `is_active` (默认 true，传 `?include_inactive=true` 包含停用)
  - `design_status` (枚举值)
- **响应**：`Page<StyleResponse>` 含 `items, total, page, page_size`

### BR-U02-61 — Sku 列表分页
- 同 BR-U02-60 结构
- 额外筛选：`style_id` (UUID)、`color`、`size`、`sourcing_type`
- 关键路径：EP02-S05 `GET /api/skus/by-style/{style_id}` 返回某款式下全部 SKU（不分页，最大 1000 条）

---

## 8. 错误码矩阵

| 场景 | HTTP | code | 说明 |
|---|---|---|---|
| style_code 重复 | 409 | `STYLE_CODE_CONFLICT` | EP02-S01 |
| sku_code 重复 | 409 | `SKU_CODE_CONFLICT` | EP02-S02 |
| brand_code 重复 | 409 | `BRAND_CODE_CONFLICT` | — |
| sku.style_id 不存在 | 422 | `INVALID_STYLE_REFERENCE` | EP02-S02 |
| 价格 < 0 | 422 | `INVALID_PRICE` | — |
| sourcing_type 与价格不一致 | 422 | `SOURCING_PRICE_MISMATCH` | — |
| 价格字段超过 DECIMAL(10,2) | 422 | `PRICE_OUT_OF_RANGE` | — |
| 角色无 product:write 写权限 | 403 | `PERMISSION_DENIED` | EP02-S04 |
| PR 角色尝试修改 cost_price | 403 | `FIELD_PERMISSION_DENIED` | EP02-S04 |
| 款式下还有启用 SKU 时尝试删款式 | 409 | `STYLE_HAS_ACTIVE_SKU` | — |
| SKU 已被 promotion 引用尝试软删 | 409 | `SKU_HAS_REFERENCE` | — |
| 资源不存在 | 404 | `NOT_FOUND` | — |
| 跨租户访问 | 404 | `NOT_FOUND` | RLS 直接 404，不暴露存在性 |

---

## 9. 性能 / 容量预估

| 指标 | 预估 | 说明 |
|---|---|---|
| Style 表行数 | 5K~50K / 租户 | 服装电商款式总量 |
| Sku 表行数 | 50K~500K / 租户 | 平均每款 10 个 SKU |
| Brand 表行数 | 10~100 / 租户 | 自有品牌少 |
| GET /api/styles/ 列表 P95 | ≤ 200ms | 按 (tenant_id, is_active) 索引扫描 |
| GET /api/skus/by-style/{id} | ≤ 100ms | 按 (tenant_id, style_id) 索引 |
| GET /api/styles/match?keyword= | ≤ 300ms | GIN trgm 索引（U02 必建），5 万行可控 |

---

## 10. 与后续单元的契约

| 单元 | 引用方式 | 契约要求 |
|---|---|---|
| U04 (推广) | `promotion.sku_id FK` | sku.id 不变 + 价格字段稳定 |
| U06b (导入) | 按 `(tenant_id, style_code/sku_code)` upsert | code 业务键稳定，DB 层支持 ON CONFLICT |
| U10a (设计) | 扩展 `style.design_status` | DesignStatus 枚举从 2 值扩 7 值需要数据迁移（已预留） |
| U09 (字段级权限) | 改造 service 层 hardcode | 所有 `# TODO U09` 标记位置 |
| U16 (订单) | `order.sku_id FK` + 快照 base_price | sku.id 不变 |
| U17 (套装) | `bundle_item.sku_id FK` | sku.id 不变 |

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 全部规则有验收映射或场景说明 | ✅ |
| 错误码与 U01 错误码体系一致（{code, message, details}） | ✅ |
| 价格规则与 SourcingType 一致 | ✅ |
| 字段级权限有 TODO 标记，便于 U09 清理 | ✅ |
| 删除规则覆盖 promotion / order 引用（U04/U16 启用） | ✅ |
| 模糊匹配性能与表规模匹配 | ✅ |
