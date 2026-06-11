# U12 逻辑组件（Logical Components）

> 单元：U12 — 平台凭据 + 采集失败告警
> 新建 modules/credential 11 文件 + 修改 3 + migration 016

---

## 1. 新建组件（modules/credential/）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 模块包 |
| `enums.py` | CredentialPlatform（千牛/万相台/灰豚）+ CredentialStatus（active/paused/disabled）|
| `config.py` | CONSECUTIVE_FAILURE_THRESHOLD=3 |
| `exceptions.py` | PrivacyConsentRequired(422) / CredentialAlreadyExists(409) / CredentialNotFound(404) |
| `permissions.py` | SCOPE_READ/WRITE/DELETE = credential:read/write/delete |
| `models.py` | Credential ORM（TenantScopedModel + 9 业务字段） |
| `schemas.py` | CredentialCreate / CredentialUpdate / CredentialPublic / CredentialPage |
| `repository.py` | CredentialRepository（add/get_by_id/list/require）|
| `service.py` | CredentialService（create/get/list/update/pause/resume/delete/decrypt_for_purpose/report_failure/report_success）|
| `deps.py` | CredentialServiceDep |
| `api.py` | router /api/credentials（7 端点） |

## 2. 修改组件

| 组件 | 改动 |
|---|---|
| `app/core/metrics.py` | +credential_decrypt_total + credential_auto_paused_total |
| `modules/wecom/enums.py` | NotificationType +CREDENTIAL_FAILURE |
| `app/main.py` | 注册 credential_router |
| `alembic/versions/016_u12_create_credential.py` | credential 表 + RLS + UNIQUE + idx + CHECK + scope seed |

## 3. 复用组件

| 复用 | 来源 |
|---|---|
| encrypt_credential / decrypt_credential / CredentialDecryptError | U07 core/security/crypto.py |
| NotificationService + notify | U07 |
| AuditService | U01 |
| RoleRepository.list_user_ids_by_role_code | U10a (auth) |
| TenantScopedModel + RLS | U01 |
| require_permission + CurrentActiveUser | U01 |
| IntegrityError→409 模式 | U10b |

## 4. 依赖图

```
credential/api (7 端点)
  → CredentialService
      → CredentialRepository (CRUD)
      → crypto.py (encrypt/decrypt, U07)
      → AuditService (U01)
      → NotificationService (U07) ── report_failure best-effort
      → RoleRepository.list_user_ids_by_role_code (U10a)
      → core/metrics (2 counter)
      → config.CONSECUTIVE_FAILURE_THRESHOLD

U13 CrawlerTaskService（未来）
  → CredentialService.decrypt_for_purpose
  → CredentialService.report_failure / report_success
```
- 无循环依赖：credential 单向依赖 U01/U07/U10a。

## 5. CredentialService 方法 → 故事/规则映射

| 方法 | 故事 | 规则 |
|---|---|---|
| create | EP07-S02 | BR-U12-01~06 |
| get / list | EP07-S03 | BR-U12-10~13 |
| update | EP07-S02 补充 | BR-U12-20~22 |
| pause / resume | EP07-S05 | BR-U12-40~43 |
| delete | EP07-S05 | BR-U12-50~53 |
| decrypt_for_purpose | EP07-S04 | BR-U12-30~33 |
| report_failure / report_success | EP07-S06 | BR-U12-60~65 |

## 6. migration 016

```text
CREATE TABLE credential (
  id / tenant_id / created_at / updated_at (TenantScopedModel),
  platform VARCHAR(16) NOT NULL,
  username VARCHAR(128) NOT NULL,
  password_ciphertext BYTEA NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'paused',
  consecutive_failures INT NOT NULL DEFAULT 0,
  last_failure_reason TEXT NULL,
  last_failure_at TIMESTAMPTZ NULL,
  privacy_consent_at TIMESTAMPTZ NOT NULL,
  remark TEXT NULL
);
UNIQUE(tenant_id, platform, username);
idx_credential_tenant_status (tenant_id, status);
CHECK status IN ('active','paused','disabled');
CHECK consecutive_failures >= 0;
enable_rls_sql("credential");
seed: credential:read/write/delete → admin(全部) / operations(read);
downgrade: DROP TABLE + DELETE scope.
```

## 7. 测试文件

| 文件 | 类型 |
|---|---|
| tests/unit/test_credential_crypto.py | 加密往返 + 跨租户不可解 + tag 篡改 + 状态/失败计数 domain |
| tests/integration/test_credential_service.py | 创建/隐私 422/重复 409/解密审计/连续失败自动暂停+通知/RLS |
| tests/api/test_credential_api.py | 7 端点鉴权 + 响应不含密码 + OpenAPI |

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 11 + 修改 4 + migration 016 | ✅ |
| 复用 U01/U07/U10a | ✅ |
| 无循环依赖 | ✅ |
| 方法→故事/规则映射完整 | ✅ |
| 与 P-U12-01/02 一致 | ✅ |
