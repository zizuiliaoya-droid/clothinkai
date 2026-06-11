## U04 基础设施设计（Infrastructure Design）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性增量；通用基础设施全部继承 U01-U03 + shared-infrastructure  
> **关键约束**：U04 必须与 U05 同批部署（FB1）

---

## 1. 与 U01-U03 + shared-infrastructure 的关系

### 1.1 100% 继承

| 类别 | 来源 | U04 复用 |
|---|---|---|
| GitHub 仓库 | U01 | ✓ |
| Zeabur production / staging 项目 | U01 | ✓（不增加服务） |
| 6 个部署服务 | U01 | ✓（配置不变） |
| PostgreSQL 16 实例 | U01 | ✓ |
| pg_trgm 扩展 | U02 启用 | ✓ |
| Redis 实例 | U01 | 不使用 |
| R2 4 个桶 | U01 | 不使用 |
| Sentry 2 项目 | U01 | 加 `module=promotion` tag |
| 域名 / DNS / TLS | U01 | 不变 |
| 健康检查 `/health` + `/ready` | U01 | 不变 |
| GitHub Actions 4 workflows | U01 | CI 增加 grep 检查 |
| 备份 daily/monthly | U01 | promotion 表自动纳入 |
| PostgreSQL 角色 | U01 | 复用三个角色 |

### 1.2 U04 增量

| 类别 | 增量 | 章节 |
|---|---|---|
| PostgreSQL 表 | 2 张：promotion / promotion_sequence | §2 |
| PostgreSQL 索引 | 13 个（含 3 GIN trgm + 复合索引支撑 CTE） | §2 |
| PostgreSQL RLS | 1 条策略（promotion；promotion_sequence 也启用） | §2 |
| permission seed | 已 seed `promotion.*:*`（U01 已有），但需追加 `promotion.review:approve`（U04 必备） | §2 |
| Sentry tag | `module=promotion` | §3 |
| Prometheus 指标 | 4 个自定义指标 | §4 |
| **CI 强约束** | grep 检查 finance.listeners 引用 | §6 |
| **部署强约束** | U04+U05 同批 migration + staging smoke test | §6 |

---

## 2. PostgreSQL 增量

### 2.1 新增 2 张表

| 表 | 行数预估（单租户） | RLS |
|---|---|---|
| `promotion` | MVP ≤ 2 万 / V1 ≤ 10 万 / V2+ ≤ 50 万 | ✅ |
| `promotion_sequence` | ≤ 365 行/年 | ✅ |

### 2.2 13 个索引（详见 NFR §3.4）

```sql
-- 业务键唯一
CREATE UNIQUE INDEX uq_promotion_internal_code ON promotion (tenant_id, internal_code);
CREATE UNIQUE INDEX uq_promotion_sequence ON promotion_sequence (tenant_id, date_key);

-- 列表 / 筛选
CREATE INDEX idx_promotion_tenant_active ON promotion (tenant_id, is_active, publish_status);
CREATE INDEX idx_promotion_pr ON promotion (tenant_id, pr_id);
CREATE INDEX idx_promotion_blogger ON promotion (tenant_id, blogger_id, publish_status);
CREATE INDEX idx_promotion_style ON promotion (tenant_id, style_id, publish_status);
CREATE INDEX idx_promotion_cooperation_date ON promotion (tenant_id, cooperation_date DESC);
CREATE INDEX idx_promotion_settlement_status ON promotion (tenant_id, settlement_status);
CREATE INDEX idx_promotion_recall_status ON promotion (tenant_id, recall_status);
CREATE INDEX idx_promotion_publish_dates ON promotion (tenant_id, publish_status, scheduled_publish_date);  -- urge_status CTE

-- GIN trgm（复用 U02 已启用扩展）
CREATE INDEX idx_promotion_internal_code_trgm ON promotion
  USING gin (internal_code gin_trgm_ops) WHERE is_active = true;
CREATE INDEX idx_promotion_style_code_snapshot_trgm ON promotion
  USING gin (style_code_snapshot gin_trgm_ops) WHERE is_active = true;
CREATE INDEX idx_promotion_short_name_trgm ON promotion
  USING gin (style_short_name_snapshot gin_trgm_ops) WHERE is_active = true;
```

### 2.3 RLS 策略

```sql
-- promotion 表
ALTER TABLE promotion ENABLE ROW LEVEL SECURITY;
ALTER TABLE promotion FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON promotion
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

-- promotion_sequence 表（同样启用 RLS）
ALTER TABLE promotion_sequence ENABLE ROW LEVEL SECURITY;
ALTER TABLE promotion_sequence FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON promotion_sequence ...;
```

### 2.4 Permission Seed 增量

U01 `003_u01_seed_initial_data.py` 已 seed `promotion.*:*` 通配符。U04 migration 追加细粒度权限：

```python
# 005_u04_create_promotion_tables.py
op.execute("""
INSERT INTO permission (id, scope, name, category, created_at, updated_at)
VALUES 
    (gen_random_uuid(), 'promotion.review', 'promotion 审核', 'function', NOW(), NOW()),
    (gen_random_uuid(), 'promotion.review:approve', 'promotion 审核-批准', 'function', NOW(), NOW())
ON CONFLICT (scope) DO NOTHING;
""")

# 给 admin / pr_manager 角色绑定 promotion.review:approve 权限
op.execute("""
INSERT INTO role_permission (id, role_id, permission_id)
SELECT gen_random_uuid(), r.id, p.id
FROM role r, permission p
WHERE r.code IN ('admin', 'platform_admin', 'pr_manager')
  AND p.scope = 'promotion.review:approve'
ON CONFLICT (role_id, permission_id) DO NOTHING;
""")
```

---

## 3. Sentry 增量

- 复用 `clothing-erp-backend` 项目
- 新增 tag：`module=promotion`
- 关键事件 `MissingRequiredHandlerError` 即时告警（U05 未部署）

---

## 4. Prometheus 指标增量

详见 NFR Design §6.2，4 个自定义指标：
- `promotion_state_transitions_total` (Counter)
- `settlement_requested_events_total` (Counter)
- `promotion_sequence_lock_duration_seconds` (Histogram)
- `promotion_search_results_count` (Histogram)

实现位置：`backend/app/core/metrics.py`（追加，与 U02/U03 共存）。

---

## 5. 资源使用预估

| 资源 | U03 baseline | U04 增量 | 总计 |
|---|---|---|---|
| PostgreSQL 行数 / 租户 | ~ 60.3 万行（U01+U02+U03） | + 2 万 promotion + 365 sequence | ~ 62.3 万行 |
| PostgreSQL 索引大小 / 租户 | ~ 155MB | + 30MB（含 GIN trgm + 复合索引） | ~ 185MB |
| Redis 键空间 | ~ 1MB | 0 | ~ 1MB |
| R2 storage | ~ 50GB | 0 | ~ 50GB |
| backend Memory | ~ 620MB | + 30MB（ORM + Pydantic + 状态机） | ~ 650MB |
| backend CPU | ~ 0.35 vCPU | + 0.05 vCPU（CTE 计算 + 状态机校验） | ~ 0.40 vCPU |

完全在 Zeabur 现有 6 服务承载范围内。

---

## 6. 部署强约束（FB1 + FB10）

### 6.1 U04 与 U05 同批部署

**多层防护机制**：

| 层 | 防护 | 实现位置 |
|---|---|---|
| 1. Source Control | U04+U05 必须在同 PR 提交 | PR review 检查 |
| 2. Migration | 一次性 alembic upgrade 005_u04 + 006_u05 | migrate.yml 一键升 head |
| 3. CI | grep 检查 finance.listeners 引用 | ci.yml |
| 4. Staging Smoke | end-to-end test 必须通过 | deploy-staging.yml 后置 |
| 5. Startup | register_finance 失败 fail fast | main.py lifespan |

### 6.2 CI 增加 grep 检查（修改 ci.yml）

```yaml
# .github/workflows/ci.yml（追加）
  validate-listeners:
    name: Validate Cross-Unit Listeners Wired
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Check U04 SettlementRequested has U05 listener
        run: |
          if ! grep -rn "from app.modules.finance.listeners import register" backend/app/main.py; then
            echo "ERROR: U04 emits SettlementRequested but U05 listener registration missing"
            echo "       in backend/app/main.py:register_event_listeners()"
            exit 1
          fi
```

### 6.3 Smoke Test 后置（修改 deploy-staging.yml）

```yaml
# .github/workflows/deploy-staging.yml（追加 step）
  e2e-smoke-after-deploy:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - name: Run E2E smoke for U04+U05
        run: |
          pytest backend/tests/e2e/test_review_approve_creates_settlement.py \
            --base-url=https://staging.api.clothinkai.com \
            --maxfail=1
```

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 不新增 Zeabur 服务 / 域名 / Secrets | ✅ |
| 不使用 R2 / 不新增 Celery 队列 | ✅ |
| 仅 PG 增量：2 表 + 13 索引 + 1 RLS + permission seed | ✅ |
| 复用 pg_trgm 扩展（U02） | ✅ |
| 仅 Sentry 增量：module=promotion | ✅ |
| 仅 Prometheus 增量：4 个自定义指标 | ✅ |
| **U04+U05 同批部署强约束（CI/smoke/migration）** | ✅ |
| 与 shared-infrastructure 完全对齐 | ✅ |
| 资源预估容量充足 | ✅ |
