# U12 NFR 需求计划（NFR Requirements Plan）

> 单元：U12 — 平台凭据 + 采集失败告警
> 增量式：复用 U01 NFR 基线 + U07 加密基础设施，仅列 U12 特异指标

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否引入新依赖？
[Answer] 零新增依赖。加密复用 U07 已 pin 的 `cryptography`（AESGCM+HKDF）；通知复用 U07 NotificationService；审计复用 U01 AuditService。

### Q2：加密性能目标？
[Answer] 单次 encrypt/decrypt < 5ms（HKDF 派生 < 1ms + AESGCM < 1ms，实测余量充足）。凭据 CRUD 非高频操作（管理员手动 + Worker 采集前一次），无需缓存密钥。

### Q3：API 性能 SLA？
[Answer] 创建/更新/暂停/删除 P95 ≤ 200ms（单行写）；列表 P95 ≤ 200ms（≤100 凭据/租户，命中 idx_credential_tenant_status）；解密 P95 ≤ 50ms（单行读 + HKDF + AESGCM）。

### Q4：容量假设？
[Answer] 单租户凭据数 ≤ 30（千牛/万相台/灰豚 × 多账号）；全局 ≤ 数千。无分页性能压力，但仍提供分页接口。

### Q5：安全威胁模型？
[Answer] 复用 U07 凭据加密威胁模型：
- 跨租户：salt=tenant_id → A 密钥不可解 B 密文
- 篡改：AESGCM tag 校验失败 → CredentialDecryptError → 500（不静默）
- 不可回显：3 层防御（Pydantic schema 无密码字段 / structlog redact / 错误响应不含密文）
- 解密审计：每次 decrypt 写 audit_log（append-only，DB 层拒绝改删）
- master key 与 DB 分离（环境变量注入）

### Q6：解密审计的不可篡改性？
[Answer] 复用 U01 audit_log 表的 append-only 保证（migration 002 已 REVOKE UPDATE/DELETE）。U12 不需额外措施。

### Q7：连续失败阈值是否可配置？
[Answer] U12 写代码常量 `CONSECUTIVE_FAILURE_THRESHOLD=3`（modules/credential/config.py 或 service 常量）。V1+ system_setting 单元落地后改为租户级可配。

### Q8：企微告警失败如何处理？
[Answer] 复用 U07 NotificationService 的容错——通知发送失败不阻塞 report_failure 主流程（凭据状态置 paused 必须成功；通知是 best-effort）。通知降级走 U07 既有 notification 表 + 异步重试机制。

### Q9：多租户隔离测试要求？
[Answer] 测试矩阵必含：A 租户凭据 B 租户不可见（RLS）+ A 密钥不可解 B 密文（HKDF salt）+ 解密审计写入正确 tenant_id。测试引擎 bypass 角色 → 聚合/全局查询显式 WHERE tenant_id。

### Q10：migration 编号与内容？
[Answer] migration 016（接 015）：创建 credential 表（TenantScopedModel：id/tenant_id/created_at/updated_at + 业务字段）+ RLS enable + UNIQUE(tenant,platform,username) + idx_credential_tenant_status + CHECK 约束；seed credential:read/write/delete 3 scope 绑 admin(全部)/operations(read)。downgrade DROP TABLE + DELETE scope。

### Q11：可观测性指标？
[Answer] 追加 2 个 Prometheus 指标（复用 core/metrics.py）：
- `credential_decrypt_total{platform, result}`（success/failed）
- `credential_auto_paused_total{platform}`（连续失败自动暂停计数）

### Q12：测试覆盖目标？
[Answer] service ≥ 80%、domain/加密 ≥ 90%、api ≥ 60%；整体 ≥ 70%（与既有门槛一致）。

---

## 1. 步骤

- [x] 1.1 编写 nfr-requirements.md（零依赖 + 加密/API/解密 SLA + 威胁模型 + 不可回显 3 层 + 解密审计 append-only + 失败阈值常量 + 通知容错 + 多租户隔离 + migration 016 + 2 指标 + 测试矩阵）
- [x] 1.2 编写 tech-stack-decisions.md（零依赖复用 cryptography/NotificationService/AuditService + modules/credential 落点 + CredentialService 方法 + crypto.py 复用 + CONSECUTIVE_FAILURE_THRESHOLD 常量 + migration 016 片段 + 2 metrics + 测试落点）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
