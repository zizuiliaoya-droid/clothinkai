# U07 功能设计计划（Functional Design Plan）

> 单元：U07 — 企微集成基础（企业微信自动催发）
> 覆盖故事：EP08-S02 ~ S08（配置自建应用 / 外部联系人绑定 / 编辑催发模板 / 自动催发扫描 / 触发群发 / 频控降级 / 回调更新状态）
> 依赖：U04（promotion + urge_status 计算）；复用 U01（crypto 凭据 / audit / RLS / Celery）+ U03（blogger）
> 节奏：Functional Design 阶段 = 本计划 + 3 份功能设计文档（同一轮生成）

---

## 1. 单元上下文

### 1.1 范围（MVP）
企微自建应用配置（secret 加密）+ 博主外部联系人绑定 + 催发模板编辑 + 每日扫描催发推广 + 企微群发助手 API 发送 + 频控降级站内通知 + 回调更新消息状态。

### 1.2 关键语义（来自开发文档 §八 + stories EP08）
- 催发触发：urge_status ∈ {催发, 重要催发, 超时}（U04 `urge_calculator` 已实现，实时计算不存库）。
- 频控：每博主每天最多 1 条群发；每 PR 每天最多 1 次群发；超限降级为站内通知。
- 企微限制：群发需员工在企微端确认（故 created → sent 经回调推进）。
- 消息状态：pending / created / sent / rejected / rate_limited / failed（6 态）。
- 模板变量白名单：{博主昵称} {商品简称} {预定发布日期} {剩余天数}。

### 1.3 不在本单元（划归 U15 / 后续）
- 发文通知控评（EP08-S09）、异常预警推送管理群（EP08-S10）→ U15。
- U01 备份失败接入企微告警通道 → U07 完成后作为附带项（本单元提供 `push_to_admins` 能力即可，不强制接线）。

---

## 2. 澄清问题（已预填 [Answer]，请审阅）

### Q1 — wecom secret 加密机制（EP08-S02）
U07 是 MVP 首个需要凭据加密的单元，但 `core/security/crypto.py` 当前为 U12 占位（抛 NotImplementedError）。
- [Answer] **U07 落地真实 AES-256-GCM + 每租户 HKDF 派生**（实现 crypto.py 的 `encrypt_credential`/`decrypt_credential`，用 `CREDENTIAL_MASTER_KEY` 作 master key，HKDF salt=tenant_id）。U12 仅在此基础上追加密钥轮换 + 采集凭据 CRUD。`wecom_config.secret_ciphertext` 存密文（bytea），任何响应**绝不回显**明文；解密走 `@audit("wecom.secret.decrypt")`。

### Q2 — 外部联系人绑定存储（EP08-S03）
- [Answer] **独立 `wecom_contact` 表**（不改 U03 blogger 表）：`blogger_id`(UNIQUE per tenant) + `external_userid` + `bound_by` + `bound_at`。一个博主一条绑定记录（可重绑覆盖）。绑定时调企微"获取客户列表"按 wechat 匹配 external_userid；未匹配 → 404。

### Q3 — wecom_config 多应用？
- [Answer] **单租户单条**（`UNIQUE(tenant_id)`）：corp_id + agent_id + secret_ciphertext + callback_token + callback_aes_key(可选) + default_sender_userid（群发助手发送人）+ is_active。

### Q4 — 催发模板存储（EP08-S04）
- [Answer] **独立 `message_template` 表**：`template_type`（urge / urge_important）+ `content` + `updated_by`，`UNIQUE(tenant_id, template_type)`。两类模板（催发 / 重要催发）；超时复用催发模板。保存时校验变量白名单，非法变量 → 422 列出。

### Q5 — 模板变量白名单
- [Answer] {博主昵称}{商品简称}{预定发布日期}{剩余天数} 四个；渲染时缺值用空串；超出白名单的 `{xxx}` → 422。

### Q6 — wecom_message 模型与状态机（EP08-S05~S08）
- [Answer] 字段：`blogger_id` + `pr_id`（发起 PR）+ `external_userid`(快照) + `template_type` + `rendered_content` + `promotion_ids`(JSONB 聚合溯源) + `status`(6 态) + `wecom_msgid`(企微返回) + `error_detail` + `sent_at`。状态机：pending→created→sent；pending→rate_limited（降级终态）；created→rejected/failed；任意→failed。无 is_active（消息记录留痕）。

### Q7 — 频控计数权威源
- [Answer] **DB 查询 `wecom_message` 当天计数**（非 Redis 计数器，避免双源不一致）：博主当天 status ∈ {created, sent} 计数 ≥1 → 博主频控；PR 当天 status ∈ {created, sent} 计数 ≥1 → PR 频控。命中 → status=rate_limited + 写 notification。

### Q8 — notification 表（EP08-S07 降级目标）
- [Answer] **U07 创建 `notification` 表 + NotificationService**（MVP 首个消费者）：`user_id` + `type`（urge_manual 等）+ `content` + `link`(可选) + `is_read` + `created_at`。提供 `notify(user_ids, content, link)` / `unread_count` / `mark_read`。

### Q9 — 扫描任务聚合与超时处理（EP08-S05）
- [Answer] 扫描 urge_status ∈ {催发, 重要催发, 超时} 且 publish_status ∈ {未发布, 异常} 且未取消的推广，按 **(blogger_id, pr_id)** 聚合为一条 pending message（promotion_ids 存聚合列表，template_type 取该组最紧急级别：重要催发/超时→urge_important，否则 urge）。超时也写 pending（执行时若博主当天已发则自然降级），与 story 筛选口径一致。

### Q10 — access_token 缓存
- [Answer] Redis key `wecom:token:{tenant_id}`，TTL 7000s（企微 7200 留余量）；失效/41001 错误码自动刷新一次重试。

### Q11 — 回调签名校验（EP08-S08）
- [Answer] 企微回调 URL 验证（GET echostr 解密回显）+ 消息回调签名 `msg_signature = sha1(sorted(token, timestamp, nonce, encrypt))`。校验失败 → 403 + `@audit("wecom.callback.invalid_signature")` 记可疑请求。回调载荷解出 msgid + result → 推进 wecom_message.status。

### Q12 — 发送人 sender（群发助手需 sender userid）
- [Answer] MVP 用 `wecom_config.default_sender_userid` 作群发助手发送人（避免改 auth user 表存企微 userid）；PR 维度频控仍按 promotion.pr_id 统计。V1 可扩展 user.wecom_userid 精确映射。

### Q13 — WecomClient 真实 HTTP？无企微环境如何测试
- [Answer] WecomClient 用 httpx 真实封装企微 REST（get_access_token / 获取客户列表 / add_msg_template / 回调解密）；单元与集成测试用 **monkeypatch mock WecomClient**（无真实企微凭据），不阻断 CI。test_connection 调 get_access_token 成功返回 true。

### Q14 — 字段级权限
- [Answer] MVP 沿用模块/功能级权限（`wecom.config:write` / `wecom.bind:write` / `wecom.template:write` / `wecom.message:read`）；secret 解密仅 system_context（Celery）+ 配置写权限可见状态，不回显明文。字段级权限 U09 统一。

---

## 3. 执行步骤（Functional Design 阶段）

- [x] 3.1 `U07/functional-design/domain-entities.md`：4 新实体（wecom_config / wecom_contact / message_template / wecom_message）+ notification + 状态机 + ER 图 + 复用 U01/U03/U04 声明
- [x] 3.2 `U07/functional-design/business-rules.md`：BR-U07-01~ （配置加密 / 绑定 / 模板变量 / 扫描聚合 / 频控降级 / 回调签名 / access_token 缓存 / 审计）+ 错误码矩阵
- [x] 3.3 `U07/functional-design/business-logic-model.md`：7 UC（配置 + 测试连接 / 绑定 / 编辑模板 / 扫描 / 执行群发+降级 / 回调 / 通知）+ 端到端时序 + 与 U04/U15 契约
- [x] 3.4 全部诊断器无警告 + 故事 EP08-S02~S08 100% 覆盖

---

## 4. 故事追溯矩阵

| 故事 | 设计落点 |
|---|---|
| EP08-S02 配置自建应用 | wecom_config + secret 加密 + test_connection |
| EP08-S03 外部联系人绑定 | wecom_contact + 企微客户列表匹配 |
| EP08-S04 编辑催发模板 | message_template + 变量白名单校验 |
| EP08-S05 自动催发扫描 | scan_and_dispatch_urge + (blogger,pr) 聚合 |
| EP08-S06 触发群发 | execute_wecom_message + add_msg_template + 回调 created→sent |
| EP08-S07 频控降级 | check_rate_limit + notification |
| EP08-S08 回调更新状态 | handle_callback + 签名校验 |

---

**等待用户回复"继续"批准节奏；本轮直接生成 3 份功能设计文档。**
