# U15 业务逻辑模型（企微进阶）

> 单元：U15（EP08-S09、S10 + EP10-NFR06）
> 2 个核心用例 + 跨单元契约（U04 事件 / U07 客户端 / U14 投产数据）

---

## UC-1：发文通知控评（EP08-S09）

**角色**：PR（被动，由发布动作触发）
**前置**：promotion 发布成功；wecom_alert_config.control_group_webhook 已配置

```
[U04 PR 发布笔记]
  PUT /api/promotions/{id}/publish (publish_url, publish_date)
    └─ PromotionService.publish() 事务内：
         publish_status='已发布' → dispatch(PromotionPublished, session)
                                        │（同事务，required_handler=False）
                                        ▼
         [U15 listener] on_promotion_published(event, session)
              └─ notify_control_group.delay(promotion_id, tenant_id)   ← 仅入队，无 HTTP
    └─ commit（发布主流程结束，无论通知成功与否）

[Celery 异步] notify_control_group(promotion_id, tenant_id)
  system_context + AsyncSessionApp + set_config(tenant_id)
    1. 读 promotion；校验 publish_status='已发布' 且 publish_url 非空
         否则 → skip（status="skipped"，防回滚误发 BR-U15-03）
    2. 读 wecom_alert_config.control_group_webhook
         为空 → log warning + status="unconfigured"，return（BR-U15-04）
    3. 构建 markdown（款号 + 博主昵称 + publish_url + 发布日期）
    4. GroupNotifyService → WecomClient.send_group_robot(webhook, markdown)
         成功 → status="sent"
         WecomApiError/RateLimited/HTTP 异常 → catch + status="failed"（BR-U15-05，不重试）
    5. wecom_group_notify_total{status}.inc()
```

**关键点**：事件在事务内仅入队；HTTP 在任务里且任务重读校验状态 → 幂等 + 回滚安全 + best-effort 不阻塞发布。

---

## UC-2：异常预警推送管理群（EP08-S10）

**角色**：管理员（配置阈值）；系统（Beat 定时监控）
**前置**：wecom_alert_config.is_enabled=true；alert_recipients 非空；U14 投产数据可用

```
[Beat 每小时] check_anomaly_and_alert()
  bypass 读 wecom_alert_config WHERE is_enabled=true → tenant 列表
  for tid in tenants:  （单租户失败 catch+log+Sentry 不中止其余 BR-U15-61）
    system_context + AsyncSessionApp + set_config(tid)
      1. 读 wecom_alert_config（实时阈值，无缓存 BR-U15-24）
      2. AnomalyAlertService.check_and_alert(tid, cfg):
           report = ProductionService.get_report(tid, last_7d)   ← 复用 U14
           anomalies = []
           for row in report.items:
             if row.return_rate is not None and row.return_rate > cfg.return_rate_threshold:
                 anomalies.append(("return_rate_high", row, 详情))
             if cfg.low_roi_threshold and row.net_roi is not None
                and row.net_roi < cfg.low_roi_threshold:
                 anomalies.append(("roi_low", row, 详情))
             # conversion_low：V1 占位不检（BR-U15-23）
           for (alert_type, row, detail) in anomalies:
             period_key = today 'YYYY-MM-DD'
             if alert_log_exists(tid, alert_type, row.style_id, period_key):
                 status="deduped"; continue            （BR-U15-25）
             if not cfg.alert_recipients:
                 status="no_recipient"; continue（不落 log BR-U15-27）
             try:
                 markdown = 建议文案(alert_type, row, detail)
                 WecomClient.send_app_message(cfg.alert_recipients, markdown)
                 insert wecom_alert_log(...)            （成功才落 log BR-U15-29）
                 status="sent"
             except (WecomApiError, WecomRateLimited, HTTP):
                 status="failed"  （不落 log，可重试 BR-U15-28）
             wecom_anomaly_alert_total{alert_type, status}.inc()
      3. commit
```

**去重语义**：`UNIQUE(tenant, alert_type, entity_ref, period_key)` + 推送前查询双保险；同款式同类型当天至多一条。

---

## UC-3：配置异常阈值（管理员）

```
GET /api/wecom/alert-config   (require wecom.alert_config:read)
  → AlertConfigResponse（webhook 脱敏 BR-U15-44 + 3 阈值 + 接收人 + 开关）

PUT /api/wecom/alert-config   (require wecom.alert_config:write)
  payload: control_group_webhook? / return_rate_threshold / low_roi_threshold? /
           low_conversion_threshold? / alert_recipients / is_enabled
  → 校验（阈值区间 BR-U15-41 / webhook https BR-U15-43）
  → upsert ON CONFLICT(tenant_id)（BR-U15-40）
  → 下次 check_anomaly_and_alert 立即生效（无缓存）
```

---

## 4. 跨单元契约

| 来源单元 | 契约 | U15 用法 |
|---|---|---|
| U04 promotion | `PromotionPublished` 事件（通知类，required_handler=False） | U15 注册 on_promotion_published listener；U04 无需改动（预留监听点已存在） |
| U07 wecom | WecomConfig（agent_id/secret）+ WecomClient（token 缓存 + 频控）+ decrypt_credential | send_app_message 复用 access_token；send_group_robot 直连 webhook |
| U07 wecom | core/events 事件总线 + main.register_event_listeners | 注册模式同 promotion/finance listeners |
| U14 report | `ProductionService.get_report(tenant, last_7d)` → ProductionRow(return_rate/net_roi/style_code) | S10 异常预警数据源（只读复用） |
| U01 core | system_context / AsyncSessionApp / Sentry / metrics | Celery 逐租户容错 + NFR06 |

---

## 5. 故事覆盖

| 故事 | 覆盖 |
|---|---|
| EP08-S09 发文通知控评 | UC-1（事件→任务→群机器人 best-effort + webhook 缺失 warning） |
| EP08-S10 异常预警推送管理群 | UC-2 + UC-3（投产阈值判定 + 去重 + 自建应用推送 + 阈值可配即时生效） |
| EP10-NFR06 监控与告警 | 异常预警业务监控 + Sentry 任务失败 + 4 类指标（BR-U15-80~82） |

---

## 6. 时间维度与一致性

- S10 监控窗口固定 last_7d（resolve_time_range 复用 U08/U14）；去重周期 period_key=运行当日。
- 同一异常每日至多一条（去重）；阈值调整后下次运行（≤1 小时）生效。
- 数据口径与 U14 投产报表完全一致（同一 ProductionService），不重复实现聚合。
