# U14 基础设施设计（Infrastructure Design）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 低基础设施增量：migration 018（2 表）+ report Celery 队列（precompute 占位）

---

## 1. 基础设施增量总览

| 维度 | 是否新增 | 说明 |
|---|---|---|
| Zeabur 服务 | ❌ | 报表聚合在既有 backend 同步查询 |
| 数据库表 | ✅ | migration 018：target_planning + store_daily |
| Celery 队列 | ✅（占位） | report 队列；precompute_report_cache V1 不强制启用 |
| 第三方依赖 | ❌ | 复用 text() SQL + safe_div + resolve_time_range |
| 环境变量 / Secrets | ❌ | 无 |
| R2 桶 / Redis | ❌ | 无 |
| Prometheus 指标 | ✅（应用层） | report_query_duration_seconds，NFR Design 定义 |

---

## 2. migration 018 详情

```sql
CREATE TABLE target_planning (
    id/tenant_id/created_at/updated_at (TenantScopedModel),
    pr_id UUID NOT NULL REFERENCES "user"(id) ON DELETE RESTRICT,
    style_id UUID NOT NULL REFERENCES style(id) ON DELETE CASCADE,
    period_month VARCHAR(7) NOT NULL,
    min_target INTEGER NOT NULL,
    CONSTRAINT ck_target_planning_min CHECK (min_target >= 0)
);
CREATE UNIQUE INDEX uq_target_planning ON target_planning
    (tenant_id, pr_id, style_id, period_month);
CREATE INDEX idx_target_planning_month ON target_planning (tenant_id, period_month);

CREATE TABLE store_daily (
    id/tenant_id/created_at/updated_at (TenantScopedModel),
    date DATE NOT NULL,
    ad_spend_total NUMERIC(12,2) NULL,
    zhitongche_spend NUMERIC(12,2) NULL,
    yinli_spend NUMERIC(12,2) NULL,
    remark TEXT NULL
);
CREATE UNIQUE INDEX uq_store_daily_date ON store_daily (tenant_id, date);

-- RLS：enable_rls_sql(target_planning) + enable_rls_sql(store_daily)
-- scope seed（ON CONFLICT DO NOTHING）:
--   report.work_progress:read / report.production:read / report.target:read / report.store_daily:read
--     → 通配 report.*:read 已覆盖 operations/pr_manager，显式 seed 便于发现
--   report.target:write → pr_manager
--   report.store_daily:write → operations
-- downgrade：DROP 2 表 + DELETE scope
```

- 无锁现有表；无回填。

---

## 3. Celery 队列（report，占位）

| 项 | 配置 |
|---|---|
| report 队列 | task_queues +{"report": {}}；precompute 启用时 celery-worker `-Q ...,report` |
| precompute_report_cache | tasks/report_tasks.py 占位任务；Beat 调度默认注释（V1 实时聚合，不强制预聚合） |

> V1 报表数据量可控，实时聚合满足 SLA（≤800ms）；precompute 留作 V2+ 大数据量优化扩展位。

---

## 4. 性能与索引（复用）

| 报表 | 索引路径 |
|---|---|
| 工作进度 | promotion idx(cooperation_date) + pr_id |
| 爆款约篇 | target_planning idx(tenant,period_month) + promotion 子查询 |
| 店铺数据 | qianniu_daily idx(tenant,date) + store_daily UNIQUE(tenant,date) |
| 投产报表 | qianniu_daily/ad_daily idx(tenant,date) + platform_product idx(tenant,style) |

> 投产跨表用子查询预聚合（应用层优化，见 NFR Design P-U14-03），非基础设施增量。

---

## 5. 部署与回滚

| 项 | 说明 |
|---|---|
| 部署单位 | 代码 + migration 018 +（precompute 启用时）celery-worker -Q report |
| migration | migrate job upgrade head（含 018） |
| 回滚 | alembic downgrade 017（DROP 2 表 + DELETE scope）+ 代码回退 |
| 风险 | 低——新表 + 纯读聚合，无现有表/数据变更 |

---

## 6. 本地 Docker 验证

| 资源 | 端口 |
|---|---|
| PostgreSQL 16 | 5557 |
| Redis 7 | 6412 |

> 接 U13（5556/6411）。

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新服务/依赖/环境变量/R2/Redis | ✅ |
| migration 018 2 表 + RLS + 6 scope | ✅ |
| report 队列 + precompute 占位 | ✅ |
| 复用索引 | ✅ |
| 部署回滚安全 | ✅ |
| 与 NFR Design migration 018 一致 | ✅ |

> 注：本文件 spec-format 诊断（Missing Overview/Architecture）为已知假阳性，IGNORE。
