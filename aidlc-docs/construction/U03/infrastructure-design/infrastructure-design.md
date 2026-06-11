# U03 基础设施设计（Infrastructure Design）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性增量；通用基础设施全部继承 U01 + U02 + shared-infrastructure  
> 阅读：先读 `aidlc-docs/construction/shared-infrastructure.md`，再看本文件

---

## 1. 与 U01 / U02 + shared-infrastructure 的关系

### 1.1 100% 继承（不重复定义）

| 类别 | 来源 | U03 复用方式 |
|---|---|---|
| GitHub 仓库 | U01 | 直接 PR |
| Zeabur 项目（production / staging） | U01 | 不增加服务 |
| 6 个部署服务（frontend/backend/celery-worker/celery-beat/postgres/redis） | U01 | 配置不变 |
| PostgreSQL 16 实例 | U01 | 通过 alembic 加 1 张新表 |
| pg_trgm 扩展 | U02 已启用 | 直接复用 |
| Redis 实例 + DB 分片 | U01 | 不使用（U03 无缓存键） |
| Cloudflare R2 4 个桶 | U01 | **U03 不使用**（无附件） |
| Sentry 2 项目 | U01 | 加 `module=blogger` tag |
| 域名 + DNS + TLS | U01 | 不变 |
| 健康检查 `/health` + `/ready` | U01 | 不变 |
| GitHub Actions 4 workflows | U01 | 不变 |
| 备份 daily/monthly | U01 | blogger 表自动纳入 |
| PostgreSQL 角色 | U01 | 复用 clothing_app / clothing_bypass |

### 1.2 U03 增量（极小化）

| 类别 | 增量 | 章节 |
|---|---|---|
| PostgreSQL 表 | 1 张：blogger | §2 |
| PostgreSQL 索引 | 10 个（含 2 个 GIN trgm + 2 个 GIN JSONB） | §2 |
| PostgreSQL RLS | 1 条策略 | §2 |
| permission seed | 追加 blogger 权限（U01 seed 已有 blogger.*:*，可能不需要） | §2 |
| Sentry tag | `module=blogger` | §3 |
| Prometheus 指标 | `blogger_search_results_count` Histogram | §4 |
| R2 路径 | 无 | — |
| 环境变量 | 无 | — |
| Celery 队列 | 无（U03 无任务） | — |
| 外部 API 集成 | 无 | — |

---

## 2. PostgreSQL 增量

### 2.1 新增表

| 表 | 行数预估（单租户） | RLS |
|---|---|---|
| `blogger` | ≤ 3000（V2 ≤ 5 万） | ✅ |

### 2.2 10 个索引

| 索引 | 类型 | 列 / 表达式 |
|---|---|---|
| `uq_blogger_xiaohongshu_id` | B-tree UNIQUE (partial) | `(tenant_id, xiaohongshu_id) WHERE is_deleted=false` |
| `idx_blogger_tenant_active` | B-tree | `(tenant_id, is_active, is_deleted)` |
| `idx_blogger_type` | B-tree | `(tenant_id, blogger_type)` |
| `idx_blogger_follower_count` | B-tree | `(tenant_id, follower_count)` |
| `idx_blogger_platform` | B-tree | `(tenant_id, platform)` |
| `idx_blogger_suspected_fake` | B-tree (partial) | `(tenant_id) WHERE is_suspected_fake=true` |
| `idx_blogger_nickname_trgm` | **GIN trgm** (partial) | `(nickname gin_trgm_ops) WHERE is_deleted=false` |
| `idx_blogger_xhs_id_trgm` | **GIN trgm** (partial) | `(xiaohongshu_id gin_trgm_ops) WHERE is_deleted=false` |
| `idx_blogger_category_tags` | **GIN JSONB** | `(category_tags)` |
| `idx_blogger_quality_tags` | **GIN JSONB** | `(quality_tags)` |

**索引大小预估**：3000 行 → 总计 < 5MB / 租户。

### 2.3 RLS 策略

通过 U01 `core/security/rls.py:enable_rls_sql()` 模板生成：

```sql
ALTER TABLE blogger ENABLE ROW LEVEL SECURITY;
ALTER TABLE blogger FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON blogger
    FOR ALL
    TO clothing_app
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    );
```

### 2.4 Permission Seed

U01 `003_u01_seed_initial_data.py` 已 seed `blogger.*:*` 和 `blogger.*:read`，无需追加。

---

## 3. Sentry 增量

### 3.1 复用项目
- 复用 `clothing-erp-backend`（U01 已建）
- 复用 `clothing-erp-frontend`（U01 已建）

### 3.2 增量 tag
- 新增：`module=blogger`
- 适用于 BloggerService 内捕获的所有 transaction

### 3.3 透明继承
- `environment` / `tenant_id` / `actor_type` / `release` 透明继承

---

## 4. Prometheus 指标增量

### 4.1 自定义指标（实现位置：`backend/app/core/metrics.py`）

```python
# core/metrics.py（U02 已建立，U03 追加 1 个指标）

blogger_search_results_count: Histogram = Histogram(
    "blogger_search_results_count",
    "Distribution of blogger search result counts",
    buckets=(0, 1, 5, 20, 100),
)
```

### 4.2 通用指标（自动覆盖 U03 端点）
- `http_request_duration_seconds`（Histogram）
- `http_requests_total`（Counter）

### 4.3 SLO 阈值（与 NFR §3.1 一致）

| SLI | SLO | 告警 |
|---|---|---|
| `/api/bloggers/` GET P95 | ≤ 200ms | > 1s 持续 5min |
| `/api/bloggers/?keyword=` GET P95 | ≤ 150ms | > 1s 持续 5min |
| `/api/bloggers/?category_tag=` GET P95 | ≤ 100ms | > 1s 持续 5min |
| 写请求 P95 | ≤ 150ms | > 2s 持续 5min |
| 5xx 错误率 | ≤ 1% | > 5% 持续 5min |
| 零候选率 | — | > 30% 持续 30min（业务告警） |

---

## 5. 环境变量

**U03 不新增任何环境变量**。所有依赖项（DATABASE_URL / SENTRY_DSN_BACKEND 等）已由 U01 注入。

---

## 6. 与 shared-infrastructure.md 的对齐

### 6.1 §5 数据库迁移规约
U03 alembic migration 命名 `005_u03_create_blogger_table.py`，符合 `{NNN}_{unit-id}_{description}.py` 规约。

### 6.2 §6 Celery 队列
U03 不启用任何 Celery 队列。

### 6.3 §8 R2 路径规约
U03 不使用 R2。

### 6.4 §9 GitHub Actions
U03 不新增 workflow。

---

## 7. 资源使用预估

| 资源 | U02 baseline | U03 增量 | 总计 |
|---|---|---|---|
| PostgreSQL 行数 / 租户 | ~ 60 万行（U01+U02） | ≤ 3000 行 | ~ 60.3 万行 |
| PostgreSQL 索引大小 / 租户 | ~ 150MB（含 GIN trgm 50MB） | < 5MB | ~ 155MB |
| Redis 键空间 | ~ 1MB | 0 | ~ 1MB |
| R2 storage / 租户 | ~ 50GB | 0 | ~ 50GB |
| backend Memory | ~ 600MB | +20MB（ORM + Pydantic） | ~ 620MB |
| backend CPU | ~ 0.35 vCPU | 几乎可忽略 | ~ 0.35 vCPU |

完全在 Zeabur 现有 6 服务承载范围内，无扩容需求。

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| U03 不新增任何 Zeabur 服务 | ✅ |
| 不新增域名 / 证书 / Secrets / 环境变量 | ✅ |
| 不新增 Celery 队列 | ✅ |
| 不使用 R2 | ✅ |
| 仅 PG 增量：1 表 + 10 索引 + 1 RLS | ✅ |
| 复用 U02 启用的 pg_trgm 扩展 | ✅ |
| 仅 Sentry 增量：module=blogger tag | ✅ |
| 仅 Prometheus 增量：1 个自定义 metric | ✅ |
| 与 shared-infrastructure 完全对齐 | ✅ |
| 资源预估容量充足 | ✅ |
