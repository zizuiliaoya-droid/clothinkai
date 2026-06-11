# U12 NFR 设计计划（NFR Design Plan）

> 单元：U12 — 平台凭据 + 采集失败告警
> 模式：P-U12-01（凭据 CRUD + 加密 + 不可回显）、P-U12-02（解密审计 + 失败告警自动暂停）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：create 的并发唯一性如何保证？
[Answer] 依赖 `UNIQUE(tenant_id, platform, username)`；create 时 catch IntegrityError → 409 CREDENTIAL_ALREADY_EXISTS（防 TOCTOU，与 U10b create 同模式）。

### Q2：解密审计写入与解密本身的事务关系？
[Answer] decrypt_for_purpose 先解密（纯计算，无 DB 写）→ 成功后 AuditService.log + commit。解密失败 → 记 credential.decrypt_failed 审计 + 抛 500（审计也要 commit）。审计写入不阻塞返回明文（解密成功路径：先 log 再返回）。

### Q3：report_failure 的自动暂停 + 通知是否同事务？
[Answer] 同事务：consecutive_failures+1 + （达阈值时）status=paused 在一个 UPDATE 提交。通知 NotificationService.notify 在 commit 后触发（best-effort，失败不回滚状态）。与 U07 send 模式一致。

### Q4：report_failure/success 的调用者上下文？
[Answer] U13 CrawlerTaskService 在 system_context（Worker 无前端用户）下调用。U12 的 report_* 方法不要求 user 参数；审计 user_id 可空（系统操作）。

### Q5：decrypt_for_purpose 是否检查 status？
[Answer] 不检查——U13 调度层负责跳过 paused 凭据（BR-U12-42）。decrypt 是纯能力函数，无论状态都能解密（避免 Worker 已领任务后状态变更导致解密失败的边界问题）。但记审计。

### Q6：通知收件人解析？
[Answer] 复用 `RoleRepository.list_user_ids_by_role_code("admin")`（U10a 已加）获取该租户 admin 用户；NotificationService.notify 逐用户写 notification 行。

### Q7：指标埋点位置？
[Answer] credential_decrypt_total 在 decrypt_for_purpose（success/failed 分支）；credential_auto_paused_total 在 report_failure 达阈值置 paused 时 inc。

### Q8：密码更新审计脱敏？
[Answer] update 含密码变更时审计 after={"password_changed": true}（不记明文/密文）；与 U03 blogger quote audit 脱敏同模式。

---

## 1. 步骤

- [x] 1.1 编写 nfr-design-patterns.md（P-U12-01 create+IntegrityError→409/加密/list/get/update 不回显/pause/resume/硬删 完整伪代码 + P-U12-02 decrypt_for_purpose 审计+指标/report_failure 自动暂停+通知 best-effort/report_success 重置 完整伪代码 + 一致性校验）
- [x] 1.2 编写 logical-components.md（modules/credential 11 文件 + 横切 3 改动(metrics/wecom enums/main) + migration 016 + 依赖图无循环 + 3 测试文件）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
