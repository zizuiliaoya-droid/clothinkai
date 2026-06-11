# U13 基础设施设计（Infrastructure Design）

> 单元：U13 — 自动数据采集 Worker
> 增量：migration 017（5 表）+ crawler 队列 + Beat 任务 + Worker 网络暴露 API + 外部 RPA Worker 旁路

---

## 1. 基础设施增量总览

| 维度 | 是否新增 | 说明 |
|---|---|---|
| Zeabur 后端服务 | ❌ | crawler 任务在既有 celery-worker（+crawler 队列）+ celery-beat（+调度） |
| 外部 RPA Worker | ✅（旁路） | 自建 VM/Docker 主机，HTTPS 调 /api/crawler/*，pull 解耦，不在 Zeabur |
| 数据库表 | ✅ | migration 017：5 表 |
| Celery 队列 | ✅ | crawler（celery-worker 订阅 -Q ...,crawler） |
| Celery Beat | ✅ | schedule_daily_tasks 02:00 |
| 第三方依赖 | ❌ | secrets/hashlib 标准库；复用 U12/U06a/U07 |
| 环境变量 / Secrets | ❌ | worker_token issue API 生成；凭据复用 CREDENTIAL_MASTER_KEY |
| R2 桶 / 路径 | ❌ | 复用 private 桶 imports/（U06a） |
| Sentry | ✅（tag） | +crawler_platform；actor_type=worker（已枚举） |

---

## 2. migration 017 详情

```sql
-- 5 表（TenantScopedModel：id/tenant_id/created_at/updated_at + RLS）
worker_token        : UNIQUE(tenant_id, token_hash) + idx(tenant_id, is_active)
crawler_task        : UNIQUE(tenant_id, platform, credential_id, target_date)
                      + idx(tenant_id, status) + FK credential CASCADE + FK worker_token SET NULL
data_quality_issue  : idx(tenant_id, source, severity) + idx(tenant_id, status)
                      + CHECK severity / CHECK status
qianniu_daily       : UNIQUE(tenant_id, platform_id_snapshot, date)
                      + FK platform_product SET NULL + idx(tenant_id, date)
ad_daily            : UNIQUE(tenant_id, platform_id_snapshot, date)
                      + FK platform_product SET NULL + idx(tenant_id, date)

-- RLS：enable_rls_sql(每表)
-- scope seed（ON CONFLICT DO NOTHING）：
--   crawler.worker:write / crawler.task:read → admin
--   data_quality:read → admin, operations
--   data_quality:write → admin
-- downgrade：DROP 5 表 + DELETE scope
```

- 无锁现有表；无回填。

---

## 3. Celery 队列与调度

| 项 | 配置 |
|---|---|
| crawler 队列 | celery_app task_queues +{"crawler": {}}；celery-worker 启动 `-Q default,backup,wecom,crawler` |
| Beat schedule_daily_tasks | crontab(hour=2, minute=0)，queue=crawler（与 03:00 备份/09:00 催发错峰） |
| autodiscover | +app.tasks.crawler_tasks |

> 部署文档需更新 celery-worker 启动命令的 -Q 队列订阅清单。

---

## 4. Worker API 网络安全（暴露端点）

| 项 | 措施 |
|---|---|
| 鉴权 | X-Worker-Token 头（sha256 校验，独立于用户 JWT） |
| IP 限制 | worker_token.ip_allowlist 应用层强制（空 allowlist 拒绝） |
| 传输 | HTTPS（Zeabur 自动 Let's Encrypt） |
| 边缘增强（建议） | 条件允许时 Zeabur 边缘 IP 白名单 / mTLS（非 U13 强制；应用层 allowlist 已覆盖核心防护） |
| 一次性凭据 | poll 不返明文；exchange 一次性 cred_token + 5min TTL |
| 审计 | poll/exchange/result 全审计 |
| 自动吊销 | 连续 5 次鉴权失败吊销 token + 企微告警 |

> **安全声明**：/api/crawler/* 必须以 worker_token + IP allowlist 鉴权部署，绝不无鉴权暴露。

---

## 5. 外部 RPA Worker 部署（旁路）

| 项 | 说明 |
|---|---|
| 位置 | 自建 Windows VM / Docker 主机（浏览器自动化），不在 Zeabur |
| 通信 | HTTPS pull：poll → exchange → 登录平台采集 → result 上传 |
| 配置 | worker_token（管理员 issue 后配置）+ 注册 IP 到 allowlist |
| 约束 | 明文密码仅内存使用，不落盘/不写日志 |
| 解耦 | 停止 Worker poll 即停采集；后端无需感知 Worker 在线状态 |
| 启动模板 | rpa-worker/README.md（文档） |

---

## 6. 部署与回滚

| 项 | 说明 |
|---|---|
| 部署单位 | 代码 + migration 017 + celery-worker -Q 更新 + celery-beat 调度 |
| migration | 专用 migrate job upgrade head（含 017） |
| 回滚 | downgrade 016（DROP 5 表 + DELETE scope）+ 移除 Beat 调度 + 代码回退 |
| 外部 Worker | 停止 poll 即停（pull 模型，无状态依赖） |
| 风险 | 中——新表 + 网络暴露端点；需确认 worker_token + IP allowlist 已正确配置 |

---

## 7. 本地 Docker 验证

| 资源 | 端口 |
|---|---|
| PostgreSQL 16 | 5556 |
| Redis 7 | 6411 |

> 接 U12（5555/6410）。

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新后端服务（复用 worker/beat）+ 外部 Worker 旁路 | ✅ |
| crawler 队列 + Beat 02:00 | ✅ |
| migration 017 5 表 + RLS + scope | ✅ |
| Worker 网络安全 worker_token+IP+HTTPS | ✅ |
| 复用 private 桶 imports/ | ✅ |
| 与 NFR Design migration 017 一致 | ✅ |

> 注：本文件 spec-format 诊断（Missing Overview/Architecture）为已知假阳性，IGNORE。
