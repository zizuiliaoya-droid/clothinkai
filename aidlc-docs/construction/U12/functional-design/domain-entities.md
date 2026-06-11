# U12 领域实体（Domain Entities）

> 单元：U12 — 平台凭据 + 采集失败告警
> 故事：EP07-S02~S06

---

## 1. 实体概览

| 实体 | 类型 | 用途 |
|---|---|---|
| `Credential` | ORM (TenantScopedModel) | 平台凭据加密存储 + 状态管理 |

> U12 只新增 1 个表。复用 core/security/crypto.py（U07 已落地）+ AuditService + NotificationService。

---

## 2. Credential 实体

### 2.1 字段

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK（TenantScopedModel 继承） | |
| tenant_id | UUID | FK + RLS（继承） | |
| created_at | TIMESTAMPTZ | auto | |
| updated_at | TIMESTAMPTZ | auto | |
| platform | VARCHAR(16) | NOT NULL | 千牛/万相台/灰豚 |
| username | VARCHAR(128) | NOT NULL | 平台登录账号 |
| password_ciphertext | BYTEA | NOT NULL | AES-256-GCM 加密密文（nonce‖ct‖tag）|
| status | VARCHAR(16) | NOT NULL DEFAULT 'paused' | active / paused / disabled |
| consecutive_failures | INTEGER | NOT NULL DEFAULT 0 | 连续采集失败次数 |
| last_failure_reason | TEXT | NULL | 最近失败原因 |
| last_failure_at | TIMESTAMPTZ | NULL | 最近失败时间 |
| privacy_consent_at | TIMESTAMPTZ | NOT NULL | 用户确认隐私提示时间 |
| remark | TEXT | NULL | 管理员备注 |

### 2.2 约束与索引

| 约束 | 说明 |
|---|---|
| `UNIQUE(tenant_id, platform, username)` | 同租户同平台同账号不重复 |
| `CHECK(status IN ('active','paused','disabled'))` | 状态三值 |
| `CHECK(consecutive_failures >= 0)` | 非负 |
| `idx_credential_tenant_status` | 列表查询加速 |
| RLS | enable_rls_sql("credential") — 继承 TenantScopedModel |

### 2.3 状态转换图

```
         ┌─── create ────┐
         │               │
         ▼               │
     ┌──────┐            │
     │paused│◄─── pause ─┤
     └──┬───┘            │
        │ resume         │
        ▼                │
     ┌──────┐            │
     │active│────────────┘
     └──┬───┘
        │ N 次失败 auto
        ▼
     ┌────────┐
     │disabled│  (连续失败自动暂停，实际置 paused + 通知)
     └────────┘

注：disabled 不作为独立状态——连续失败后置 paused，
    通过 consecutive_failures >= 3 标识"因失败暂停"。
    resume 重置 consecutive_failures=0。
    delete 硬删。
```

---

## 3. Pydantic Schemas

### 3.1 CredentialCreate

```python
class CredentialCreate(BaseModel):
    platform: CredentialPlatform  # Enum: 千牛/万相台/灰豚
    username: str  # 1~128
    password: SecretStr  # 传输后即加密，不持久化明文
    privacy_consent: bool  # 必须 True
    remark: str | None = None
```

### 3.2 CredentialUpdate

```python
class CredentialUpdate(BaseModel):
    """部分更新（仅允许改密码和备注）。"""
    password: SecretStr | None = None  # 新密码（可选）
    remark: str | None = None
```

### 3.3 CredentialPublic（响应）

```python
class CredentialPublic(BaseModel):
    """永不包含明文密码或密文。"""
    id: UUID
    platform: str
    username: str
    status: str
    consecutive_failures: int
    last_failure_reason: str | None
    last_failure_at: datetime | None
    privacy_consent_at: datetime
    remark: str | None
    created_at: datetime
    updated_at: datetime
```

### 3.4 CredentialPlatform Enum

```python
class CredentialPlatform(str, Enum):
    QIANNIU = "千牛"
    WANXIANGTAI = "万相台"
    HUITUN = "灰豚"
```

---

## 4. 加密方案复用

| 操作 | 函数 | 来源 |
|---|---|---|
| 创建/更新密码 | `encrypt_credential(tenant_id, plaintext)` | core/security/crypto.py (U07) |
| Worker 解密 | `decrypt_credential(tenant_id, credential_id, ciphertext, purpose=...)` | 同上 |
| 密钥派生 | HKDF(master=CREDENTIAL_MASTER_KEY, salt=tenant_id, info=b"wecom-credential") | 同上 |

> 注意 info 标签为 `b"wecom-credential"` 是 U07 定义的。U12 凭据与 wecom 配置共用同一派生密钥（同租户）。
> 安全性不受影响——不同密文的 nonce 独立随机，互不影响解密结果。

---

## 5. 与其他单元的关系

| 单元 | 关系 |
|---|---|
| U07 | 复用 crypto.py + NotificationService（CREDENTIAL_FAILURE 通知类型追加到 wecom enums） |
| U13 | CrawlerTaskService 调用 `CredentialService.decrypt_for_purpose` 获取明文 + 采集完成后调用 `report_success/report_failure` |
| U01 | 继承 TenantScopedModel + AuditService + RLS + require_permission |

---

## 6. 一致性校验

| 校验项 | 结果 |
|---|---|
| EP07-S02 AES-256 加密入库 + credential_id 不含明文 | ✅ 2.1 password_ciphertext |
| EP07-S03 响应不含 password | ✅ 3.3 CredentialPublic |
| EP07-S04 解密写 audit_log | ✅ 4 decrypt + AuditService |
| EP07-S05 暂停/删除 | ✅ 2.3 状态转换 + 硬删 |
| EP07-S06 连续 N 次失败自动暂停 + 企微告警 | ✅ 2.1 consecutive_failures + NotificationService |
| 需求 §12.1 隐私确认 | ✅ 2.1 privacy_consent_at + 3.1 privacy_consent |
| 需求 §12.2 加密存储 | ✅ 4 AES-256-GCM 复用 |
| 需求 §12.3 不可回显 | ✅ 3.3 无密码字段 |
| 需求 §12.7 最小权限 | ✅ 文案引导（前端），后端不强制 |
