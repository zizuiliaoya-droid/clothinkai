# U12 业务逻辑模型（Business Logic Model）

> 单元：U12 — 平台凭据 + 采集失败告警
> 故事：EP07-S02~S06

---

## 1. 用例列表

| UC | 名称 | 故事 | 主要角色 |
|---|---|---|---|
| UC-1 | 添加平台凭据 | EP07-S02 | 管理员 |
| UC-2 | 查看/列表凭据 | EP07-S03 | 管理员/运营 |
| UC-3 | 更新凭据密码 | EP07-S02 补充 | 管理员 |
| UC-4 | 暂停/恢复凭据 | EP07-S05 | 管理员 |
| UC-5 | 删除凭据 | EP07-S05 | 管理员 |
| UC-6 | 采集失败告警 | EP07-S06 | 系统/Worker |

---

## 2. UC-1 添加平台凭据

```
管理员 → POST /api/credentials
│
├─ 1. 校验 privacy_consent == true？
│     └─ false → 422 PRIVACY_CONSENT_REQUIRED
├─ 2. 校验 UNIQUE(tenant_id, platform, username)
│     └─ 重复 → 409 CREDENTIAL_ALREADY_EXISTS
├─ 3. encrypt_credential(tenant_id, password) → password_ciphertext
├─ 4. 创建 Credential(status='paused', consecutive_failures=0, privacy_consent_at=now())
├─ 5. AuditService.log(action="credential.create", after={platform, username})
├─ 6. commit
└─ 7. 返回 CredentialPublic（不含密码）
```

---

## 3. UC-2 查看/列表凭据

```
管理员/运营 → GET /api/credentials / GET /api/credentials/{id}
│
├─ 1. require_permission("credential", "read")
├─ 2. RLS 自动隔离 tenant_id
├─ 3. 列表：按 platform/status 筛选 + 分页 + ORDER BY updated_at DESC
└─ 4. 返回 CredentialPublic[]（永不含 password）
```

---

## 4. UC-3 更新凭据密码

```
管理员 → PUT /api/credentials/{id}
│
├─ 1. 加载 credential（404 if not found）
├─ 2. 如 payload.password 有值：
│     ├─ encrypt_credential(tenant_id, new_password) → password_ciphertext
│     └─ AuditService.log(action="credential.update", after={password_changed: true})
├─ 3. 如 payload.remark 有值：更新 remark
├─ 4. commit
└─ 5. 返回 CredentialPublic
```

---

## 5. UC-4 暂停/恢复凭据

```
管理员 → PUT /api/credentials/{id}/pause  |  /resume
│
├─ pause:
│   ├─ credential.status = 'paused'
│   └─ AuditService.log(action="credential.pause")
│
├─ resume:
│   ├─ credential.status = 'active'
│   ├─ credential.consecutive_failures = 0
│   └─ AuditService.log(action="credential.resume")
│
├─ commit
└─ 返回 CredentialPublic
```

---

## 6. UC-5 删除凭据

```
管理员 → DELETE /api/credentials/{id}
│
├─ 1. 加载 credential（404 if not found）
├─ 2. AuditService.log(action="credential.delete", after={platform, username})
├─ 3. session.delete(credential) — 硬删
├─ 4. commit
└─ 5. 返回 204
```

---

## 7. UC-6 采集失败告警（系统内部）

```
U13 CrawlerTaskService → CredentialService.report_failure(credential_id, error_reason)
│
├─ 1. 加载 credential
├─ 2. credential.consecutive_failures += 1
├─ 3. credential.last_failure_reason = error_reason
├─ 4. credential.last_failure_at = now()
├─ 5. if consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:
│     ├─ credential.status = 'paused'
│     └─ NotificationService.notify(
│           type=CREDENTIAL_FAILURE,
│           tenant_id=credential.tenant_id,
│           data={platform, username, failure_count, reason}
│         )
├─ 6. commit
└─ 7. 返回 None

U13 CrawlerTaskService → CredentialService.report_success(credential_id)
│
├─ 1. credential.consecutive_failures = 0
├─ 2. commit
└─ 3. 返回 None
```

---

## 8. J5 端到端时序（凭据生命周期）

```
管理员 ──────────────── 系统 ──────────────── Worker(U13) ──── 企微(U07)
   │                      │                      │                 │
   │  POST /credentials   │                      │                 │
   │─────────────────────▶│                      │                 │
   │  encrypt + save      │                      │                 │
   │◀─ CredentialPublic ──│                      │                 │
   │                      │                      │                 │
   │  PUT /{id}/resume    │                      │                 │
   │─────────────────────▶│ status=active        │                 │
   │                      │                      │                 │
   │                      │  poll_next_task      │                 │
   │                      │◀─────────────────────│                 │
   │                      │  decrypt_for_purpose │                 │
   │                      │  + audit_log         │                 │
   │                      │──────plaintext──────▶│                 │
   │                      │                      │ 采集失败        │
   │                      │  report_failure      │                 │
   │                      │◀─────────────────────│                 │
   │                      │  failures >= 3?      │                 │
   │                      │  → pause + notify    │                 │
   │                      │─────────────────────────────────────▶  │
   │                      │                      │   企微告警      │
   │◀──────────────────── 通知 ──────────────────────────────── ──│
```

---

## 9. CredentialService 接口定义

```python
class CredentialService:
    """U12 凭据管理服务。"""

    async def create(self, payload: CredentialCreate, user: User) -> CredentialPublic
    async def get(self, credential_id: UUID) -> CredentialPublic
    async def list(self, *, platform: str | None, status: str | None, page: int, page_size: int) -> CredentialPage
    async def update(self, credential_id: UUID, payload: CredentialUpdate, user: User) -> CredentialPublic
    async def pause(self, credential_id: UUID, user: User) -> CredentialPublic
    async def resume(self, credential_id: UUID, user: User) -> CredentialPublic
    async def delete(self, credential_id: UUID, user: User) -> None
    async def decrypt_for_purpose(self, credential_id: UUID, purpose: str) -> str
    async def report_failure(self, credential_id: UUID, error_reason: str) -> None
    async def report_success(self, credential_id: UUID) -> None
```

---

## 10. 跨单元契约

### 10.1 U12 → U07（复用）

| 消费 | 来源 | 说明 |
|---|---|---|
| NotificationService.notify | U07 notification_service | CREDENTIAL_FAILURE 通知类型 |
| NotificationType Enum | U07 wecom/enums | 追加 CREDENTIAL_FAILURE |
| RoleRepository.list_user_ids_by_role_code | U10a (auth) | 获取 admin 用户列表做通知 |

### 10.2 U13 → U12（被消费）

| 消费者 | 接口 | 说明 |
|---|---|---|
| CrawlerTaskService | decrypt_for_purpose | Worker 采集前获取明文密码 |
| CrawlerTaskService | report_failure / report_success | 采集结束回调更新状态 |

### 10.3 U01 → U12（基础）

| 消费 | 说明 |
|---|---|
| TenantScopedModel | ORM 基类 |
| AuditService | 审计日志 |
| require_permission | API 鉴权 |
| encrypt_credential / decrypt_credential | 加解密 |
| RLS | 多租户隔离 |

---

## 11. API 端点概览

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | /api/credentials | credential:write | 创建凭据 |
| GET | /api/credentials | credential:read | 列表（+筛选分页） |
| GET | /api/credentials/{id} | credential:read | 详情 |
| PUT | /api/credentials/{id} | credential:write | 更新密码/备注 |
| PUT | /api/credentials/{id}/pause | credential:write | 暂停 |
| PUT | /api/credentials/{id}/resume | credential:write | 恢复 |
| DELETE | /api/credentials/{id} | credential:delete | 删除 |

> 注：decrypt_for_purpose / report_failure / report_success 为内部服务接口，不暴露 HTTP 端点。U13 通过 Python 直接调用。

---

## 12. 一致性校验

| 校验 | 结果 |
|---|---|
| EP07-S02~S06 GWT 全覆盖 | ✅ |
| 需求 §12.1~12.7 全覆盖 | ✅ |
| 需求 §13.7 凭据安全验收 | ✅（UC-2 不返回密码 + UC-4/6 decrypt 审计）|
| component-methods CredentialService 接口对齐 | ✅ |
| 与 domain-entities 字段一致 | ✅ |
| 与 business-rules BR-U12-* 一致 | ✅ |
| 跨单元契约 U07/U13/U01 明确 | ✅ |
