# U12 技术栈决策（Tech Stack Decisions）

> 单元：U12 — 平台凭据 + 采集失败告警
> 增量式：零新增依赖，复用 U01/U07 基础设施

---

## 1. 依赖决策

| 能力 | 选型 | 状态 |
|---|---|---|
| 加密 | cryptography（AESGCM + HKDF） | U07 已 pin，复用 |
| 通知 | U07 NotificationService | 复用 |
| 审计 | U01 AuditService | 复用 |
| ORM | SQLAlchemy 2.0 async + TenantScopedModel | 复用 |
| 指标 | prometheus（core/metrics.py） | 复用，追加 2 counter |

> **结论：requirements.txt / requirements-dev.txt 不变。**

---

## 2. 代码落点

| 文件 | 职责 | 新建/修改 |
|---|---|---|
| `modules/credential/__init__.py` | 模块包 | 新建 |
| `modules/credential/enums.py` | CredentialPlatform + CredentialStatus Enum | 新建 |
| `modules/credential/config.py` | CONSECUTIVE_FAILURE_THRESHOLD=3 常量 | 新建 |
| `modules/credential/exceptions.py` | PrivacyConsentRequired/CredentialAlreadyExists/CredentialNotFound | 新建 |
| `modules/credential/permissions.py` | SCOPE_READ/WRITE/DELETE | 新建 |
| `modules/credential/models.py` | Credential ORM | 新建 |
| `modules/credential/schemas.py` | CredentialCreate/Update/Public/Page | 新建 |
| `modules/credential/repository.py` | CredentialRepository | 新建 |
| `modules/credential/service.py` | CredentialService（10 方法） | 新建 |
| `modules/credential/deps.py` | CredentialServiceDep | 新建 |
| `modules/credential/api.py` | router /api/credentials（7 端点） | 新建 |
| `app/main.py` | 注册 credential_router | 修改 |
| `app/core/metrics.py` | +2 counter | 修改 |
| `modules/wecom/enums.py` | NotificationType +CREDENTIAL_FAILURE | 修改 |
| `alembic/versions/016_u12_create_credential.py` | credential 表 + RLS + scope seed | 新建 |

---

## 3. 加密复用（crypto.py）

```python
from app.core.security.crypto import encrypt_credential, decrypt_credential, CredentialDecryptError

# 创建/更新
ciphertext = encrypt_credential(tenant_id, payload.password.get_secret_value())

# 解密（Worker）
plaintext = decrypt_credential(tenant_id, credential_id, ciphertext, purpose="crawler_qianniu")
```

> 注：crypto.py 的 info 标签 `b"wecom-credential"` 为 U07 定义；U12 复用同一派生密钥（同租户）。
> 不同密文 nonce 独立随机，互不影响——安全性不受影响。

---

## 4. 失败阈值常量

```python
# modules/credential/config.py
CONSECUTIVE_FAILURE_THRESHOLD = 3
"""连续采集失败 N 次后自动暂停凭据（V1+ system_setting 可配）。"""
```

---

## 5. 指标定义（core/metrics.py 追加）

```python
credential_decrypt_total = Counter(
    "credential_decrypt_total",
    "凭据解密次数",
    ["platform", "result"],  # success / failed
)

credential_auto_paused_total = Counter(
    "credential_auto_paused_total",
    "凭据因连续失败自动暂停次数",
    ["platform"],
)
```

---

## 6. NotificationType 扩展（wecom/enums.py）

```python
class NotificationType(str, Enum):
    # ... 既有 DESIGN_ADVANCE / DESIGN_REJECT / DESIGN_DONE ...
    CREDENTIAL_FAILURE = "credential_failure"  # U12 凭据连续失败告警
```

---

## 7. migration 016 片段

```python
revision = "016_u12_create_credential"
down_revision = "015_u11_add_audience_profile"

def upgrade():
    op.create_table(
        "credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'paused'")),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_failure_reason", sa.Text(), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("privacy_consent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('active','paused','disabled')", name="ck_credential_status"),
        sa.CheckConstraint("consecutive_failures >= 0", name="ck_credential_failures_nonneg"),
    )
    op.create_index("uq_credential_tenant_plat_user", "credential",
                    ["tenant_id", "platform", "username"], unique=True)
    op.create_index("idx_credential_tenant_status", "credential", ["tenant_id", "status"])
    op.execute(enable_rls_sql("credential"))
    _seed_permissions()  # credential:read/write/delete → admin(全部)/operations(read)
```

---

## 8. 测试落点

| 文件 | 类型 |
|---|---|
| tests/unit/test_credential_crypto.py | 加密往返 + 跨租户不可解 + tag 篡改 + 状态/失败计数 domain |
| tests/integration/test_credential_service.py | 创建/隐私 422/重复 409/解密审计/连续失败自动暂停+通知/RLS |
| tests/api/test_credential_api.py | 7 端点鉴权 + 响应不含密码 + OpenAPI |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| modules/credential 11 文件落点 | ✅ |
| crypto.py 复用 | ✅ |
| CONSECUTIVE_FAILURE_THRESHOLD 常量 | ✅ |
| migration 016 接 015 | ✅ |
| 2 metrics + NotificationType 扩展 | ✅ |
| 测试 3 文件 | ✅ |
| 与 nfr-requirements 一致 | ✅ |
