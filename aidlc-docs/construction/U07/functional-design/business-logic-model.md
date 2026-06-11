# U07 业务逻辑模型（企微集成基础）

> 单元：U07 — 企微集成基础
> 覆盖故事：EP08-S02 ~ S08
> 7 个用例 + 端到端时序 + 跨单元契约

---

## UC-1 配置企微应用 + 测试连接（EP08-S02）

```
[管理员] PUT /api/settings/wecom {corp_id, agent_id, secret, callback_token, callback_aes_key, default_sender_userid}
  → @require_permission("wecom.config:write")
  → WecomConfigService.configure(payload)
        ├── secret_ciphertext = crypto.encrypt_credential(tenant_id, payload.secret)   # BR-U07-01
        ├── upsert wecom_config（UNIQUE tenant_id）
        └── audit("wecom.config.update")（不含 secret 明文）
  → 200 {corp_id, agent_id, secret_configured: true, is_active}                          # BR-U07-02

[管理员] POST /api/settings/wecom/test
  → WecomConfigService.test_connection()
        ├── token = WecomClient.get_access_token()   # 解密 secret → gettoken          # BR-U07-04
        └── return {ok: true} | {ok: false, reason}
```

---

## UC-2 博主外部联系人绑定（EP08-S03）

```
[PR] POST /api/bloggers/{id}/wecom-bind
  → @require_permission("wecom.bind:write")
  → WecomService.bind_contact(blogger_id)
        ├── cfg = require active wecom_config            else 409 WECOM_NOT_CONFIGURED  # BR-U07-13
        ├── blogger = get; blogger.wechat else 422 WECOM_BLOGGER_NO_WECHAT              # BR-U07-11
        ├── external_userid = WecomClient.find_external_userid_by_wechat(blogger.wechat)
        │     └── None → 404 WECOM_CONTACT_NOT_FOUND                                    # BR-U07-12
        └── upsert wecom_contact(blogger_id, external_userid, matched_wechat, bound_by, bound_at)
  → 200 {blogger_id, external_userid, bound_at}
```

---

## UC-3 编辑催发模板（EP08-S04）

```
[管理员] PUT /api/settings/templates/urge  {content}
  → @require_permission("wecom.template:write")
  → MessageTemplateService.upsert("urge", content)
        ├── vars = regex_extract("\{([^}]+)\}", content)
        ├── illegal = vars - {博主昵称, 商品简称, 预定发布日期, 剩余天数}
        │     └── illegal 非空 → 422 WECOM_TEMPLATE_INVALID_VAR {invalid: [...]}        # BR-U07-21
        └── upsert message_template(UNIQUE tenant_id, template_type)
  → 200 {template_type, content}
```

---

## UC-4 自动催发扫描（EP08-S05，Celery Beat 09:00）

```
[Celery Beat] scan_and_dispatch_urge()
  for each tenant with active wecom_config:                                              # BR-U07-30
    system_context + SET LOCAL app.tenant_id
    today = get_today()
    candidates = PromotionRepository.find_urge_candidates(today, urge=10, important=3)   # BR-U07-31
        # WHERE urge_status ∈ {催发,重要催发,超时} AND publish_status ∈ {未发布,异常}
    groups = group_by (blogger_id, pr_id)                                                # BR-U07-32
    for (blogger_id, pr_id), promos in groups:
        if exists non-failed message today for (blogger_id, pr_id): continue             # BR-U07-34
        contact = wecom_contact[blogger_id]
        if contact is None:
            NotificationService.notify(pr_id, "博主未绑定企微，无法自动催发"); continue   # BR-U07-33
        template_type = "urge_important" if any(重要催发/超时) else "urge"
        msg = WecomMessageRepository.create(
            blogger_id, pr_id, external_userid=contact.external_userid,
            template_type, rendered_content=render(template, blogger, promo),
            promotion_ids=[p.id...], status="pending")
        execute_wecom_message.delay(msg.id)                                              # BR-U07-35
```

---

## UC-5 群发执行 + 频控降级（EP08-S06, S07，Celery Worker）

```
[Celery Worker] execute_wecom_message(message_id)                                        # BR-U07-40
  system_context + SET LOCAL (msg.tenant_id)
  msg = get(message_id); guard status == "pending"
  blogger_hit = count(today, blogger_id, status∈{created,sent}) >= 1                     # BR-U07-41
  pr_hit      = count(today, pr_id,      status∈{created,sent}) >= 1                      # BR-U07-42
  if blogger_hit or pr_hit:
      msg.status = "rate_limited"; msg.error_detail = "频控降级"
      NotificationService.notify(pr_id, f"请手动催发 {blogger.nickname}")                # BR-U07-43
      commit; return
  else:
      content = msg.rendered_content
      token = WecomClient.get_access_token()      # Redis 缓存 7000s / 解密 secret       # BR-U07-60
      try:
          resp = WecomClient.send_external_msg_template(
              sender=cfg.default_sender_userid, recipients=[msg.external_userid], content)# BR-U07-44
          msg.wecom_msgid = resp["msgid"]; msg.status = "created"
      except WecomRateLimited:
          → 同频控降级路径                                                                # BR-U07-46
      except WecomTokenExpired:
          refresh token; retry once                                                      # BR-U07-45
      except WecomApiError as e:
          msg.status = "failed"; msg.error_detail = str(e)                               # BR-U07-45
      commit
```

---

## UC-6 企微回调更新状态（EP08-S08）

```
[企微] GET /api/wecom/callback?msg_signature&timestamp&nonce&echostr
  → verify_url(): sha1 校验 + 解密 echostr 回显                                          # BR-U07-50

[企微] POST /api/wecom/callback?msg_signature&timestamp&nonce  (body=encrypted)
  → tenant 路由（按 corp_id/回调标识反查租户）                                           # BR-U07-82
  → if sha1(sorted(token,timestamp,nonce,encrypt)) != msg_signature:
        audit("wecom.callback.invalid_signature"); return 403                            # BR-U07-51
  → (msgid, result) = decrypt(body)
  → msg = WecomMessageRepository.find_by_wecom_msgid(msgid)
        └── None or status != "created" → 200 忽略（幂等）                               # BR-U07-53/54
  → handle_callback: status = {success→sent(+sent_at), reject→rejected, fail→failed}     # BR-U07-52
  → 200
```

---

## UC-7 站内通知查询（EP08-S07 支撑）

```
[用户] GET /api/notifications?page&unread_only        → 本人通知分页                     # BR-U07-71
[用户] GET /api/notifications/unread-count            → {count}
[用户] POST /api/notifications/{id}/read              → is_read=true（限本人）
```

---

## 端到端时序（Journey J2 催发闭环）

```
推广临近发布(urge_status=催发)
  → [09:00 Beat] scan_and_dispatch_urge → wecom_message(pending) → execute.delay
    → [Worker] 未频控 → 企微 add_msg_template → created
    → [PR 企微端确认] → 企微回调 → POST /wecom/callback → sent
  ── 或博主当天已发 1 条 → rate_limited → notification「请手动催发」→ PR 看站内通知手动联系
```

---

## 跨单元契约

| 契约 | 提供方 | 使用 |
|---|---|---|
| `find_urge_candidates(today, urge_days, important_days)` | U04 PromotionRepository（U07 补该查询方法） | 扫描候选 |
| `URGE_STATUS_SQL_EXPR` / `get_today` | U04 urge_calculator | 一致性筛选 |
| `encrypt_credential/decrypt_credential` | U01 crypto（U07 落地实现） | secret |
| `system_context` + 双引擎 | U01 | Celery 逐租户 |
| NotificationService | U07 新建（共用） | U15/未来设计模块复用 |

### 演化
- U15：复用 wecom_config + WecomClient 增群机器人 webhook（发文通知 EP08-S09 / 异常预警 EP08-S10）。
- U01 备份失败告警：完成后接 `push_to_admins`（NotificationService + 可选企微）。

---

## 故事覆盖校验

| 故事 | UC | 状态 |
|---|---|---|
| EP08-S02 | UC-1 | ✅ |
| EP08-S03 | UC-2 | ✅ |
| EP08-S04 | UC-3 | ✅ |
| EP08-S05 | UC-4 | ✅ |
| EP08-S06 | UC-5 | ✅ |
| EP08-S07 | UC-5 + UC-7 | ✅ |
| EP08-S08 | UC-6 | ✅ |
