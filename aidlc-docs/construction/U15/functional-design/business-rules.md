# U15 业务规则（企微进阶：发文通知控评 + 异常预警）

> 单元：U15（EP08-S09、S10 + EP10-NFR06）
> 错误码沿用 core/exceptions 体系；企微调用沿用 U07 WecomApiError/WecomRateLimited

---

## 1. S09 发文通知控评（群机器人）

- **BR-U15-01** 触发源：复用 U04 `PromotionPublished` 通知类事件（publish 成功时发出，含 promotion_id/internal_code/blogger_id/publish_url/publish_date/pr_id）。U15 注册 listener `on_promotion_published`。
- **BR-U15-02** 事务安全：listener 在 publish 事务内执行，**不做 HTTP**，仅 `notify_control_group.delay(promotion_id, tenant_id)` 入队。HTTP 在 Celery 任务异步执行。
- **BR-U15-03** 防回滚误发：任务执行时重新读取 promotion，校验 `publish_status='已发布'` 且 `publish_url` 非空，否则 skip（publish 事务已回滚或状态已变）。
- **BR-U15-04** webhook 缺失容错：`wecom_alert_config.control_group_webhook` 为空 → 不发送，log warning + `wecom_group_notify_total{status="unconfigured"}`，**不抛错**（GWT 第二条「不阻塞主流程，记录 warning」）。
- **BR-U15-05** 发送 best-effort：群机器人 HTTP 失败 / 频控 / 企微错误码 → catch + log + `wecom_group_notify_total{status="failed"}`，不重试不冒泡（best-effort，发布主流程已完成）。成功 → `status="sent"`。
- **BR-U15-06** 消息内容：markdown，含 款号/内部编号 + 博主昵称 + publish_url（可点击）+ 发布日期，模板固定（V1 不可配，复用简洁文案）。
- **BR-U15-07** 群机器人鉴权：webhook URL 自带 key，**不需 access_token**（与自建应用解耦）；直连传入 URL。

---

## 2. S10 异常预警推送管理群（自建应用）

- **BR-U15-20** 数据源：复用 U14 `ProductionService.get_report(tenant, last_7d)` 按款式聚合结果（return_rate / net_roi / style_code / style_name）。
- **BR-U15-21** 退货率异常：`return_rate` 非空且 `> return_rate_threshold`（默认 0.40）→ 生成 `return_rate_high` 预警。
- **BR-U15-22** 投产比异常：`low_roi_threshold` 非空且 `net_roi` 非空且 `net_roi < low_roi_threshold` → 生成 `roi_low` 预警。
- **BR-U15-23** 转化率异常：`conversion_low` V1 口径缺失 → 占位不检（`low_conversion_threshold` 即使配置也跳过，文档标注 U13/U16 补齐后启用）。
- **BR-U15-24** 阈值即时生效：每次 `check_anomaly_and_alert` 运行实时读 `wecom_alert_config`，无缓存（GWT「新阈值立即生效」）。
- **BR-U15-25** 去重：命中异常先查 `wecom_alert_log` 是否存在 (tenant, alert_type, entity_ref=style_id, period_key=当日)；存在 → skip；不存在 → 推送 + 落 log（同事务，UNIQUE 兜底防并发重复）。
- **BR-U15-26** 推送方式：自建应用 `/cgi-bin/message/send`，touser=`alert_recipients`（userid 数组 `|` 连接），msgtype=markdown，含异常类型/款号/指标值/阈值/建议文案。
- **BR-U15-27** 接收人为空：`alert_recipients=[]` → 不推送，log warning + 仍落 log（避免下次重复判定）？→ **不落 log**（无接收人视为未告警，配置补齐后应能补推）；仅 `wecom_anomaly_alert_total{status="no_recipient"}`。
- **BR-U15-28** 推送失败：HTTP/频控/企微错误 → catch + log + `wecom_anomaly_alert_total{status="failed"}`，**不落 alert_log**（下次可重试），不中止其他款式/租户。
- **BR-U15-29** 推送成功 → `wecom_anomaly_alert_total{alert_type, status="sent"}` + 落 alert_log。

---

## 3. 配置管理（AlertConfigService）

- **BR-U15-40** 单租户单条：`wecom_alert_config` UNIQUE(tenant)；update 用 upsert（ON CONFLICT(tenant_id) DO UPDATE）。
- **BR-U15-41** 阈值校验：return_rate_threshold ∈ [0, 1]；low_roi_threshold > 0（若提供）；low_conversion_threshold ∈ [0, 1]（若提供）。非法 → 400。
- **BR-U15-42** 接收人校验：alert_recipients 为字符串数组（userid），去重；不校验企微端是否存在（推送时由企微返回，best-effort）。
- **BR-U15-43** webhook 校验：control_group_webhook 若提供须为 https URL（基础格式校验），否则 400。
- **BR-U15-44** 读响应：control_group_webhook 回显时脱敏（仅显示是否已配置 + 末 6 位），避免 webhook key 完整泄露。

---

## 4. 调度与多租户

- **BR-U15-60** `check_anomaly_and_alert` Beat 每小时触发；bypass 读 `wecom_alert_config WHERE is_enabled=true` 租户列表。
- **BR-U15-61** 逐租户 system_context + AsyncSessionApp + `set_config('app.tenant_id', tid)`；单租户异常 catch + log + Sentry capture，不中止其余租户（同 U07 scan 模式）。
- **BR-U15-62** `notify_control_group` 任务：autoretry 仅对 OperationalError（DB）max_retries=1；企微 HTTP 失败不重试（best-effort BR-U15-05）。

---

## 5. 权限

- **BR-U15-70** `wecom.alert_config:read` → admin + operations；`wecom.alert_config:write` → admin + operations。migration 019 seed（admin 通配已覆盖，显式 seed operations）。
- **BR-U15-71** Celery 任务以 system actor 运行（无用户上下文），解密/推送审计复用 system_context。

---

## 6. NFR06（监控与告警）

- **BR-U15-80** 业务监控：异常预警即 NFR06 的业务层告警（退货率/投产比超阈值主动推送）。
- **BR-U15-81** 系统监控：Celery 任务失败 → 复用 U01 Sentry capture（任务内 catch + capture_exception）。
- **BR-U15-82** 可观测指标：`wecom_group_notify_total{status}`（sent/failed/unconfigured/skipped）+ `wecom_anomaly_alert_total{alert_type,status}`（sent/failed/no_recipient/deduped）。

---

## 7. 错误码矩阵

| 场景 | 处理 | HTTP/结果 |
|---|---|---|
| 阈值非法（越界） | AlertConfigInvalidError | 400 |
| webhook 非 https | AlertConfigInvalidError | 400 |
| 群机器人未配置 | log warning + 指标 | 任务 status=unconfigured（不抛错） |
| 群机器人发送失败 | catch + 指标 | 任务 status=failed（不抛错） |
| 自建应用接收人为空 | log + 指标 | status=no_recipient（不落 log） |
| 自建应用推送失败 | catch + 指标 | status=failed（不落 log，可重试） |
| 异常当日已推 | 去重 skip | status=deduped |
| 配置读写无权限 | 403（require_permission） | 403 |
