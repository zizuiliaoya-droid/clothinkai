# U02 基础设施设计（Infrastructure Design）

> 单元：U02 — 商品 / SKU 基础  
> 范围：U02 特异性增量；通用基础设施全部继承 U01 + shared-infrastructure  
> 阅读：先读 `aidlc-docs/construction/shared-infrastructure.md`，再看本文件

---

## 1. 与 U01 + shared-infrastructure 的关系

### 1.1 100% 继承（不重复定义）

| 类别 | 来源 | U02 复用方式 |
|---|---|---|
| GitHub 仓库 | U01 | 直接 PR |
| Zeabur production / staging 项目 | U01 | 不增加服务 |
| 6 个部署服务（frontend/backend/celery-worker/celery-beat/postgres/redis） | U01 | 配置不变 |
| PostgreSQL 16 实例 | U01 | 通过 alembic 加新表（非新实例） |
| Redis 实例 + DB 0/1/2/3 分片 | U01 | 不使用新键空间 |
| Cloudflare R2 4 个桶 | U01 | 仅在 `public` 桶加 `styles/` 子路径 |
| Sentry 2 项目 | U01 | 加 `module=product` tag |
| 域名 + DNS + TLS | U01 | 不变 |
| 健康检查端点 `/health` + `/ready` | U01 | 不变 |
| GitHub Actions 4 workflows | U01 | 不变（migrate.yml + deploy-staging.yml + deploy-prod.yml + ci.yml） |
| 备份 daily/monthly tar.gz to R2 | U01 | 4 张新表自动纳入 |
| PostgreSQL 角色（clothing_app/bypass/archiver） | U01 | 不新增 |

### 1.2 U02 增量（极小化）

| 类别 | 增量 | 章节 |
|---|---|---|
| PostgreSQL 扩展 | `pg_trgm` 启用 | §2 |
| PostgreSQL 表 | 4 张：brand / style / sku / style_detail_image | §3 |
| PostgreSQL 索引 | 12 个（含 1 个 GIN trgm） | §3 |
| PostgreSQL RLS | 4 条策略 | §3 |
| R2 路径 | `public` 桶 `{tenant_id}/styles/{style_id}/{main\|details}/` | §4 |
| Sentry tag | `module=product` | §5 |
| Prometheus 指标 | `style_search_results_count` Histogram + `sku_upsert_total` Counter | §6 |
| 环境变量 | 无 | — |
| Celery 队列 | 无（U02 无任务） | — |
| 外部 API 集成 | 无 | — |
| 域名 / 证书 | 无 | — |

---

## 2. PostgreSQL 扩展

### 2.1 启用 pg_trgm

**目的**：支持 GIN trgm 索引以满足模糊匹配 P95 ≤ 300ms（5 万行）。

**启用方式**（决策与 Plan Q1=A 一致）：alembic migration 内执行，由 `clothing_bypass` 角色权限完成。

```python
# alembic/versions/004_u02_create_product_tables.py
def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    # 后续创建表 / 索引 / RLS
```

**前置条件**：alembic env.py 通过 `BYPASS_DATABASE_URL` 连接数据库（U01 已配置，env.py 中的 connection 使用 bypass 角色）。`pg_trgm` 是 PostgreSQL 16 内置 trusted 扩展，无需 superuser 也可创建。

**验证**：`SELECT * FROM pg_extension WHERE extname='pg_trgm';` 返回 1 行。

**降级路径（极端场景）**：若 Zeabur 托管策略不允许应用层创建扩展（实测应该可以），SRE 在 migrate.yml workflow 执行前手动 `psql ... -c "CREATE EXTENSION pg_trgm;"`，migration 中的 `IF NOT EXISTS` 保证幂等。

---

## 3. PostgreSQL 表 / 索引 / RLS（增量）

### 3.1 4 张新表

| 表 | 行数预估（单租户） | 关键字段 | RLS |
|---|---|---|---|
| `brand` | ≤ 200 | id, tenant_id, brand_code, brand_name, is_active | ✅ |
| `style` | ≤ 5 万 | id, tenant_id, style_code, style_name, short_name, brand_id, category, ... | ✅ |
| `sku` | ≤ 50 万 | id, tenant_id, style_id, sku_code, color, size, cost_price, ... | ✅ |
| `style_detail_image` | ≤ 50 万 | id, tenant_id, style_id, attachment_id, sort_order | ✅ |

详细字段定义见 `U02/functional-design/domain-entities.md` §3。

### 3.2 12 个索引

| 索引 | 类型 | 列 / 表达式 |
|---|---|---|
| `uq_brand_code` | B-tree UNIQUE | `(tenant_id, brand_code)` |
| `uq_style_code` | B-tree UNIQUE (partial) | `(tenant_id, style_code) WHERE is_deleted=false` |
| `idx_style_tenant_active` | B-tree | `(tenant_id, is_active, is_deleted)` |
| `idx_style_brand` | B-tree | `(tenant_id, brand_id)` |
| `idx_style_category` | B-tree | `(tenant_id, category)` |
| `idx_style_search_trgm` | **GIN trgm** (partial) | 见 §3.3 |
| `uq_sku_code` | B-tree UNIQUE (partial) | `(tenant_id, sku_code) WHERE is_deleted=false` |
| `idx_sku_tenant_style` | B-tree | `(tenant_id, style_id)` |
| `idx_sku_tenant_active` | B-tree | `(tenant_id, is_active, is_deleted)` |
| `idx_sdi_style` | B-tree | `(tenant_id, style_id, sort_order)` |
| `idx_style_owner` | B-tree | `(tenant_id, owner_id)` |
| `idx_sku_style_active` | B-tree | `(tenant_id, style_id, is_active, is_deleted)` |

### 3.3 GIN trgm 索引

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX idx_style_search_trgm ON style
  USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false;
```

**索引大小预估**：5 万 style × ~1KB 表达式 ≈ 50MB（GIN 索引膨胀因子 1.5x）。

### 3.4 4 条 RLS 策略

每张表执行（通过 U01 `core/security/rls.py:rls_template()` 函数生成）：

```sql
-- 启用 RLS
ALTER TABLE brand ENABLE ROW LEVEL SECURITY;
ALTER TABLE brand FORCE ROW LEVEL SECURITY;

-- 策略 1：clothing_app 角色按 tenant_id 过滤
CREATE POLICY tenant_isolation ON brand
  FOR ALL
  TO clothing_app
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- clothing_bypass 角色不受策略限制（系统任务/平台管理员）
-- archiver 角色仅 audit_log 表使用，brand/style/sku 无需配置
```

同样应用到 `style` / `sku` / `style_detail_image`。

### 3.5 表归属于备份框架

U01 备份 task `backup_tasks.py` 通过 `pg_dump` 全库备份，4 张新表自动纳入（无需配置）。

每月备份保留策略：
- daily：30 天
- monthly：12 个月

---

## 4. Cloudflare R2 路径增量

### 4.1 R2 桶使用（决策与 Plan Q2 一致）

**`clothing-erp-public` 桶**（CDN 加速，公开 URL）：
- `{tenant_id}/styles/{style_id}/main/{filename}` — 款式主图
- `{tenant_id}/styles/{style_id}/details/{sort_order}/{filename}` — 款式详情图

**staging 环境**前缀：
- `staging/{tenant_id}/styles/{style_id}/main/{filename}`
- `staging/{tenant_id}/styles/{style_id}/details/{sort_order}/{filename}`

### 4.2 不使用其他桶
- `private`：U02 无敏感附件（cost_price 等价格字段在 DB 字段）
- `credentials`：U12 凭据存储专用
- `backups`：U01 数据库备份专用

### 4.3 上传约束（继承 U01 AttachmentService）

| 约束 | 值 | 来源 |
|---|---|---|
| 文件类型 | jpg / jpeg / png / webp / avif | U01 white-list |
| 单文件大小 | ≤ 5MB | U01 |
| 详情图数量 | ≤ 10 张 / 款式 | U02 业务规则 |
| 详情图总大小 | ≤ 50MB / 款式 | U02 业务规则 |
| 病毒扫描 | 无（V1 引入 ClamAV） | U02 不引入 |

---

## 5. Sentry 增量

### 5.1 复用项目
- 复用 `clothing-erp-backend`（U01 已建）
- 复用 `clothing-erp-frontend`（U01 已建）

### 5.2 增量 tag
- 新增：`module=product`
- 适用于 styleService / skuService / brandService 内捕获的所有 transaction

### 5.3 透明继承
- `environment` (production/staging) — 透明
- `tenant_id` — 透明（TenancyMiddleware 自动注入）
- `actor_type` (user/system) — 透明
- `release` — 通过 GitHub Actions 自动注入版本号

---

## 6. Prometheus 指标增量

### 6.1 自定义指标（实现位置：`backend/app/core/metrics.py`）

```python
# core/metrics.py（U01 已建立，U02 追加 2 个指标）
from prometheus_client import Counter, Histogram

# U02 增量
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

### 6.2 已存在的通用指标（自动覆盖 U02 端点）
- `http_request_duration_seconds` (Histogram) — 通过 `prometheus-fastapi-instrumentator`
- `http_requests_total` (Counter) — 同上
- `db_query_duration_seconds` (Histogram) — U01 已配置（通过 SQLAlchemy event listener）

### 6.3 SLO 阈值（与 NFR Design §6 一致）

| SLI | SLO | 告警 |
|---|---|---|
| `/api/styles/` GET P95 | ≤ 200ms | > 1s 持续 5min |
| `/api/styles/match` GET P95 | ≤ 300ms | > 1s 持续 5min |
| `/api/styles/` 写 P95 | ≤ 200ms | > 2s 持续 5min |
| `/api/styles/*` 5xx 错误率 | ≤ 1% | > 5% 持续 5min |
| match 零候选率 | ≤ 30% | > 30% 持续 30min（业务告警） |

---

## 7. 环境变量

**U02 不新增任何环境变量**。所有依赖项已由 U01 注入：

| 变量 | 来源 | U02 用途 |
|---|---|---|
| `DATABASE_URL` | U01 | SQLAlchemy 业务连接 |
| `BYPASS_DATABASE_URL` | U01 | alembic migration 角色 |
| `REDIS_URL` | U01 | （U02 不直接使用） |
| `R2_ENDPOINT` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | U01 | AttachmentService |
| `R2_PUBLIC_BUCKET` | U01 | styles/ 子路径写入 |
| `SENTRY_DSN_BACKEND` | U01 | Sentry 上报 |
| `JWT_SECRET` | U01 | API 鉴权 |
| `ENVIRONMENT` (production / staging) | U01 | Sentry tag |

业务规则常量（如 `PRICE_VISIBLE_ROLES`）在代码中（`modules/product/legacy_field_permissions.py`），不通过环境变量管理（避免运行时被错改且利于版本管理）。

---

## 8. 与 shared-infrastructure.md 的对齐

### 8.1 §8 R2 路径规约更新

shared-infrastructure §8 已预留：
> | public | `{tenant_id}/styles/{style_id}/{filename}` | U02, U03 |

U02 实际细化为两类（main / details），与原规约兼容（path prefix 一致）。无需修改 shared-infrastructure。

### 8.2 §6 Celery 队列
U02 不启用任何 Celery 队列。shared-infrastructure §6 表保持不变。

### 8.3 §5 数据库迁移规约
U02 alembic migration 命名 `004_u02_create_product_tables.py`，符合 `{NNN}_{unit-id}_{description}.py` 规约。`downgrade()` 必须可执行（drop 顺序：child → parent）。

### 8.4 §9 GitHub Actions
U02 不新增 workflow。`migrate.yml` 自动包含 4 张新表的 alembic migration。

---

## 9. 资源使用预估

| 资源 | U01 baseline | U02 增量 | 总计 |
|---|---|---|---|
| PostgreSQL 行数 | ~1 万行 / 租户（auth/audit） | 5 万 style + 50 万 sku + 200 brand | ~ 60 万行 / 租户 |
| PostgreSQL 索引大小 | ~50MB / 租户 | ~100MB（含 GIN trgm 50MB） | ~ 150MB / 租户 |
| Redis 键空间 | ~1MB（session/blacklist） | 0（U02 不用） | ~ 1MB |
| R2 storage / 租户 | 0（U01 仅备份） | 50 万 × 100KB 平均 ≈ 50GB | ~ 50GB |
| backend Memory | 512MB | +50MB（ORM 模型 + Pydantic schemas） | ~ 600MB（< 1GB 配额） |
| backend CPU | 0.3 vCPU 平均 | +0.05 vCPU（GIN 查询略重） | ~ 0.35 vCPU（< 1 vCPU 配额） |

### 9.1 Zeabur 实例承载
PostgreSQL 16 单实例（默认配置 4GB / 2 vCPU / 100 max_connections）足够支撑 100 个租户的 U01+U02：
- 行数：100 × 60 万 = 6000 万行（PG 单表无压力）
- 索引：100 × 150MB = 15GB（接近 4GB 内存限制 → 大部分索引在磁盘冷读）
- 连接：100 个租户共用连接池，并发 22 < 100

如果租户数突破 100（V2+），评估：
- 升级 Zeabur PG 实例规格
- 按 tenant_id 分库（U18+ 范围）

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| U02 不新增任何 Zeabur 服务 | ✅ |
| 不新增域名 / 证书 / Secrets | ✅ |
| 不新增 Celery 队列 | ✅ |
| 不新增环境变量 | ✅ |
| 仅 PG 增量：1 扩展 + 4 表 + 12 索引 + 4 RLS | ✅ |
| 仅 R2 增量：public 桶 styles/ 子路径 | ✅ |
| 仅 Sentry 增量：module=product tag | ✅ |
| 仅 Prometheus 增量：2 个自定义 metric | ✅ |
| 与 shared-infrastructure 完全对齐 | ✅ |
| 资源预估容量充足 | ✅ |
