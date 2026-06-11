# U15 基础设施设计计划（Infrastructure Design Plan）

> 单元：U15 — 企微进阶（发文通知控评 + 异常预警推送）（EP08-S09、S10 + EP10-NFR06）
> 产出：infrastructure-design.md + deployment-architecture.md
> 增量式：复用 U01/U07 基础设施（Zeabur 6 服务 + celery-worker/beat + Redis + R2 + Sentry）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务 / 计算资源？
[Answer] 无新服务。复用 backend（alert-config API）+ celery-worker（notify_control_group / check_anomaly_and_alert）+ celery-beat（check-anomaly-hourly schedule）。Beat 任务走现有 default 队列，无新 worker 进程。

### Q2：数据库变更？
[Answer] migration 019：wecom_alert_config（UNIQUE tenant + RLS）+ wecom_alert_log（UNIQUE 去重 + RLS + idx fired_at）2 表 + wecom.alert_config:read/write scope seed（admin 通配 + operations 显式）。无回填（新表）；down 安全 drop。

### Q3：新增环境变量 / 依赖 / R2 桶 / Redis 库？
[Answer] 全部零新增。复用 WECOM_API_BASE/WECOM_HTTP_TIMEOUT/WECOM_TOKEN_TTL（U07）+ REDIS_URL_CACHE（access_token 缓存）+ REDIS_URL_CELERY_*（队列）。webhook URL 存 DB 非环境变量。无 R2 用量。

### Q4：外部网络出站？
[Answer] 出站到企微域名（qyapi.weixin.qq.com）：群机器人 webhook/send（S09）+ /cgi-bin/message/send（S10），复用 U07 已建立的出站路径。webhook 含 key，仅经 HTTPS 出站，不回显不入日志。

### Q5：Beat 调度错峰？
[Answer] check-anomaly-hourly crontab(minute=0) 每小时整点；与既有 09:00 催发扫描 / 02:00 采集 / 03:00 备份 / 04:00 清理错峰（整点小任务，单租户 ≤5s，量级可控）。default 队列与催发共用。

### Q6：监控告警（NFR06）部署面？
[Answer] 复用 U01 Sentry（2 项目 prod/staging）：Celery 任务失败 capture_exception。复用 prometheus-fastapi-instrumentator + /metrics（2 新 Counter 自动暴露）。无新监控基础设施；异常预警本身即业务告警通道（推企微管理群）。

### Q7：部署一致性约束？
[Answer] U15 依赖 U07（wecom 表/客户端/事件总线）+ U14（report 表/ProductionService）已部署。PromotionPublished listener 注册后，U04 发布即触发（U04 早于 U15，事件已存在，无部署顺序风险——通知类 required_handler=False 缺 handler 也不报错）。migration 019 在 018 之后。

### Q8：本地验证环境？
[Answer] Docker PG16:5558 + Redis7:6413 + Py3.12（U15 唯一端口）；alembic upgrade head 含 019；U15 子集 + 全量回归；覆盖率 ≥70%。

### Q9：回滚策略？
[Answer] 代码回滚：移除 alert_router 挂载 + listener 注册 + Beat schedule（功能下线，无数据破坏）。DB 回滚：migration 019 down（drop 2 表 + 删 scope），无回填无依赖外键被引用，安全。

---

## 1. 步骤

- [x] 1.1 阅读 U01/U07 infrastructure-design（Zeabur 服务/celery 队列/Beat/Sentry）+ U15 nfr-design logical-components（migration 019 + Beat）
- [x] 1.2 编写 infrastructure-design.md（无新服务；migration 019 2 表 DDL+RLS+scope seed；复用企微出站+Redis+Sentry；零新依赖/环境变量/桶；Beat check-anomaly-hourly；本地 Docker 5558/6413）
- [x] 1.3 编写 deployment-architecture.md（拓扑无变更+部署 checklist+celery-beat schedule 更新+验证步骤+监控 2 指标+回滚）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。infrastructure-design.md 的 spec-format 假阳性 IGNORE。**
