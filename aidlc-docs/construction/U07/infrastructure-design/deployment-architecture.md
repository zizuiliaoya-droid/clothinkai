# U07 部署架构（Deployment Architecture）

> 单元：U07 — 企微集成基础
> 范围：U07 部署增量（环境变量注入 + 回调 URL 配置 + migration 011 + 企微后台配置）；复用 U01 部署基线

---

## 1. 部署增量 Checklist

### 1.1 环境变量（Zeabur 各服务）
- [ ] backend + celery-worker：`WECOM_API_BASE` / `WECOM_HTTP_TIMEOUT=10` / `WECOM_TOKEN_TTL=7000`
- [ ] celery-beat：`WECOM_URGE_SCAN_CRON=0 9 * * *`
- [ ] 确认 `CREDENTIAL_MASTER_KEY` 已存在三服务（U01 既有 Secret，base64 32B）
- [ ] `.env.example` 已追加 4 变量说明（开发参考）

### 1.2 数据库迁移
- [ ] 触发 migrate job（deploy 前）：`alembic upgrade head` → 应用 `011_u07_create_wecom_tables`
- [ ] 验证 5 表存在 + RLS enabled + 5 权限点 seed + 角色映射

### 1.3 回调端点
- [ ] 确认 `https://api.<domain>/api/wecom/callback/{tenant_id}` 可公开访问（中间件白名单生效，无 JWT 也能到达签名校验）
- [ ] TLS 证书有效（Zeabur Let's Encrypt）

### 1.4 Beat 调度
- [ ] celery-beat 重启后日志含 `wecom-urge-scan` 注册
- [ ] 与备份/清理调度错峰确认（09:00 vs 03:00/04:xx）

---

## 2. 企微后台配置（运维 + 租户管理员协作，一次性）

1. 企微管理后台创建自建应用 → 获取 `corp_id` / `agent_id` / `secret`。
2. 配置「接收消息」回调：URL = `https://api.<domain>/api/wecom/callback/<tenant_id>`，自定义 `Token` + `EncodingAESKey`。
3. 配置可信 IP / 客户联系功能（群发助手 add_msg_template 需开通"客户联系"）。
4. 系统内 `PUT /api/settings/wecom` 录入 corp_id/agent_id/secret/callback_token/callback_aes_key/default_sender_userid。
5. `POST /api/settings/wecom/test` 验证 access_token 获取成功。
6. 企微验证回调 URL（GET echostr）→ 系统签名校验通过并回显解密 echostr。

---

## 3. 本地开发（docker-compose）

- 复用 U01 docker-compose（6 服务）；新增 4 环境变量（带默认值，可不设）。
- 无真实企微环境：`WECOM_API_BASE` 可指向本地 mock 桩（或测试中 monkeypatch WecomClient）。
- 本地催发扫描可手动触发：`celery -A app.core.celery_app call app.tasks.wecom_tasks.scan_and_dispatch_urge`。

---

## 4. 回滚

- 代码回滚：Zeabur 多版本切换（U01 既有）。
- migration 011 downgrade：drop 5 表 + 权限 seed 回退（数据丢失，仅用于未上生产前；生产慎用，优先前向修复）。
- 回调 URL 失效不影响核心业务（消息停在 created，可重新配置后由后续回调或人工核对）。

---

## 5. 监控告警接入

- Prometheus：4 新指标（wecom_message_total / wecom_send_duration_seconds / wecom_rate_limited_total / wecom_callback_total）随 backend/worker /metrics 暴露。
- Sentry：解密失败 / 企微 API 异常 / 回调解密异常 capture。
- 告警阈值（V1 Grafana）：`wecom_message_total{status="failed"}` 突增、`wecom_callback_total{result="invalid_signature"}` 突增（疑似伪造回调）。
- U01 备份失败告警：U07 完成后可接 `NotificationService.notify(管理员)`（附带项，本单元提供能力）。

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 环境变量注入清单（三服务） | ✅ §1.1 |
| migration 011 部署步骤 | ✅ §1.2 |
| 公开回调可达 + TLS | ✅ §1.3 |
| 企微后台配置步骤完整 | ✅ §2 |
| 本地无企微环境可跑 | ✅ §3 |
| 回滚路径 | ✅ §4 |
| 4 指标 + Sentry 接入 | ✅ §5 |
