# U07 业务规则（企微集成基础）

> 单元：U07 — 企微集成基础
> 覆盖故事：EP08-S02 ~ S08
> 编号：BR-U07-NN

---

## 1. 配置与凭据安全（EP08-S02）

- **BR-U07-01**：`PUT /api/settings/wecom` 写入 corp_id / agent_id / secret / callback_token / callback_aes_key / default_sender_userid。secret 经 `encrypt_credential(tenant_id, secret)` 加密存 `secret_ciphertext`，明文不落库不落日志。
- **BR-U07-02**：任何读取响应（`GET /api/settings/wecom`）**不回显** secret 明文，仅返回 `secret_configured: true|false`（密文是否存在）。
- **BR-U07-03**：secret 解密仅在发送链路（Celery `system_context`）按需进行，且必经 `@audit("wecom.secret.decrypt")`（actor_type=system）。
- **BR-U07-04**：`POST /api/settings/wecom/test` 用当前配置调 `get_access_token`，成功返回 `{ok: true}`，失败返回 `{ok: false, reason}`（不抛 5xx，连接性测试属业务结果）。
- **BR-U07-05**：`secret_ciphertext` 解密失败（密钥不匹配 / 篡改）→ 系统错误（5xx + Sentry），不可静默当作空 secret。

## 2. 外部联系人绑定（EP08-S03）

- **BR-U07-10**：`POST /api/bloggers/{id}/wecom-bind` 读取 blogger.wechat，调企微"获取客户列表"匹配 external_userid，写入 `wecom_contact`（UNIQUE(tenant_id, blogger_id)，重绑覆盖 + 更新 bound_at/bound_by）。
- **BR-U07-11**：blogger 无 wechat → 422「该博主未填写微信号」。
- **BR-U07-12**：微信号未在企微外部联系人中匹配到 → 404「请先在企微端添加该联系人」。
- **BR-U07-13**：未配置/未启用 wecom_config → 409「请先配置企微应用」。

## 3. 催发模板（EP08-S04）

- **BR-U07-20**：`PUT /api/settings/templates/urge`（及 urge_important）按 `template_type` upsert `message_template`（UNIQUE(tenant_id, template_type)）。
- **BR-U07-21**：模板变量白名单 = {博主昵称} {商品简称} {预定发布日期} {剩余天数}。保存前正则提取 `\{([^}]+)\}`，任一不在白名单 → 422，响应列出全部非法变量。
- **BR-U07-22**：渲染时变量值缺失（如 scheduled_publish_date 为空）以空串替换，不报错。
- **BR-U07-23**：缺省模板：系统初始化 seed 两条默认模板（urge / urge_important）；超时态复用 urge_important。

## 4. 自动催发扫描（EP08-S05）

- **BR-U07-30**：`scan_and_dispatch_urge` 由 Celery Beat 每天 09:00（`Asia/Shanghai`，`get_today()` 统一日期）触发，遍历所有启用 wecom_config 的租户（system_context 内逐租户 SET LOCAL）。
- **BR-U07-31**：候选推广筛选 = `urge_status ∈ {催发, 重要催发, 超时}` 且 `publish_status ∈ {未发布, 异常}` 且未取消未删除（复用 `URGE_STATUS_SQL_EXPR` + 阈值 10/3）。
- **BR-U07-32**：按 `(blogger_id, pr_id)` 聚合为一条 `wecom_message(status=pending)`：`promotion_ids` 存该组全部推广 id；`template_type` = 组内最紧急（含 重要催发/超时 → urge_important，否则 urge）。
- **BR-U07-33**：无对应 `wecom_contact`（博主未绑定）的组 → 跳过 + 写 notification 给 pr「博主 {昵称} 未绑定企微，无法自动催发」（不创建 pending message）。
- **BR-U07-34**：扫描幂等：同一 (blogger_id, pr_id) 当天已存在非 failed 的 message → 跳过（防 Beat 重复触发重复建单）。
- **BR-U07-35**：每条 pending message 创建后投递 `execute_wecom_message.delay(message_id)`。

## 5. 群发执行与频控降级（EP08-S06, S07）

- **BR-U07-40**：`execute_wecom_message` 每条 message 独立事务；先做频控判定再决定发送或降级。
- **BR-U07-41**：博主频控 = 当天该 blogger_id 已有 status ∈ {created, sent} 的 message ≥ 1 → 命中。
- **BR-U07-42**：PR 频控 = 当天该 pr_id 已有 status ∈ {created, sent} 的 message ≥ 1 → 命中（每 PR 每天 1 次群发）。
- **BR-U07-43**：命中任一频控 → `status=rate_limited` + `error_detail` 记原因 + `NotificationService.notify(pr_id, "请手动催发 {博主昵称}")`，**不调企微 API**。
- **BR-U07-44**：未命中 → 渲染模板 → 解密 secret/取 access_token → `WecomClient.send_external_msg_template(sender=default_sender_userid, recipients=[external_userid], content)` → 成功 `status=created` + 记 `wecom_msgid`。
- **BR-U07-45**：企微 API 失败（网络/错误码非频控类）→ `status=failed` + error_detail；token 失效（errcode 40014/42001）→ 刷新 token 重试一次。
- **BR-U07-46**：企微返回频控类错误码 → 视同频控命中（BR-U07-43 降级处理）。

## 6. 回调更新状态（EP08-S08）

- **BR-U07-50**：回调 URL 验证（GET）：解密 echostr 并原样回显（企微配置回调时校验）。
- **BR-U07-51**：消息回调（POST）签名校验 `msg_signature == sha1(sorted(token, timestamp, nonce, encrypt))`；不匹配 → 403 + `@audit("wecom.callback.invalid_signature")` 记 IP/原始报文摘要。
- **BR-U07-52**：签名通过 → 解密载荷取 (msgid, result) → 按 result 推进 `wecom_message.status`：成功→sent(+sent_at)、拒绝→rejected、失败→failed。
- **BR-U07-53**：回调中 msgid 未匹配到本租户 message → 200 忽略（幂等，不报错，防重放放大）。
- **BR-U07-54**：仅允许从 created 推进到 sent/rejected/failed；非 created 状态收到回调 → 200 忽略（幂等）。

## 7. access_token 与外部调用

- **BR-U07-60**：access_token 缓存于 Redis `wecom:token:{tenant_id}`，TTL 7000s；命中直接用，未命中调 `gettoken` 刷新。
- **BR-U07-61**：所有企微外部调用经 `WecomClient`（httpx），超时 10s，errcode≠0 抛 `WecomApiError(errcode, errmsg)`。

## 8. 通知（EP08-S07 支撑）

- **BR-U07-70**：`NotificationService.notify(user_ids, content, link=None, type="urge_manual")` 为每个 user 写一条 notification（tenant 内）。
- **BR-U07-71**：`GET /api/notifications`（分页）+ `GET /api/notifications/unread-count` + `POST /api/notifications/{id}/read`，均限本人（user_id = current_user）。

## 9. 权限与多租户

- **BR-U07-80**：权限点 `wecom.config:write`（配置）、`wecom.bind:write`（绑定）、`wecom.template:write`（模板）、`wecom.message:read`（消息查询）、`notification:read`（本人通知）。默认角色：管理员含全部；PR 含 bind:write + message:read + notification:read。
- **BR-U07-81**：所有表 tenant_id 经 RLS + ORM 钩子隔离；Celery 扫描/执行在 `system_context` 内逐租户 `SET LOCAL app.tenant_id`（复用 U06a NF-1 模式）。
- **BR-U07-82**：回调端点为公开（无 JWT），仅靠签名校验 + tenant 路由（回调 URL 含 tenant 标识或按 corp_id 反查租户）。

---

## 10. 错误码矩阵

| 场景 | HTTP | 错误码 |
|---|---|---|
| 博主无微信号 | 422 | WECOM_BLOGGER_NO_WECHAT |
| 外部联系人未匹配 | 404 | WECOM_CONTACT_NOT_FOUND |
| 未配置企微应用 | 409 | WECOM_NOT_CONFIGURED |
| 模板非法变量 | 422 | WECOM_TEMPLATE_INVALID_VAR |
| 回调签名失败 | 403 | WECOM_CALLBACK_BAD_SIGNATURE |
| 企微 API 错误 | 502 | WECOM_API_ERROR |
| secret 解密失败 | 500 | WECOM_DECRYPT_FAILED |

---

## 11. 性能与容量

- 扫描任务：单租户万级在途推广，SQL 表达式筛选 + 聚合，P95 ≤ 2s；按租户分批。
- 群发执行：每 message 独立 Celery task，外部调用 10s 超时；失败不阻塞其他 message。
- 频控统计：命中 `idx(tenant_id, blogger_id, created_at)` / `idx(tenant_id, pr_id, created_at)`，单查询 ≤ 50ms。
