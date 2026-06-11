# U07 逻辑组件（Logical Components）

> 单元：U07 — 企微集成基础
> 范围：U07 新增/修改组件清单 + 依赖图 + 注册序列

---

## 1. 组件清单

### 1.1 新建 — modules/wecom/

| 文件 | 职责 | 关键内容 |
|---|---|---|
| `__init__.py` | 包标识 | — |
| `enums.py` | 枚举 | WecomMessageStatus(6) / TemplateType(urge/urge_important) / NotificationType |
| `exceptions.py` | 异常 | WecomApiError / WecomRateLimited / WecomTokenExpired / WecomNotConfigured / WecomContactNotFound / WecomTemplateInvalidVar / WecomCallbackBadSignature / CredentialDecryptError |
| `permissions.py` | 权限点 | wecom.config:write / wecom.bind:write / wecom.template:write / wecom.message:read / notification:read |
| `models.py` | ORM | WecomConfig / WecomContact / MessageTemplate / WecomMessage / Notification |
| `schemas.py` | Pydantic | WecomConfigUpdate / WecomConfigResponse(secret_configured) / BindResponse / TemplateUpdate / WecomMessageResponse / NotificationResponse |
| `repository.py` | 仓储 | WecomConfigRepository / WecomContactRepository / MessageTemplateRepository / WecomMessageRepository（create_message / exists_today_non_failed / count_today / find_by_msgid / get_contact）/ NotificationRepository |
| `domain.py` | 纯函数 | render_template(content, ctx)（4 白名单变量）/ extract_template_vars / validate_template_vars / is_important |
| `client.py` | 企微 SDK | WecomClient（async httpx：get_access_token + find_external_userid_by_wechat + send_external_msg_template）+ WecomCrypto（回调签名 verify + decrypt，EncodingAESKey AES-CBC） |
| `config_service.py` | 配置编排 | WecomConfigService（configure 加密 / test_connection / get(secret 不回显)） |
| `bind_service.py` | 绑定编排 | WecomBindService（bind_contact：匹配 external_userid → upsert wecom_contact） |
| `template_service.py` | 模板编排 | MessageTemplateService（upsert + 变量白名单校验 + seed 默认） |
| `scan_service.py` | 扫描编排 | WecomScanService（scan_tenant：候选→聚合→建 message→delay） |
| `send_service.py` | 发送编排 | WecomSendService（send：频控判定→发送/降级） |
| `callback_service.py` | 回调编排 | WecomCallbackService（verify + handle_callback 幂等推进） |
| `notification_service.py` | 通知 | NotificationService（notify / unread_count / mark_read / list） |
| `deps.py` | DI | get_wecom_config_service / get_bind_service / ... + 权限依赖 |
| `api.py` | HTTP | 配置（PUT/GET /settings/wecom + POST /settings/wecom/test）/ 绑定（POST /bloggers/{id}/wecom-bind）/ 模板（PUT/GET /settings/templates/{type}）/ 消息查询（GET /wecom/messages） |
| `callback_api.py` | 公开回调 | GET/POST /api/wecom/callback/{tenant_id}（无 JWT，签名校验） |
| `notification_api.py` | 通知 HTTP | GET /api/notifications + /unread-count + POST /{id}/read |

### 1.2 新建 — app/tasks/

| 文件 | 职责 |
|---|---|
| `wecom_tasks.py` | scan_and_dispatch_urge（Beat）+ execute_wecom_message（每消息）；asyncio.run 入口 + worker SET LOCAL |

### 1.3 修改 — 横切

| 文件 | 改动 |
|---|---|
| `core/security/crypto.py` | 落地 encrypt_credential / decrypt_credential（AESGCM+HKDF）；rotate 仍占位；新增 CredentialDecryptError |
| `core/config.py` | +WECOM_API_BASE / WECOM_HTTP_TIMEOUT / WECOM_TOKEN_TTL / WECOM_URGE_SCAN_CRON（均带默认） |
| `core/metrics.py` | +4 指标（wecom_message_total / wecom_send_duration_seconds / wecom_rate_limited_total / wecom_callback_total） |
| `core/celery_app.py` | autodiscover +"app.tasks.wecom_tasks"；beat_schedule +wecom-urge-scan（09:00） |
| `main.py` | 注册 wecom_router + callback_router + notification_router；seed 默认模板（lifespan，可选） |
| `modules/promotion/repository.py` | +find_urge_candidates(today, urge_days, important_days)（复用 URGE_STATUS_SQL_EXPR） |
| `.env.example` | +4 WECOM 变量说明 |

### 1.4 新建 — migration

| 文件 | 内容 |
|---|---|
| `alembic/versions/011_u07_create_wecom_tables.py` | 5 表（wecom_config / wecom_contact / message_template / wecom_message / notification）+ 索引（频控复合索引 + 通知索引）+ UNIQUE（config tenant / contact tenant+blogger / template tenant+type）+ 3 RLS enable + 权限 seed（5 权限点 + 角色映射） |

---

## 2. 依赖图

```
api / callback_api / notification_api
   └─→ config_service / bind_service / template_service / notification_service
           └─→ repository（WecomConfig/Contact/Template/Message/Notification）
           └─→ client（WecomClient + WecomCrypto）─→ 企微 API [HTTPS]
           └─→ crypto.encrypt/decrypt_credential（core）
tasks/wecom_tasks
   ├─→ scan_service ─→ PromotionRepository.find_urge_candidates（U04）
   │                 └─→ repository.create_message + notification_service
   └─→ send_service ─→ repository.count_today + client.send + notification_service
domain（render_template / validate_vars）← scan_service / template_service
```

依赖方向：api → service → {repository, client, domain, core}；tasks → service；service 不反向依赖 api。无循环。

---

## 3. 注册序列

| 时机 | 动作 |
|---|---|
| import time | models 注册到 Base.metadata（conftest 导入保证测试建表） |
| lifespan（HTTP） | include_router(wecom / callback / notification)；seed 默认模板（若无） |
| Celery Beat | wecom-urge-scan（09:00）→ scan_and_dispatch_urge |
| Celery Worker | execute_wecom_message 由 scan .delay() 投递（autodiscover 注册） |

---

## 4. 索引设计（migration 011）

| 表 | 索引 | 用途 |
|---|---|---|
| wecom_config | UNIQUE(tenant_id) | 单租户单条 |
| wecom_contact | UNIQUE(tenant_id, blogger_id) + idx(tenant_id, external_userid) | 绑定唯一 + 反查 |
| message_template | UNIQUE(tenant_id, template_type) | 类型唯一 |
| wecom_message | idx(tenant_id, blogger_id, created_at) + idx(tenant_id, pr_id, created_at) + idx(tenant_id, status) + idx(wecom_msgid) | 频控统计 + 回调反查 |
| notification | idx(tenant_id, user_id, is_read, created_at) | 本人未读列表 |

RLS：wecom_config / wecom_contact / message_template / wecom_message / notification 全部 enable_rls（复用 U01 rls.enable_rls_sql）。

---

## 5. 测试组件

| 测试文件 | 覆盖 |
|---|---|
| `tests/unit/test_crypto_wecom.py` | encrypt/decrypt round-trip + 跨租户不可解 + tag 篡改失败 |
| `tests/unit/test_wecom_domain.py` | render_template + validate_template_vars + is_important |
| `tests/unit/test_wecom_message_status.py` | 状态机转移 |
| `tests/integration/test_wecom_config.py` | 配置加密落库不回显 + test_connection（mock client） |
| `tests/integration/test_wecom_bind.py` | 绑定 匹配/未匹配404/无微信422（mock client） |
| `tests/integration/test_wecom_scan.py` | 扫描聚合 + 未绑定跳过 + 幂等（mock client） |
| `tests/integration/test_wecom_send.py` | 正常 created / 博主频控 / PR 频控 → rate_limited + notification（mock client） |
| `tests/integration/test_wecom_callback.py` | 签名通过 sent + 签名失败 403 + 未知 msgid 忽略 |
| `tests/integration/test_notification.py` | notify + unread_count + mark_read 限本人 |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 组件分层（api→service→repository/client/domain/core） | ✅ §2 无循环 |
| crypto 落地点明确（U12 占位 → U07 实现） | ✅ §1.3 |
| 频控复合索引 | ✅ §4 |
| 5 表 RLS enable | ✅ §4 |
| migration 011（接 010 head） | ✅ §1.4 |
| 复用 U04 find_urge_candidates（新增查询，URGE_STATUS_SQL_EXPR） | ✅ §1.3 |
| Celery autodiscover + Beat 注册 | ✅ §1.3 / §3 |
