# U12 基础设施设计（Infrastructure Design）

> 单元：U12 — 平台凭据 + 采集失败告警
> 零基础设施增量：唯一变更 = migration 016（credential 表）

---

## 1. 基础设施增量总览

| 维度 | 是否新增 | 说明 |
|---|---|---|
| Zeabur 服务 | ❌ | 复用 backend（同步 CRUD + 解密 + 失败告警） |
| 数据库表 | ✅ | migration 016：credential 表（唯一增量） |
| 第三方依赖 | ❌ | 复用 cryptography（U07）+ NotificationService（U07）+ AuditService（U01） |
| 环境变量 / Secrets | ❌ | 复用 CREDENTIAL_MASTER_KEY（U01 注入，U07 已用） |
| R2 桶 | ❌ | 凭据密文存 DB BYTEA 列，不存文件 |
| Redis 库 / 缓存键 | ❌ | 密钥按需 HKDF 派生不缓存；CRUD 低频 |
| Celery 任务 / Beat | ❌ | 失败告警同步内联（report_failure） |
| Prometheus 指标 | ✅（应用层） | 2 counter，NFR Design 定义，无基础设施配置 |

---

## 2. migration 016 详情

```sql
CREATE TABLE credential (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenant(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    platform VARCHAR(16) NOT NULL,
    username VARCHAR(128) NOT NULL,
    password_ciphertext BYTEA NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'paused',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_reason TEXT NULL,
    last_failure_at TIMESTAMPTZ NULL,
    privacy_consent_at TIMESTAMPTZ NOT NULL,
    remark TEXT NULL,
    CONSTRAINT ck_credential_status CHECK (status IN ('active','paused','disabled')),
    CONSTRAINT ck_credential_failures_nonneg CHECK (consecutive_failures >= 0)
);
CREATE UNIQUE INDEX uq_credential_tenant_plat_user ON credential (tenant_id, platform, username);
CREATE INDEX idx_credential_tenant_status ON credential (tenant_id, status);
-- enable_rls_sql("credential")：启用 RLS + tenant_id 策略

-- 权限 seed（ON CONFLICT DO NOTHING 幂等）
-- credential:read / credential:write / credential:delete
-- admin → 全部 3；operations → credential:read
```

- **无锁风险**：纯 CREATE TABLE，不 ALTER 现有表。
- **无回填**：新表无历史数据。
- **回滚**：downgrade DROP TABLE credential + DELETE 3 scope（无下游数据依赖）。

---

## 3. 密钥管理

| 项 | 说明 |
|---|---|
| master key | CREDENTIAL_MASTER_KEY（base64 32 字节），U01 通过 Zeabur Secrets 注入 |
| 派生 | HKDF-SHA256(master, salt=tenant_id, info=b"wecom-credential")，每次按需 |
| 存储分离 | 密文存 DB，master key 存环境变量——DB 泄露无 key 无法解密 |
| 轮换 | rotate_tenant_key 仍占位（P1+ KMS + 90 天轮换，需求 §12.2） |

---

## 4. 部署与回滚

| 项 | 说明 |
|---|---|
| 部署单位 | 代码 + migration 016 同批（main 分支自动 prod / PR staging） |
| migration 执行 | 专用 migrate job（与既有流程一致） |
| 回滚 | alembic downgrade 015（DROP credential 表 + DELETE scope）；代码版本回退 |
| 风险 | 极低——新表 + scope seed，无现有数据/表变更 |

---

## 5. 本地 Docker 验证

| 资源 | 端口 |
|---|---|
| PostgreSQL 16 | 5555 |
| Redis 7 | 6410 |

> 接 U11（5554/6409），避免端口冲突。

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新服务/依赖/桶/环境变量/Redis/Celery | ✅ |
| 唯一增量 migration 016 | ✅ |
| 密钥复用 CREDENTIAL_MASTER_KEY | ✅ |
| 部署回滚安全 | ✅ |
| 与 NFR Design migration 016 一致 | ✅ |

> 注：本文件的 spec-format 诊断（Missing Overview/Architecture）为已知假阳性，IGNORE。
