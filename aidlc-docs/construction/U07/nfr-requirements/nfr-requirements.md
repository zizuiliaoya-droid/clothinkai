# U07 非功能需求（NFR Requirements）

> 单元：U07 — 企微集成基础
> 范围：U07 特异性 NFR 增量（凭据加密 / 外部 HTTP / access_token 缓存 / 扫描+群发异步 / 频控查询 / 回调安全 / 站内通知）；通用 NFR 继承 U01-U06

---

## 1. 与基线的关系

### 1.1 完全继承
- 错误码体系 / 认证 / 授权 / 多租户 RLS 双引擎 / audit（U01）
- 监控（Prometheus + Sentry + structlog）/ 健康检查 / pytest 框架（U01）
- Celery 基线（worker + beat + default 队列 + autoretry 模式 + system_context 逐租户 SET LOCAL）（U01 + U06a）
- Redis 客户端（redis-py 5.x asyncio）（U01）

### 1.2 U07 增量
- **凭据加密落地**：AES-256-GCM + 每租户 HKDF（填充 U12 占位的 crypto.py）
- **外部 HTTP 调用**：WecomClient（httpx）超时 + token 失效重试
- **access_token 缓存**：Redis 7000s TTL
- **扫描+群发异步语义**：Beat 扫描 + 每消息独立 task/事务 + 幂等
- **频控查询**：DB 当天计数（索引保证）
- **回调安全**：公开端点 + 签名校验威胁模型
- **4 个新 Prometheus 指标**

---

## 2. 性能 NFR

### 2.1 SLA

| 路径 | 指标 | 目标 | 备注 |
|---|---|---|---|
| 配置写 + 加密 | P95 | ≤ 200ms | AESGCM 加密微秒级，主耗时 DB 写 |
| test_connection | P95 | ≤ 3s | 解密 + 企微 gettoken（外部 HTTP 10s 上限） |
| 绑定外部联系人 | P95 | ≤ 3s | 企微"获取客户列表"外部调用 |
| access_token 命中缓存 | P95 | ≤ 5ms | Redis GET |
| 群发执行（单 message） | 完成 | ≤ 12s | 频控查询 + 解密 + 企微 add_msg_template（10s 超时） |
| scan_and_dispatch_urge（单租户万级在途） | 完成 | ≤ 5s | SQL 表达式筛选 + 聚合 + 批量建 message |
| 频控当天计数查询 | P95 | ≤ 50ms | 复合索引 |
| 回调处理（含解密+校验） | P95 | ≤ 300ms | SHA1 + AES 解密 + 单条 UPDATE |
| 通知列表/未读数 | P95 | ≤ 200ms | idx(tenant_id, user_id, is_read, created_at) |

### 2.2 加密开销
- AES-256-GCM：单 secret（数十字节）加解密 < 1ms；HKDF 派生 < 1ms（每次解密派生，不缓存密钥于进程，降低泄露面）。

### 2.3 容量

| 对象 | MVP 预估 | 增长 |
|---|---|---|
| wecom_config / 租户 | 1（UNIQUE） | — |
| wecom_contact / 租户 | = 已绑定博主数（千级） | 线性 |
| message_template / 租户 | 2（urge / urge_important） | — |
| wecom_message / 租户 | 每日催发数（百级），累计千~万级 | V1 评估归档（保留 90 天明细） |
| notification / 用户 | 累计，已读可清 | V1 评估清理已读 > 30 天 |

---

## 3. 可靠性 NFR

### 3.1 异步任务失败语义

| 失败类型 | 处理 | 重试 |
|---|---|---|
| 基础设施异常（DB/Redis 断、企微网络超时） | Celery autoretry 1 次（短退避） | 任务级 autoretry=1 |
| access_token 失效（40014/42001） | 删缓存 + 刷新 + 重试一次（任务内） | 内部重试 1 次 |
| 频控命中 / 企微频控错误码 | message.status=rate_limited + 写 notification，**不重试** | 无（业务终态） |
| 企微 API 其他错误（errcode≠0） | message.status=failed + error_detail，**不重试** | 下次 Beat 扫描重新建单 |
| PR 企微端拒绝（回调） | message.status=rejected | 无 |

### 3.2 每消息独立事务
- `execute_wecom_message` 每条 message 一个 Celery task + 独立事务；单条失败不影响其他 message（与 U06a 行级隔离同理念）。

### 3.3 幂等
- 扫描幂等：同 (blogger_id, pr_id) 当天已有非 failed message → 跳过（防 Beat 重复触发，BR-U07-34）。
- 回调幂等：未知 msgid 或 message 非 created 状态 → 200 忽略（BR-U07-53/54），防企微重推放大。
- 绑定幂等：UNIQUE(tenant_id, blogger_id) 重绑覆盖。

### 3.4 worker 租户上下文
- scan + execute 在 `system_context` 内逐租户 `SET LOCAL app.tenant_id`（复用 U06a NF-1）；secret 解密在 system_context 内审计。

---

## 4. 安全 NFR

### 4.1 凭据加密威胁模型

| 威胁 | 防护 |
|---|---|
| 数据库泄露读 secret | AES-256-GCM 密文存储；master key 在 Secrets，不入库 |
| 跨租户解密 | HKDF salt=tenant_id → 每租户独立派生密钥，A 租户密钥无法解 B 密文 |
| 密文篡改 | GCM 认证标签（tag）解密时校验，篡改 → 解密失败（5xx，不静默） |
| secret 明文泄露（响应/日志） | Schema 永不回显（仅 secret_configured: bool）；structlog redact；明文仅在内存发送瞬间 |
| 解密滥用 | 解密仅 system_context（Celery）+ `@audit("wecom.secret.decrypt")` |

### 4.2 回调端点安全（公开无 JWT）

| 威胁 | 防护 |
|---|---|
| 伪造回调 | msg_signature = sha1(sorted(token, timestamp, nonce, encrypt)) 校验，失败 403 |
| 重放放大 | 幂等：未知/已处理 msgid → 200 忽略 |
| 跨租户串扰 | tenant 路由按 corp_id/回调路径反查；解密后只更新本租户 message |
| 可疑请求溯源 | 签名失败 `@audit("wecom.callback.invalid_signature")` 记 IP + 报文摘要 |

### 4.3 权限
- `wecom.config:write` / `wecom.bind:write` / `wecom.template:write` / `wecom.message:read` / `notification:read`
- 同租户协作不限本人；通知查询限本人（user_id = current_user）
- U09 后切字段级权限

### 4.4 审计
- 配置更新 / 绑定 / 模板编辑 / secret 解密 / 回调签名失败 写 audit_log，脱敏（不记 secret/access_token 明文）

---

## 5. 可观测性 NFR

### 5.1 Prometheus 指标（4 新增，core/metrics.py 扩展）

| 指标 | 类型 | 标签 | 说明 |
|---|---|---|---|
| `wecom_message_total` | Counter | status | 消息终态计数（created/sent/rejected/rate_limited/failed） |
| `wecom_send_duration_seconds` | Histogram | — | 企微 add_msg_template 调用耗时 |
| `wecom_rate_limited_total` | Counter | reason | 频控降级计数（blogger/pr/api） |
| `wecom_callback_total` | Counter | result | 回调结果（sent/rejected/failed/invalid_signature/ignored） |

### 5.2 日志 / 追踪
- structlog 记 tenant_id / message_id / blogger_id / status（不记 secret/token/external_userid 明文，external_userid 可记尾部脱敏）
- Sentry：解密失败 / 企微 API 异常 / 回调解密异常
- 告警阈值（V1 Grafana）：`wecom_message_total{status="failed"}` 突增 / `wecom_callback_total{result="invalid_signature"}` 突增

---

## 6. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | crypto round-trip（加密→解密一致 + 跨租户密钥不可解 + 篡改 tag 失败）/ 模板变量白名单校验 / WecomMessage 状态机 / 模板渲染 / 频控判定纯函数 |
| 集成 | 配置加密落库不回显 / 绑定（匹配/未匹配 404/无微信 422）/ 扫描聚合（含未绑定跳过 + 幂等）/ 执行（正常 created / 博主频控 rate_limited+notification / PR 频控）/ 回调（签名通过 sent + 签名失败 403 + 未知 msgid 忽略）/ 跨租户隔离 |
| API | 鉴权 / OpenAPI / secret 不回显 |
| 异步 | 同步调用 scan/execute（不经 broker）；WecomClient monkeypatch mock |
| 覆盖率 | service ≥ 80% / domain ≥ 90% / api ≥ 60%（继承基线） |

> 无真实企微环境：WecomClient 全程 mock；crypto 真实跑（验证安全语义）。

---

## 7. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP08-S02 配置 | secret AES-256-GCM 加密落库 + 不回显 + test_connection ≤ 3s |
| EP08-S03 绑定 | 外部调用 ≤ 3s + 未匹配 404 |
| EP08-S04 模板 | 变量白名单校验 422 |
| EP08-S05 扫描 | 单租户万级 ≤ 5s + 幂等 + 逐租户 RLS |
| EP08-S06 群发 | 单消息独立事务 ≤ 12s + token 缓存命中 ≤ 5ms |
| EP08-S07 频控降级 | 当天计数 ≤ 50ms + rate_limited + notification |
| EP08-S08 回调 | 签名校验 403 + 幂等 + ≤ 300ms |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 凭据加密算法 + 每租户密钥派生量化 | ✅ §4.1 + AES-256-GCM/HKDF |
| 外部 HTTP 超时 + token 重试语义 | ✅ §3.1 |
| 每消息独立事务 + 失败语义 | ✅ §3.1/3.2 |
| 幂等（扫描/回调/绑定） | ✅ §3.3 |
| 回调安全威胁模型（公开端点） | ✅ §4.2 |
| 4 个 Prometheus 指标 | ✅ §5.1 |
| 无新增运行时依赖（httpx/cryptography 已 pin） | ✅ tech-stack-decisions |
| 测试无需真实企微（mock + crypto 真测） | ✅ §6 |
