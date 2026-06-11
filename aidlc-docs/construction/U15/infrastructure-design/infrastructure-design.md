# U15 基础设施设计（企微进阶）

> 增量式：复用 U01/U07 全部基础设施（Zeabur 6 服务 + celery-worker/beat + Redis + Sentry + 企微出站）。
> 单元：EP08-S09、S10 + EP10-NFR06。唯一增量 = migration 019（2 表）+ Beat schedule 1 条。

---

## 1. 服务拓扑（无变更）

| 服务 | U15 用途 | 变更 |
|---|---|---|
| backend | GET/PUT /api/wecom/alert-config | 无（挂 alert_router） |
| celery-worker | notify_control_group / check_anomaly_and_alert | 无（default 队列复用） |
| celery-beat | check-anomaly-hourly schedule | +1 条 schedule |
| postgres | wecom_alert_config / wecom_alert_log 2 表 | migration 019 |
| redis | access_token 缓存 + Celery broker/backend | 无（复用 U07） |
| frontend | （前端不在本单元范围） | 无 |

**结论**：无新服务、无新进程、无资源规格变更。

---

## 2. 数据库变更（migration 019）

### 表 1：wecom_alert_config（UNIQUE tenant，单条）
| 列 | 类型 | 约束 |
|---|---|---|
| id / tenant_id / created_at / updated_at | base_cols | TenantScopedModel + FK tenant RESTRICT |
| control_group_webhook | Text | NULL |
| return_rate_threshold | Numeric(5,4) | NOT NULL DEFAULT 0.4000 |
| low_roi_threshold | Numeric(8,4) | NULL |
| low_conversion_threshold | Numeric(5,4) | NULL |
| alert_recipients | JSONB | NOT NULL DEFAULT '[]' |
| is_enabled | Boolean | NOT NULL DEFAULT true |

索引：`uq_wecom_alert_config_tenant` UNIQUE(tenant_id)。RLS 启用。

### 表 2：wecom_alert_log（去重留痕）
| 列 | 类型 | 约束 |
|---|---|---|
| id / tenant_id / created_at / updated_at | base_cols | TenantScopedModel |
| alert_type | String(24) | NOT NULL |
| entity_type | String(24) | NULL |
| entity_ref | String(64) | NULL |
| period_key | String(10) | NOT NULL |
| detail | JSONB | NOT NULL DEFAULT '{}' |
| fired_at | DateTime(tz) | NOT NULL server_default now() |

索引：`uq_wecom_alert_log` UNIQUE(tenant_id, alert_type, entity_ref, period_key) + `idx_wecom_alert_log_fired` (tenant_id, fired_at)。RLS 启用。

### scope seed
- permission：wecom.alert_config:read / wecom.alert_config:write（ON CONFLICT(scope) DO NOTHING）
- role_permission：operations 显式绑 read+write（admin 通配 "*" 已覆盖，无需显式）

### 迁移属性
- revision `"019_u15_create_wecom_alert_tables"`，down_revision `"018_u14_create_report_tables"`。
- 无回填（新表）；down 安全 drop 2 表 + 删 scope。
- 复用 `enable_rls_sql` / `disable_rls_sql`。

---

## 3. 复用基础设施（零新增）

| 维度 | 复用 | 说明 |
|---|---|---|
| 依赖 | httpx / cryptography / Celery / prometheus | U01/U07 已有 |
| 环境变量 | WECOM_API_BASE / WECOM_HTTP_TIMEOUT / WECOM_TOKEN_TTL | U07；webhook 存 DB 非 env |
| Redis | REDIS_URL_CACHE（token 缓存）+ CELERY broker/backend | U07/U01 |
| R2 | 无用量 | — |
| Sentry | 2 项目（prod/staging）Celery 失败 capture | U01 NFR06 |
| 队列 | default（notify_control_group / check_anomaly_and_alert） | U07 |

---

## 4. 外部网络出站

| 调用 | 目标 | 触发 | 安全 |
|---|---|---|---|
| send_group_robot | qyapi.weixin.qq.com/cgi-bin/webhook/send | S09 笔记发布后 | HTTPS；webhook 含 key 不回显不入日志 |
| send_app_message | qyapi.weixin.qq.com/cgi-bin/message/send | S10 每小时监控命中 | HTTPS；access_token 缓存复用 |

复用 U07 已建立的企微出站路径；超时 10s；best-effort 不重试。

---

## 5. Beat schedule（celery-beat 增 1 条）

```python
"check-anomaly-hourly": {
    "task": "app.tasks.wecom_tasks.check_anomaly_and_alert",
    "schedule": crontab(minute=0),       # 每小时整点
    "options": {"queue": "default"},
},
```

错峰：09:00 催发扫描 / 02:00 采集 / 03:00 备份 / 04:00 清理 / 04:30 归档 — 整点小任务（单租户 ≤5s）不冲突。

---

## 6. 部署一致性

- U15 依赖 U07（wecom 表 + 客户端 + 事件总线）+ U14（report 表 + ProductionService）均已部署（V1 早于 U15）。
- PromotionPublished 事件 U04 早已发出（U07 阶段无 handler，通知类 required_handler=False 不报错）；U15 注册 listener 后即生效，无逆向部署风险。
- migration 顺序：019 紧接 018（U14）之后，head 推进到 019。

---

## 7. 本地验证

- Docker PG16:5558 + Redis7:6413 + python:3.12-slim（U15 唯一端口）。
- alembic upgrade head（含 019）；U15 子集（test_anomaly_rules + test_wecom_alert + test_wecom_alert_api）+ 全量回归；覆盖率 ≥70%。

---

## 8. 回滚

- 代码：移除 alert_router 挂载 + on_promotion_published 注册 + Beat schedule（功能下线，无数据破坏）。
- DB：migration 019 down（drop wecom_alert_config / wecom_alert_log + 删 2 scope）；无外键被引用，安全。

---

> spec-format 校验「Missing ## Overview / ## Architecture」为已知假阳性，IGNORE。
