# U15 NFR 需求计划（NFR Requirements Plan）

> 单元：U15 — 企微进阶（发文通知控评 + 异常预警推送）（EP08-S09、S10 + EP10-NFR06）
> 增量式：复用 U01 NFR 基线 + U07 企微 NFR（client/频控/token/Sentry）；仅列 U15 特异指标
> 依赖：U07（WecomClient/事件总线）、U14（ProductionService）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：S09 控评通知性能与超时？
[Answer] 异步 Celery 任务，无在线 SLA（不阻塞发布主流程）。群机器人 HTTP 复用 WECOM_HTTP_TIMEOUT=10s；单笔记一次推送，best-effort 不重试（仅 OperationalError 入队失败 autoretry 1 次）。

### Q2：S10 监控任务性能预算？
[Answer] check_anomaly_and_alert 每小时逐租户；单租户 = 1 次 ProductionService.get_report(last_7d)（U14 SLA ≤800ms）+ N 款异常推送。监控任务无在线 SLA；目标单租户处理 ≤5s（含推送），全租户串行可接受（租户数 V1 量级小）。

### Q3：群机器人 webhook 凭据安全？
[Answer] webhook URL 含 key 属凭据。V1 明文存 wecom_alert_config（RLS 隔离 + 仅 admin/operations 可读写 + 读响应脱敏仅显末 6 位）；不进日志（log 仅记 tenant/status 不记 URL）。U16+ 可升级 AES-GCM（复用 crypto.py，与 credential 同模式）。

### Q4：自建应用推送复用 U07 token 安全？
[Answer] send_app_message 复用 WecomClient.get_access_token（Redis 缓存 7000s + 40014/42001 刷新重试）+ decrypt_credential（解密审计 system actor）。无新密钥。频控复用 WecomRateLimited（best-effort 不重试）。

### Q5：威胁模型？
[Answer] (a) 跨租户：alert_config/alert_log RLS + 任务 set_config tenant_id；(b) webhook 泄露：脱敏回显 + 不入日志；(c) 推送轰炸：period_key 当日去重 + is_enabled 总开关；(d) 接收人越权：alert_recipients 由 admin 配置，推送 system actor 审计可溯。

### Q6：去重并发安全？
[Answer] wecom_alert_log UNIQUE(tenant, alert_type, entity_ref, period_key) DB 兜底；service 先 SELECT 查重 + INSERT，IntegrityError（并发同 key）catch 视为已推 deduped。每小时单 Beat 触发并发概率低，UNIQUE 防御足够。

### Q7：可观测指标？
[Answer] 2 个新指标：wecom_group_notify_total{status}（sent/failed/unconfigured/skipped）+ wecom_anomaly_alert_total{alert_type,status}（sent/failed/no_recipient/deduped）。复用 U07 wecom_send_duration_seconds（send_app_message 计时）。NFR06：Celery 任务失败 Sentry capture。

### Q8：多租户隔离与容错？
[Answer] 逐租户 system_context + AsyncSessionApp + set_config；单租户失败 catch+log+Sentry 不中止其余（同 U07 scan / U11 recompute / U13 schedule 模式）。bypass 读 is_enabled 租户清单。

### Q9：迁移与 schedule？
[Answer] migration 019：2 表（wecom_alert_config/wecom_alert_log）+ RLS + UNIQUE + idx + wecom.alert_config:read/write scope seed（admin 通配 + operations 显式）。celery_app Beat 追加 check-anomaly-hourly（crontab minute=0，default 队列，与 09:00 催发/02:00 采集错峰）+ autodiscover 已含 wecom_tasks。

### Q10：测试矩阵？
[Answer] 测试 3 文件：unit（AnomalyAlertService 阈值判定纯函数：退货率>阈值/roi<阈值/conversion 占位/去重逻辑）+ integration（alert_config upsert + check_and_alert 端到端：投产数据→命中→落 log→去重 skip + RLS）+ api（alert-config GET/PUT 401 + OpenAPI + webhook 脱敏）。S09 group_notify 任务用 monkeypatch WecomClient.send_group_robot 验证 best-effort 分支。

### Q11：阈值默认与配置？
[Answer] return_rate_threshold 默认 0.40（DB DEFAULT）；low_roi_threshold/low_conversion_threshold 默认 null（不检）；监控窗口 last_7d 常量（resolve_time_range 复用）；去重周期 = 当日。阈值即时生效（任务每次读 DB）。

---

## 1. 步骤

- [x] 1.1 阅读 U15 functional-design 3 文档 + U07 wecom NFR（client/频控/token）+ U14 ProductionService SLA + U01 NFR 基线
- [x] 1.2 编写 nfr-requirements.md（性能：异步无 SLA + 监控 ≤5s/租户；安全：webhook 凭据脱敏+RLS+威胁模型；去重并发；2 指标 + NFR06；多租户隔离测试矩阵；migration 019；Beat hourly）
- [x] 1.3 编写 tech-stack-decisions.md（零新依赖复用 WecomClient/events/ProductionService/crypto；modules/wecom 6 新建 + 横切落点；2 指标定义；migration 019 片段；Beat schedule；测试 3 文件）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。nfr-requirements.md 的 spec-format 假阳性 IGNORE。**
