# U07 API 端点（企微集成基础）

> 单元：U07 — 企微集成基础

---

## 1. 配置（EP08-S02）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| PUT | /api/settings/wecom | wecom.config:write | 配置自建应用（secret AESGCM 加密，不回显） |
| GET | /api/settings/wecom | wecom.config:write | 读配置（secret_configured: bool，无明文） |
| POST | /api/settings/wecom/test | wecom.config:write | 测试连接（业务结果 ok:true/false，不抛 5xx） |

**PUT body**：`{corp_id, agent_id, secret, callback_token?, callback_aes_key?, default_sender_userid?, is_active}`
**响应**：`{corp_id, agent_id, secret_configured, callback_token?, default_sender_userid?, is_active}`

---

## 2. 绑定（EP08-S03）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | /api/bloggers/{blogger_id}/wecom-bind | wecom.bind:write | 绑定外部联系人 |

错误：无微信 422 / 未匹配 404 / 未配置 409。响应 `{blogger_id, external_userid, bound_at}`。

---

## 3. 模板（EP08-S04）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| PUT | /api/settings/templates/{template_type} | wecom.template:write | 编辑模板（白名单校验 422） |
| GET | /api/settings/templates/{template_type} | wecom.template:write | 读模板 |

`template_type` ∈ {urge, urge_important}；非法 422。

---

## 4. 消息记录（EP08-S06）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | /api/wecom/messages?limit&offset | wecom.message:read | 消息记录列表（按 created_at desc） |

---

## 5. 回调（EP08-S08，公开无 JWT）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /api/wecom/callback/{tenant_id} | URL 验证（msg_signature+timestamp+nonce+echostr → 回显解密 echostr） |
| POST | /api/wecom/callback/{tenant_id} | 接收回调（body.encrypt；签名失败 403+audit；幂等推进 status） |

---

## 6. 站内通知（EP08-S07 支撑）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | /api/notifications?unread_only&limit&offset | notification:read | 本人通知列表 |
| GET | /api/notifications/unread-count | notification:read | 未读数 |
| POST | /api/notifications/{id}/read | notification:read | 标记已读（限本人，否则 404） |

---

## 7. 权限矩阵（migration 011 seed）

| 权限 | admin | pr | pr_manager | operations | finance/merchandiser |
|---|---|---|---|---|---|
| wecom.config:write | ✅(*) | | | | |
| wecom.bind:write | ✅(*) | ✅ | ✅ | | |
| wecom.template:write | ✅(*) | | | | |
| wecom.message:read | ✅(*) | ✅ | ✅ | ✅ | |
| notification:read | ✅(*) | ✅ | ✅ | ✅ | ✅ |
