# U15 NFR 设计计划（NFR Design Plan）

> 单元：U15 — 企微进阶（发文通知控评 + 异常预警推送）（EP08-S09、S10 + EP10-NFR06）
> 产出：nfr-design-patterns.md（伪代码模式）+ logical-components.md（组件清单 + 依赖图）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：S09 listener → 任务 的事务时序模式？
[Answer] P-U15-01：on_promotion_published 在 publish 事务内仅 `notify_control_group.delay(...)`（不 HTTP / 不写库）。Celery 任务用 asyncio.run(_async)，system_context + AsyncSessionApp + set_config tenant；重读 promotion 校验 publish_status='已发布' + publish_url 非空（防回滚误发）→ GroupNotifyService.notify_publish。完整伪代码含 unconfigured/skipped/sent/failed 4 分支 + 指标。

### Q2：GroupNotifyService.notify_publish 内部？
[Answer] P-U15-01 续：读 wecom_alert_config（webhook）→ 空则 status="unconfigured" warning return；构建 markdown（款号 internal_code + 博主昵称 + publish_url + 日期）→ WecomClient.send_group_robot best-effort try/except(WecomApiError/WecomRateLimited/httpx 异常)→ status sent/failed；wecom_group_notify_total{status}.inc()。不抛错（best-effort）。

### Q3：S10 check_anomaly_and_alert 调度与逐租户容错？
[Answer] P-U15-02：Beat hourly → asyncio.run(_check_all)。bypass 读 `wecom_alert_config WHERE is_enabled=true` 租户清单 → for tid: tenant_id_ctx.set + system_context + AsyncSessionApp + set_config → AnomalyAlertService.check_and_alert → commit；单租户 except catch+log+Sentry capture 不中止。完整伪代码。

### Q4：AnomalyAlertService.check_and_alert 判定 + 去重 + 推送？
[Answer] P-U15-03：cfg = 读 alert_config（实时阈值）；report = ProductionService.get_report(tenant, last_7d)；for row: 判定 return_rate>threshold→return_rate_high / low_roi_threshold 非空且 net_roi<→roi_low（conversion 占位跳过）；命中 → period_key=today；_already_fired(tenant,type,style_id,period_key)? skip deduped；alert_recipients 空? no_recipient（不落 log）；try send_app_message + insert alert_log（成功才落，IntegrityError→deduped）；except→failed 不落 log；指标 wecom_anomaly_alert_total{type,status}。完整伪代码 + markdown 建议文案模板。

### Q5：AlertConfigService 配置读写 + 脱敏 + 校验？
[Answer] P-U15-04：upsert ON CONFLICT(tenant_id) DO UPDATE（阈值/webhook/recipients/enabled）；get_response webhook 脱敏（configured bool + 末 6 位 mask）；校验 return_rate∈[0,1]/low_roi>0/recipients 字符串数组去重/webhook https → 非法 AlertConfigInvalidError(400)。完整伪代码。

### Q6：客户端 2 方法实现？
[Answer] P-U15-05：send_group_robot 直连 webhook_url POST markdown（errcode 非 0→WecomApiError）；send_app_message _call /cgi-bin/message/send touser '|' join + agentid + markdown（复用 token 刷新 + 频控）+ wecom_send_duration_seconds 计时。

### Q7：logical-components 组件与依赖？
[Answer] modules/wecom 新建 6（alert_models/alert_schemas/alert_config_service/group_notify_service/anomaly_service/alert_api）+ 横切 10 改动；依赖图：alert_api→AlertConfigService→repo；group_notify_service→WecomClient+AlertConfigRepo+PromotionRepo；anomaly_service→ProductionService(U14)+WecomClient+AlertLogRepo+AlertConfigRepo；listener→Celery delay；无循环（U15→U07→U01；U15→U14→U13/U05）。

### Q8：repository 落点？
[Answer] 复用 wecom/repository.py 追加 WecomAlertConfigRepository（get/upsert）+ WecomAlertLogRepository（exists/add）；或新建 alert_repository.py。采用复用 repository.py 追加（与 wecom 现有一致）。

### Q9：测试设计映射？
[Answer] logical-components 末尾列 3 测试文件 → 组件/规则映射：test_anomaly_rules（判定纯逻辑 _evaluate_row）+ test_wecom_alert（config upsert/check_and_alert/去重/RLS）+ test_wecom_alert_api（401/OpenAPI/脱敏）。

---

## 1. 步骤

- [x] 1.1 阅读 U15 functional-design + nfr-requirements + U07 send_service/scan_service 模式（Celery 逐租户 + best-effort）
- [x] 1.2 编写 nfr-design-patterns.md（P-U15-01 S09 listener+task+notify_publish / P-U15-02 check_anomaly 逐租户容错 / P-U15-03 check_and_alert 判定+去重+推送 / P-U15-04 AlertConfigService upsert+脱敏+校验 / P-U15-05 客户端 2 方法 完整伪代码）
- [x] 1.3 编写 logical-components.md（6 新建 + 10 横切 + repository 追加 + 依赖图无循环 + migration 019 2 表 DDL 概要 + 3 测试文件映射）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。**
