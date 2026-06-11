# U07 NFR 需求计划（NFR Requirements Plan）

> 单元：U07 — 企微集成基础
> 范围：U07 特异性 NFR 增量（凭据加密 / 外部 HTTP 调用 / access_token 缓存 / 扫描+群发异步语义 / 频控查询 / 回调安全 / 站内通知）；通用 NFR 全部继承 U01-U06
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档（nfr-requirements.md + tech-stack-decisions.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — crypto 加密算法与密钥派生（U07 落地）
- [Answer] **AES-256-GCM**（`cryptography` 库 AESGCM，已 pin 43.0.1）；每租户密钥 = **HKDF-SHA256**(master=CREDENTIAL_MASTER_KEY base64 解码 32B, salt=tenant_id.bytes, info=b"wecom-credential")；密文格式 = `nonce(12B) || ciphertext || tag(16B)` 存 bytea。无新增依赖。

### Q2 — access_token 缓存策略
- [Answer] Redis `wecom:token:{tenant_id}`，`SET EX 7000`（企微 7200 留 200s 余量）；errcode 40014/42001（token 失效）→ 删缓存 + 刷新 + 重试一次。并发刷新可接受（企微 gettoken 幂等返回当前有效 token）。

### Q3 — WecomClient HTTP 超时/重试
- [Answer] httpx 同步/异步均可（Celery 内用同步 httpx.Client）；connect+read 超时 10s；仅 token 失效错误码重试一次；网络异常不自动重试（交由消息 status=failed + 用户/Beat 下次扫描）。

### Q4 — 扫描+群发异步语义
- [Answer] `scan_and_dispatch_urge`（Beat 09:00，default 队列）逐租户事务；`execute_wecom_message`（每 message 一个 task，default 队列）每条独立事务。任务级 autoretry：基础设施异常 autoretry=1；业务结果（频控/拒绝/API 错误）不重试，落 message.status。

### Q5 — 频控查询性能
- [Answer] DB 当天计数走复合索引 `idx(tenant_id, blogger_id, created_at)` 与 `idx(tenant_id, pr_id, created_at)`，单查询 ≤ 50ms；当天范围用 `created_at >= 当天0点(Asia/Shanghai 转 UTC)`。

### Q6 — 回调端点安全（公开无 JWT）
- [Answer] 回调 `/api/wecom/callback` 公开；防护 = msg_signature SHA1 校验 + AES 解密（EncodingAESKey）+ tenant 路由（按 corp_id/回调路径标识反查租户）；签名失败 403 + audit；不做 IP 白名单（企微出口 IP 不固定）；幂等忽略未知/已处理 msgid。

### Q7 — 新增 Prometheus 指标
- [Answer] 4 个：`wecom_message_total{source_type,status}`（消息终态计数）、`wecom_send_duration_seconds`（企微 API 调用耗时 Histogram）、`wecom_rate_limited_total`（频控降级计数）、`wecom_callback_total{result}`（回调结果计数，含 invalid_signature）。

### Q8 — 新增配置项
- [Answer] `WECOM_URGE_SCAN_CRON`（默认 "0 9 * * *"）、`WECOM_API_BASE`（默认 https://qyapi.weixin.qq.com）、`WECOM_HTTP_TIMEOUT`（默认 10）、`WECOM_TOKEN_TTL`（默认 7000）。`CREDENTIAL_MASTER_KEY` 已存在（复用）。无新增第三方依赖。

### Q9 — 测试策略（无真实企微环境）
- [Answer] WecomClient 全程 monkeypatch mock；crypto round-trip 真实测（加密→解密一致 + 跨租户密钥不可解）；扫描/执行/频控/回调签名用真实 DB + mock client；CI 不需企微凭据。

---

## 2. 执行步骤

- [x] 2.1 `U07/nfr-requirements/nfr-requirements.md`：性能（加密/token/发送/扫描/频控 SLA）+ 可靠性（异步语义 + 每消息事务 + 幂等）+ 安全（凭据加密威胁模型 + 回调签名 + secret 不回显）+ 可观测（4 指标）+ 测试 + 故事 NFR 映射 + 一致性校验
- [x] 2.2 `U07/nfr-requirements/tech-stack-decisions.md`：复用依赖确认（httpx/cryptography 已 pin，无新增）+ AESGCM+HKDF 代码片段 + access_token 缓存片段 + WecomClient 骨架 + 4 配置项 + Celery Beat 注册 + 环境变量清单
- [x] 2.3 诊断器无警告 + 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 需求文档。**
