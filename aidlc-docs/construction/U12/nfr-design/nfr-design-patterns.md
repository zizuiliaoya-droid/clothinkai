# U12 NFR 设计模式（NFR Design Patterns）

> 单元：U12 — 平台凭据 + 采集失败告警
> 模式：P-U12-01（凭据 CRUD + 加密 + 不可回显）、P-U12-02（解密审计 + 失败告警自动暂停）

---

## P-U12-01 — 凭据 CRUD + 加密 + 不可回显

### create（隐私校验 + 加密 + IntegrityError→409）

```python
async def create(self, payload: CredentialCreate, user: User) -> CredentialPublic:
    # BR-U12-01: 隐私确认
    if not payload.privacy_consent:
        raise PrivacyConsentRequired("请先确认隐私协议")

    # BR-U12-02: 加密（明文不落盘）
    ciphertext = encrypt_credential(
        user.tenant_id, payload.password.get_secret_value()
    )
    cred = Credential(
        platform=payload.platform.value,
        username=payload.username,
        password_ciphertext=ciphertext,
        status="paused",                 # BR-U12-03 默认暂停
        consecutive_failures=0,
        privacy_consent_at=datetime.now(UTC),
        remark=payload.remark,
    )
    self._repo.add(cred)
    try:
        await self._session.flush()
    except IntegrityError:               # BR-U12-04 UNIQUE 冲突
        await self._session.rollback()
        raise CredentialAlreadyExists("该平台账号凭据已存在")

    await self._audit.log(
        action="credential.create", resource="credential",
        resource_id=cred.id,
        after={"platform": cred.platform, "username": cred.username},
        user_id=user.id,
    )
    await self._session.commit()
    return self._to_public(cred)
```

### _to_public（不可回显 — schema 层无密码字段）

```python
def _to_public(self, c: Credential) -> CredentialPublic:
    # 永不含 password / password_ciphertext
    return CredentialPublic(
        id=c.id, platform=c.platform, username=c.username,
        status=c.status, consecutive_failures=c.consecutive_failures,
        last_failure_reason=c.last_failure_reason,
        last_failure_at=c.last_failure_at,
        privacy_consent_at=c.privacy_consent_at,
        remark=c.remark, created_at=c.created_at, updated_at=c.updated_at,
    )
```

### update（密码变更脱敏审计）

```python
async def update(self, cid, payload, user) -> CredentialPublic:
    cred = await self._require(cid)
    if payload.password is not None:                 # BR-U12-20
        cred.password_ciphertext = encrypt_credential(
            user.tenant_id, payload.password.get_secret_value()
        )
        audit_after = {"password_changed": True}     # BR-U12-21 脱敏
    else:
        audit_after = {}
    if payload.remark is not None:
        cred.remark = payload.remark
    await self._session.flush()
    if audit_after:
        await self._audit.log(action="credential.update", resource="credential",
                              resource_id=cred.id, after=audit_after, user_id=user.id)
    await self._session.commit()
    return self._to_public(cred)
```

### pause / resume / delete

```python
async def pause(self, cid, user) -> CredentialPublic:
    cred = await self._require(cid)
    cred.status = "paused"                           # BR-U12-40
    await self._session.flush()
    await self._audit.log(action="credential.pause", resource="credential",
                          resource_id=cred.id, user_id=user.id)
    await self._session.commit()
    return self._to_public(cred)

async def resume(self, cid, user) -> CredentialPublic:
    cred = await self._require(cid)
    cred.status = "active"                           # BR-U12-41
    cred.consecutive_failures = 0                    # 重置
    await self._session.flush()
    await self._audit.log(action="credential.resume", resource="credential",
                          resource_id=cred.id, user_id=user.id)
    await self._session.commit()
    return self._to_public(cred)

async def delete(self, cid, user) -> None:
    cred = await self._require(cid)
    await self._audit.log(action="credential.delete", resource="credential",  # BR-U12-51 先审计
                          resource_id=cred.id,
                          after={"platform": cred.platform, "username": cred.username},
                          user_id=user.id)
    await self._session.delete(cred)                 # BR-U12-50 硬删
    await self._session.commit()
```

---

## P-U12-02 — 解密审计 + 失败告警自动暂停

### decrypt_for_purpose（审计 + 指标 + 不静默失败）

```python
async def decrypt_for_purpose(self, cid: UUID, purpose: str) -> str:
    cred = await self._require(cid)
    # 注：不检查 status — 调度层（U13）负责跳过 paused（BR-U12-42）
    try:
        plaintext = decrypt_credential(
            cred.tenant_id, cred.id, cred.password_ciphertext, purpose=purpose
        )
    except CredentialDecryptError:
        credential_decrypt_total.labels(cred.platform, "failed").inc()
        await self._audit.log(action="credential.decrypt_failed", resource="credential",
                              resource_id=cred.id, after={"purpose": purpose})
        await self._session.commit()
        raise                                        # BR-U12-32 → 500 + Sentry
    # BR-U12-30/31 解密成功审计
    credential_decrypt_total.labels(cred.platform, "success").inc()
    await self._audit.log(action="credential.decrypt", resource="credential",
                          resource_id=cred.id,
                          after={"purpose": purpose, "platform": cred.platform,
                                 "username": cred.username})
    await self._session.commit()
    return plaintext                                 # BR-U12-33 仅返回内存，不写日志
```

### report_failure（自动暂停 + 通知 best-effort）

```python
async def report_failure(self, cid: UUID, error_reason: str) -> None:
    cred = await self._require(cid)
    cred.consecutive_failures += 1                   # BR-U12-60
    cred.last_failure_reason = error_reason
    cred.last_failure_at = datetime.now(UTC)
    notify_needed = False
    if cred.consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD:  # BR-U12-61
        cred.status = "paused"
        credential_auto_paused_total.labels(cred.platform).inc()
        notify_needed = True
    await self._session.flush()
    await self._session.commit()                     # 状态变更必须落盘
    # best-effort 通知（commit 后，失败不回滚）BR-U12-62/63
    if notify_needed:
        try:
            admin_ids = await self._roles.list_user_ids_by_role_code("admin")
            await self._notifier.notify(
                type=NotificationType.CREDENTIAL_FAILURE,
                recipient_ids=admin_ids,
                data={"platform": cred.platform, "username": cred.username,
                      "failure_count": cred.consecutive_failures,
                      "reason": error_reason},
            )
        except Exception:  # noqa: BLE001 通知失败不影响凭据状态
            log.warning("credential_failure_notify_failed credential_id=%s", str(cid))

async def report_success(self, cid: UUID) -> None:
    cred = await self._require(cid)
    cred.consecutive_failures = 0                    # BR-U12-64 重置
    await self._session.flush()
    await self._session.commit()
```

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| create IntegrityError→409 防 TOCTOU | ✅ P-U12-01 |
| 不可回显 schema 无密码字段 | ✅ _to_public |
| update 密码变更脱敏审计 | ✅ P-U12-01 |
| 硬删 + 删除前审计 | ✅ delete |
| 解密审计 success/failed 双分支 + 指标 | ✅ P-U12-02 |
| 解密不静默失败（500+Sentry） | ✅ decrypt_for_purpose |
| 自动暂停同事务 + 通知 best-effort commit 后 | ✅ report_failure |
| report_success 重置计数 | ✅ |
| 解密不检查 status（调度层负责） | ✅ |
