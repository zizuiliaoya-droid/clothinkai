# U12 功能设计计划（Functional Design Plan）

> 单元：U12 — 平台凭据 + 采集失败告警（EP07-S02~S06）
> 依赖：U01（认证+多租户+审计+crypto.py 基础）
> 被依赖：U13（采集 Worker 读凭据 + report_failure）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：credential 实体落在哪个模块目录？
[Answer] 新建 `modules/credential/`（独立于 importer 模块）。凭据是系统级安全实体而非导入框架子组件，对应 application-design 的 "CredentialService" 独立存在。U13 的 CrawlerTaskService 通过 `CredentialService.decrypt_for_purpose` 获取凭据。

### Q2：credential 表字段清单？
[Answer] `credential` 表（TenantScopedModel）：
- `platform` VARCHAR(16) NOT NULL — 千牛/万相台/灰豚
- `username` VARCHAR(128) NOT NULL — 平台账号
- `password_ciphertext` BYTEA NOT NULL — AES-256-GCM 加密密文（复用 core/security/crypto.py）
- `status` VARCHAR(16) NOT NULL DEFAULT 'paused' — active/paused/disabled
- `consecutive_failures` INTEGER NOT NULL DEFAULT 0
- `last_failure_reason` TEXT NULL — 最近失败原因
- `last_failure_at` TIMESTAMPTZ NULL
- `privacy_consent_at` TIMESTAMPTZ NOT NULL — 用户确认隐私提示时间
- `remark` TEXT NULL

唯一约束：`UNIQUE(tenant_id, platform, username)`（活跃凭据不重复）。

### Q3：隐私确认如何实现？
[Answer] `CredentialCreate` payload 中包含 `privacy_consent: bool` 字段。service 层校验 `if not privacy_consent: raise 422`；创建时将 `privacy_consent_at=now()` 写入 DB。前端在提交前展示隐私弹窗，用户确认后 frontend 自动置 true。后端仅校验布尔值、不负责展示文案。

### Q4：凭据列表/详情 API 返回什么？
[Answer] 返回 `CredentialPublic`（username + platform + status + consecutive_failures + last_failure_at + last_failure_reason + privacy_consent_at + created_at + updated_at）。**永不返回** password、password_ciphertext、明文密码。

### Q5：采集失败告警的"连续 N 次"怎么计算？
[Answer] `credential.consecutive_failures` 每次 report_failure +1；当 >= `FAILURE_THRESHOLD`（默认 3，写代码常量 `CONSECUTIVE_FAILURE_THRESHOLD=3`）自动置 status="paused"。成功采集后 reset 为 0。企微告警复用 U07 NotificationService（CREDENTIAL_FAILURE 通知类型）。

### Q6：解密审计怎么落地？
[Answer] `CredentialService.decrypt_for_purpose(credential_id, purpose)` 内部调用 `AuditService.log(action="credential.decrypt", resource="credential", resource_id=credential_id, after={"purpose": purpose})`。不需要 `@audit` 装饰器（因为需要 credential_id 参数上下文），直接在方法体内显式调用。

### Q7：删除是硬删还是软删？
[Answer] **硬删**——安全要求"凭据从存储清除明文"。删除前 `AuditService.log(action="credential.delete")`，然后 `session.delete(credential)`。关联的 crawler_task（U13 表）如果引用该 credential_id 的任务还未执行，状态标记为 cancelled（U13 负责处理 FK 引用；U12 不建 FK 到 crawler_task 表，因为 U13 尚未存在）。

### Q8：权限 scope 和角色绑定？
[Answer] `credential:read` / `credential:write` / `credential:delete` 三个 scope；migration 016 seed 并绑定：
- admin → credential:read + credential:write + credential:delete
- operations → credential:read
（只有 admin 可写和删；operations 只读列表查看状态）。U13 的 Worker 通过系统令牌（system_context）调用 decrypt，不走前端权限体系。

### Q9：企微告警通知哪些人？
[Answer] 复用 U07 NotificationService + CREDENTIAL_FAILURE 通知类型；收件人为该租户 admin 角色用户（`RoleRepository.list_user_ids_by_role_code("admin")`）。通知内容含 platform + username + failure_reason + "该凭据已自动暂停，请检查平台账号状态"。

### Q10：resume（恢复启用）是否重置 consecutive_failures？
[Answer] 是。`resume(credential_id)` 将 status="active" + consecutive_failures=0。审计记录 "credential.resume"。

---

## 1. 步骤

- [x] 1.1 阅读 EP07-S02~S06 GWT 验收标准 + requirements.md §12 + component-methods CredentialService 接口
- [x] 1.2 阅读已有 core/security/crypto.py 实现 + U07 wecom_config 如何用 encrypt/decrypt
- [x] 1.3 编写 domain-entities.md（Credential ORM + CredentialCreate/Update/Public schemas + 状态转换图）
- [x] 1.4 编写 business-rules.md（BR-U12-01~60 加密/不可回显/解密审计/暂停/删除/失败告警/隐私确认）
- [x] 1.5 编写 business-logic-model.md（6 UC + J5 端到端时序 + 跨单元契约 U13/U07）
- [x] 1.6 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.6（Plan + 3 文档，同一回合）。**
