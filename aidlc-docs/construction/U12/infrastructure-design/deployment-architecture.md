# U12 部署架构（Deployment Architecture）

> 单元：U12 — 平台凭据 + 采集失败告警

---

## 1. 拓扑变更

**无变更**——U12 完全运行在既有 backend 服务内，不引入新服务/worker/beat。

```
[既有拓扑不变]
frontend → backend ──┬─ PostgreSQL（+credential 表）
                     ├─ Redis（无新库）
                     └─ R2（无新桶）
                  └─ NotificationService（U07，失败告警）
```

---

## 2. 部署 Checklist

- [ ] 合并代码（modules/credential + 3 横切改动）到 main
- [ ] migrate job 执行 alembic upgrade head（含 016）
- [ ] 确认 CREDENTIAL_MASTER_KEY 已在 Zeabur Secrets 配置（U01/U07 已配，复查）
- [ ] backend 服务重启加载新 router（/api/credentials）
- [ ] 验证 /api/openapi.json 暴露 credential 端点

---

## 3. 验证步骤（部署后）

| # | 验证项 | 期望 |
|---|---|---|
| 1 | credential 表存在 | `\d credential` 显示 13 列 + UNIQUE + idx + CHECK |
| 2 | 3 scope seed | permission 表含 credential:read/write/delete；admin 绑 3 / operations 绑 read |
| 3 | RLS 生效 | credential 表 rowsecurity=true |
| 4 | 加密往返 | 创建凭据后 DB 中 password_ciphertext 为密文；解密还原明文一致 |
| 5 | 不可回显 | GET /api/credentials/{id} 响应不含 password/password_ciphertext |
| 6 | 解密审计 | decrypt_for_purpose 后 audit_log 有 credential.decrypt 记录（含 purpose/platform） |
| 7 | 连续失败自动暂停 | report_failure ×3 后 status=paused + 企微通知 admin |
| 8 | 多租户隔离 | A 租户列表不含 B；A 密钥解 B 密文 → CredentialDecryptError |
| 9 | 隐私未确认 | privacy_consent=false → 422 |
| 10 | 重复凭据 | 同 (platform, username) → 409 |

---

## 4. 监控

| 项 | 说明 |
|---|---|
| credential_decrypt_total | Counter（platform, result）——解密成功/失败率 |
| credential_auto_paused_total | Counter（platform）——自动暂停告警频率 |
| Sentry | 解密失败（CredentialDecryptError）capture |
| 企微告警 | 连续失败自动暂停 → NotificationType.CREDENTIAL_FAILURE → admin |

---

## 5. 回滚

| 步骤 | 命令 |
|---|---|
| 1. DB 回滚 | alembic downgrade 015（DROP credential 表 + DELETE 3 scope） |
| 2. 代码回滚 | Zeabur 切回上一版本镜像 |
| 风险 | 极低——新表无下游依赖（U13 尚未实施） |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 拓扑无变更 | ✅ |
| Checklist + 10 验证步骤 | ✅ |
| 监控 2 指标 + Sentry + 企微 | ✅ |
| 回滚安全 | ✅ |
| 本地 Docker 5555/6410 | ✅ |
