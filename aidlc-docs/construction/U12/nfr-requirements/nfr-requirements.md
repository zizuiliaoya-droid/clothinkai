# U12 NFR 需求（NFR Requirements）

> 单元：U12 — 平台凭据 + 采集失败告警
> 增量式：复用 U01 基线 + U07 加密基础设施，仅列 U12 特异指标

---

## 1. 依赖与复用

| 项 | 决策 |
|---|---|
| 新增第三方依赖 | **零**——加密复用 U07 `cryptography`；通知复用 U07 NotificationService；审计复用 U01 AuditService |
| 新表 | credential（migration 016） |
| 新环境变量 | **无**——复用 U01 已注入的 CREDENTIAL_MASTER_KEY |
| 新 Celery 任务 | **无**——失败告警同步处理（report_failure 内联通知） |
| 新 R2 桶 | **无**——凭据仅密文存 DB（不存文件） |

---

## 2. 性能需求

| 操作 | SLA | 实现路径 |
|---|---|---|
| 创建凭据 | P95 ≤ 200ms | 单行 INSERT + encrypt(<5ms) |
| 更新凭据 | P95 ≤ 200ms | 单行 UPDATE + encrypt |
| 暂停/恢复/删除 | P95 ≤ 200ms | 单行 UPDATE/DELETE |
| 列表查询 | P95 ≤ 200ms | idx_credential_tenant_status + ≤30 凭据/租户 |
| 解密（Worker） | P95 ≤ 50ms | 单行 SELECT + HKDF(<1ms) + AESGCM(<1ms) |

### 加密性能

| 操作 | 目标 | 说明 |
|---|---|---|
| encrypt_credential | < 5ms | HKDF 派生 + AESGCM 加密 |
| decrypt_credential | < 5ms | HKDF 派生 + AESGCM 解密 + tag 校验 |
| 密钥缓存 | 不缓存 | 每次按需派生，降低泄露面（HKDF 开销可忽略） |

---

## 3. 容量需求

| 维度 | 假设 |
|---|---|
| 单租户凭据数 | ≤ 30（3 平台 × 多账号） |
| 全局凭据数 | ≤ 数千 |
| 解密频率 | 低频（管理员手动 + Worker 采集前一次/任务） |

---

## 4. 安全需求（核心）

### 4.1 凭据加密威胁模型（复用 U07）

| 威胁 | 缓解 |
|---|---|
| 跨租户密文解密 | salt=tenant_id.bytes → A 派生密钥无法解 B 密文 |
| 密文篡改 | AES-256-GCM 认证标签 tag 校验失败 → CredentialDecryptError → 500（不静默返回空） |
| master key 泄露 | 环境变量注入（与 DB 分离），代码不硬编码；structlog redact credential_master_key |
| 数据库泄露 | 仅密文落盘；无 master key 无法解密 |

### 4.2 不可回显（3 层防御）

| 层 | 措施 | 编号 |
|---|---|---|
| Schema | CredentialPublic 无 password / password_ciphertext 字段 | BR-U12-10 |
| 日志 | structlog redact password / password_ciphertext / credential_master_key | BR-U12-70 |
| 错误响应 | 业务异常 message 不含密文；Sentry extra 不含密文 | BR-U12-71/72 |

### 4.3 解密审计（不可篡改）

| 项 | 措施 |
|---|---|
| 每次解密写 audit_log | action="credential.decrypt"（BR-U12-30） |
| 审计字段 | tenant_id, user_id, credential_id, platform, purpose, timestamp |
| append-only | 复用 U01 audit_log REVOKE UPDATE/DELETE（migration 002） |
| 解密失败审计 | action="credential.decrypt_failed" + Sentry capture |

### 4.4 多租户隔离

| 措施 | 来源 |
|---|---|
| RLS 自动隔离 | credential 表 enable_rls_sql（migration 016） |
| HKDF salt 隔离 | 加密层（crypto.py） |
| 测试显式 WHERE tenant_id | bypass 角色聚合查询 |

---

## 5. 可靠性需求

| 项 | 决策 |
|---|---|
| 连续失败阈值 | `CONSECUTIVE_FAILURE_THRESHOLD=3`（代码常量；V1+ system_setting 可配） |
| 失败告警容错 | 通知发送失败不阻塞主流程；凭据置 paused 必须成功；通知 best-effort（复用 U07 容错） |
| 删除安全 | 硬删——密文物理清除（BR-U12-50） |
| resume 重置 | consecutive_failures=0（BR-U12-41/64） |

---

## 6. 可观测性需求

| 指标 | 类型 | 标签 |
|---|---|---|
| `credential_decrypt_total` | Counter | platform, result(success/failed) |
| `credential_auto_paused_total` | Counter | platform |

> 结构化日志：credential.create/update/pause/resume/delete/decrypt 事件（敏感字段 redact）。

---

## 7. 数据迁移

| 项 | 决策 |
|---|---|
| migration 016 | 创建 credential 表 + RLS + UNIQUE + idx + CHECK + seed 3 scope |
| 回填 | 无（新表，无历史数据） |
| 回滚 | downgrade DROP TABLE + DELETE scope |

---

## 8. 测试需求

| 类型 | 覆盖目标 | 关键场景 |
|---|---|---|
| 单元 | 加密/domain ≥ 90% | encrypt/decrypt 往返 + 跨租户密钥不可解 + tag 篡改抛错 + 状态转换 + 失败计数 |
| 集成 | service ≥ 80% | 创建加密 + 隐私未确认 422 + 重复 409 + 解密审计写入 + 连续 3 次失败自动 paused + 通知 + RLS 隔离 |
| API | ≥ 60% | 7 端点鉴权（401/403）+ 响应不含密码 + OpenAPI |
| 整体 | ≥ 70% | 与既有门槛一致 |

### 多租户隔离测试矩阵

| 场景 | 期望 |
|---|---|
| A 租户列表 | 不含 B 租户凭据（RLS） |
| A 密钥解 B 密文 | CredentialDecryptError |
| 解密审计 tenant_id | 正确归属 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| 加密 SLA + API SLA 量化 | ✅ |
| 威胁模型 + 不可回显 3 层 | ✅ §4 |
| 解密审计 append-only | ✅ §4.3 |
| migration 016 + 回滚 | ✅ §7 |
| 2 指标 | ✅ §6 |
| 测试矩阵 + 多租户隔离 | ✅ §8 |
| 与 functional-design §12 / EP07-S02~S06 一致 | ✅ |
