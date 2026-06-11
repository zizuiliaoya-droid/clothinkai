# U15 功能设计计划（Functional Design Plan）

> 单元：U15 — 企微进阶（发文通知控评 + 异常预警推送管理群）（EP08-S09、S10 + EP10-NFR06）
> 依赖：U07（wecom 模块 + WecomClient + 事件总线）、U14（ProductionService 投产数据，异常预警数据源）
> 复用：modules/wecom 追加群机器人 + 预警；core/events PromotionPublished 监听；Celery Beat

---

## 0. 澄清问题（[Answer] 预填）

### Q1：S09 控评通知用哪种企微能力？
[Answer] 群机器人 webhook（群聊机器人 URL，无需 access_token）。在 wecom_alert_config 存 control_group_webhook；笔记发布后 POST markdown 到 webhook。与 S10 的自建应用推送解耦（不同能力、不同接收方）。

### Q2：S09 触发时机与解耦方式？
[Answer] 复用 U04 已有 `PromotionPublished` 通知类事件（required_handler=False，U07 预留监听点）。U15 注册 listener `on_promotion_published` → 仅 enqueue Celery 任务 `notify_control_group.delay(promotion_id, tenant_id)`（事件在事务内，不在事务内做 HTTP）。Celery 任务再读 promotion 校验 publish_status='已发布' + publish_url 后发送（幂等 + 防回滚误发）。

### Q3：S09 webhook 未配置怎么办？
[Answer] 任务内若 control_group_webhook 为空 → 记录 warning + 指标 wecom_group_notify_total{status="unconfigured"}，不抛错不阻塞（GWT 第二条）。发送失败（HTTP/频控）同样 best-effort：log + 指标 status="failed"，不重试影响主流程。

### Q4：S10 异常预警用哪种能力 + 推给谁？
[Answer] 自建应用消息推送（`/cgi-bin/message/send`，touser=管理群接收人 userid 列表 + agentid + markdown）。接收人列表存 wecom_alert_config.alert_recipients(JSONB userid 数组)。复用 WecomClient（access_token 缓存 + 频控处理）。

### Q5：S10 监控哪些异常指标 + 数据源？
[Answer] 复用 U14 ProductionService.get_report(last_7d) 按款式聚合结果。3 类异常：退货退款率 return_rate > return_rate_threshold（默认 0.40）/ 净投产比 net_roi < low_roi_threshold（可选，null 则不检）/ 加购转化（V1 口径缺失占位，默认不检）。逐款式判定，命中即生成预警条目（含异常详情 + 简短建议文案）。

### Q6：S10 阈值如何配置 + 立即生效？
[Answer] 新表 wecom_alert_config（每租户单条，UNIQUE tenant）存阈值。AlertConfigService get/update（admin/operations write）。check_anomaly_and_alert 任务每次运行实时读 DB 阈值 → "新阈值立即生效"（GWT 第二条），无缓存。

### Q7：S10 防重复推送？
[Answer] 新表 wecom_alert_log 记录已触发预警（tenant/alert_type/entity_ref/period_key=当日 yyyy-mm-dd），UNIQUE(tenant, alert_type, entity_ref, period_key)。任务命中异常先查当日是否已记录 → 已记录跳过（同款式同类型当天只推一次），未记录则推送 + 落 log（同事务幂等）。

### Q8：S10 监控任务频率与多租户？
[Answer] Celery Beat `check_anomaly_and_alert` 每小时（services.md 约定），逐租户：仅遍历 wecom_alert_config.is_enabled=true 的租户；单租户失败 catch+log+Sentry 不中止其他租户（同 U07 scan 模式）。system_context + AsyncSessionApp + set_config tenant_id。

### Q9：新建几张表？落在哪个模块？
[Answer] 复用 modules/wecom，追加 2 表（migration 019）：wecom_alert_config（控评 webhook + 3 阈值 + 接收人 + 开关，UNIQUE tenant）+ wecom_alert_log（预警去重留痕）。两表均 TenantScopedModel + RLS。

### Q10：客户端能力扩展？
[Answer] WecomClient 追加 2 方法：send_group_robot(webhook_url, markdown)（直连 webhook 无 token）+ send_app_message(touser, markdown)（自建应用 /cgi-bin/message/send）。WecomConfig 已有 agent_id 用于 app message。

### Q11：权限与指标？
[Answer] 新增 scope：wecom.alert_config:read/write（admin 全部，operations read+write）；migration 019 seed。指标：wecom_group_notify_total{status} + wecom_anomaly_alert_total{alert_type,status}（NFR06 监控告警可观测）。

### Q12：NFR06（监控与告警）落点？
[Answer] U15 承载 NFR06：异常预警推送（业务监控）+ 复用 U01 Sentry（Celery 任务失败 capture）+ 4 类指标。Celery 任务失败告警通过现有 Sentry + 任务内 catch；不新建告警基础设施。

---

## 1. 步骤

- [x] 1.1 阅读 EP08-S09/S10 GWT + 开发文档 2.8 节 + 已有 wecom 模块（client/events/listeners/send_service）+ U14 ProductionService
- [x] 1.2 编写 domain-entities.md（wecom_alert_config / wecom_alert_log 2 表 + 字段规范 + AlertType 枚举 + 客户端 2 新方法 I/O + ER）
- [x] 1.3 编写 business-rules.md（BR-U15-01~ 控评触发/webhook 缺失容错/发送 best-effort/异常 3 类阈值/阈值即时生效/去重 period_key/逐租户容错/权限/错误码）
- [x] 1.4 编写 business-logic-model.md（2 UC：S09 发布→事件→任务→群机器人；S10 Beat→逐租户→投产聚合→阈值判定→去重→自建应用推送 + 跨单元契约 U04/U07/U14）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
