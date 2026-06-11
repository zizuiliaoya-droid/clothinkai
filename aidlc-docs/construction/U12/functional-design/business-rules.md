# U12 业务规则（Business Rules）

> 单元：U12 — 平台凭据 + 采集失败告警
> 故事：EP07-S02~S06

---

## 1. 凭据创建（EP07-S02）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-01 | 创建凭据时 `privacy_consent` 必须为 true，否则 422 | §12.1 |
| BR-U12-02 | 密码立即 `encrypt_credential(tenant_id, password)` 写入 `password_ciphertext`；明文不落盘/不缓存 | §12.2 |
| BR-U12-03 | 初始 `status='paused'`——用户需主动 resume 启用采集 | EP07-S02 GWT |
| BR-U12-04 | 同 (tenant_id, platform, username) 唯一——重复返回 409 | 约束 |
| BR-U12-05 | 创建成功审计 `credential.create`（after: platform + username，不含密码） | §12.4 |
| BR-U12-06 | 创建成功返回 `CredentialPublic`（不含 password） | §12.3 |

## 2. 凭据查看 / 列表（EP07-S03）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-10 | 任何 GET 响应不含 password / password_ciphertext | §12.3 |
| BR-U12-11 | 列表支持按 platform / status 筛选 + 分页 | EP07-S03 |
| BR-U12-12 | 列表按 updated_at DESC 排序 | UX |
| BR-U12-13 | RLS 自动隔离——只能看本租户凭据 | §12.2 |

## 3. 凭据更新

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-20 | 更新密码时重新加密写入 password_ciphertext | §12.2 |
| BR-U12-21 | 更新审计 `credential.update`（仅记 password_changed=true，不记明文） | §12.4 |
| BR-U12-22 | 不允许更新 platform / username（如需更换账号请删除后新建） | 安全 |

## 4. 解密审计（EP07-S04）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-30 | `decrypt_for_purpose(credential_id, purpose)` 每次调用写 audit_log | §12.4 |
| BR-U12-31 | audit 字段：action="credential.decrypt", resource="credential", resource_id=credential_id, after={purpose, platform, username} | §12.4 |
| BR-U12-32 | 解密失败（密文损坏 / 密钥不匹配）→ 500 + Sentry capture + 审计 action="credential.decrypt_failed" | 安全 |
| BR-U12-33 | 解密返回明文仅传给调用方内存变量——不写日志、不写响应 | §12.3 |

## 5. 暂停与恢复（EP07-S05）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-40 | `pause(credential_id)` → status='paused'；审计 `credential.pause` | §12.5 |
| BR-U12-41 | `resume(credential_id)` → status='active' + consecutive_failures=0；审计 `credential.resume` | §12.5 |
| BR-U12-42 | paused 凭据——U13 采集调度跳过（U13 负责检查 status） | EP07-S05 GWT |
| BR-U12-43 | 只有 admin 才能 pause/resume（require_permission credential:write） | 权限 |

## 6. 删除（EP07-S05）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-50 | `delete(credential_id)` 硬删——密文从 DB 物理清除 | §12.5 "从存储清除明文" |
| BR-U12-51 | 删除前审计 `credential.delete`（记 platform + username） | §12.5 |
| BR-U12-52 | 删除后相关 U13 crawler_task 标记 cancelled（U13 在实施时处理 FK 检查） | EP07-S05 GWT |
| BR-U12-53 | 只有 admin 才能删除（require_permission credential:delete） | 权限 |

## 7. 采集失败告警（EP07-S06）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-60 | `report_failure(credential_id, error_reason)` → consecutive_failures += 1 + last_failure_reason=error + last_failure_at=now() | §12.6 |
| BR-U12-61 | 当 `consecutive_failures >= CONSECUTIVE_FAILURE_THRESHOLD`（默认 3）→ status='paused' + 企微通知 admin | §12.6 |
| BR-U12-62 | 企微通知复用 U07 NotificationService + CREDENTIAL_FAILURE 类型（追加到 wecom/enums NotificationType） | 架构 |
| BR-U12-63 | 通知内容："{platform} 凭据 {username} 连续 {N} 次采集失败，已自动暂停。请检查平台账号状态。" | UX |
| BR-U12-64 | `report_success(credential_id)` → consecutive_failures=0（重置计数器） | 逻辑 |
| BR-U12-65 | report_failure/success 由 U13 CrawlerTaskService 在任务完成时调用，U12 只暴露接口 | 契约 |

## 8. 安全约束

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U12-70 | 日志（structlog）永远 redact password / password_ciphertext 字段 | §12.3 |
| BR-U12-71 | Sentry breadcrumb / extra 不含密文 | §12.3 |
| BR-U12-72 | API 响应（成功/错误）永不包含 password_ciphertext | §12.3 |
| BR-U12-73 | credential 表启用 RLS（同所有 TenantScopedModel） | §12.2 |
| BR-U12-74 | CREDENTIAL_MASTER_KEY 通过环境变量注入，代码中不硬编码 | §12.2 |

---

## 9. 错误码矩阵

| 场景 | HTTP | code | 说明 |
|---|---|---|---|
| 隐私未确认 | 422 | PRIVACY_CONSENT_REQUIRED | BR-U12-01 |
| 重复凭据 | 409 | CREDENTIAL_ALREADY_EXISTS | BR-U12-04 |
| 凭据不存在 | 404 | CREDENTIAL_NOT_FOUND | — |
| 解密失败 | 500 | CREDENTIAL_DECRYPT_FAILED | BR-U12-32 |
| 权限不足 | 403 | PERMISSION_DENIED | — |
| 未认证 | 401 | TOKEN_INVALID | — |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| §12.1~12.7 全部映射 | ✅ |
| EP07-S02~S06 GWT 全覆盖 | ✅ |
| 与 domain-entities 字段一致 | ✅ |
| 不返回明文（3 层：schema/日志/错误响应） | ✅ |
