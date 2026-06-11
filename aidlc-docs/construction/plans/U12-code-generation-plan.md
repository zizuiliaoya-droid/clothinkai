# U12 代码生成计划（Code Generation Plan）

> 单元：U12 — 平台凭据 + 采集失败告警（EP07-S02~S06）
> 分批：**单批** + Build & Test
> Build & Test：Docker PG16:5555 + Redis7:6410 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 新建独立 `modules/credential/` 模块（11 文件）；不放 importer。
- [Answer] Credential 继承 TenantScopedModel；password_ciphertext 用 LargeBinary(BYTEA)。
- [Answer] 异常继承 core/exceptions：PrivacyConsentRequired(ValidationError 422)/CredentialAlreadyExists(DuplicateResourceError 409)/CredentialNotFound(ResourceNotFoundError 404)。
- [Answer] CredentialService.notify 复用 U07 NotificationService（notify 仅 flush，best-effort 块内自行 commit）。
- [Answer] 加密复用 core/security/crypto.py encrypt_credential/decrypt_credential/CredentialDecryptError。
- [Answer] 2 指标加 core/metrics.py；NotificationType +CREDENTIAL_FAILURE；main 注册 credential_router。
- [Answer] migration 016：credential 表 + RLS + UNIQUE + idx + CHECK + 3 scope seed（admin 全部 / operations read）。

---

## 1. 步骤

- [x] 1.1 modules/credential/__init__.py + enums.py（CredentialPlatform + CredentialStatus）+ config.py（CONSECUTIVE_FAILURE_THRESHOLD=3）
- [x] 1.2 modules/credential/exceptions.py（3 异常）+ permissions.py（3 scope）
- [x] 1.3 modules/credential/models.py（Credential ORM）
- [x] 1.4 modules/credential/schemas.py（Create/Update/Public/Page）
- [x] 1.5 modules/credential/repository.py（CredentialRepository）
- [x] 1.6 modules/credential/service.py（CredentialService 10 方法）
- [x] 1.7 modules/credential/deps.py（CredentialServiceDep）
- [x] 1.8 modules/credential/api.py（router /api/credentials 7 端点）
- [x] 1.9 core/metrics.py（+credential_decrypt_total + credential_auto_paused_total）
- [x] 1.10 modules/wecom/enums.py（NotificationType +CREDENTIAL_FAILURE）
- [x] 1.11 app/main.py（注册 credential_router）
- [x] 1.12 alembic/versions/016_u12_create_credential.py
- [x] 1.13 tests/unit/test_credential_crypto.py
- [x] 1.14 tests/integration/test_credential_service.py
- [x] 1.15 tests/api/test_credential_api.py

### Build & Test
- [x] B.1 Docker PG16:5555 + Redis7:6410；alembic upgrade head（含 016）；U12 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部步骤 + Build & Test。**
