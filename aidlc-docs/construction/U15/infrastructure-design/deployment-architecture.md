# U15 部署架构（企微进阶）

> 拓扑无变更；唯一部署动作 = migration 019 + celery-beat schedule 更新 + 代码发布。
> 单元：EP08-S09、S10 + EP10-NFR06。

---

## 1. 部署拓扑（无变更）

```
[U04 promotion publish] ──PromotionPublished event──> [U15 listener]
                                                          └─ notify_control_group.delay
                                                                  │
[celery-beat] ──check-anomaly-hourly (minute=0)──> [celery-worker default 队列]
                                                          ├─ notify_control_group → 群机器人 webhook (S09)
                                                          └─ check_anomaly_and_alert → 自建应用推送 (S10)
                                                                  └─ ProductionService(U14) 读投产数据
[backend] ──GET/PUT /api/wecom/alert-config──> postgres(wecom_alert_config/log)
```

企微出站：celery-worker → qyapi.weixin.qq.com（webhook/send + message/send）。

---

## 2. 部署 checklist

1. [ ] 合并 U15 代码（modules/wecom 6 新建 + 10 横切 + migration 019 + 3 测试）
2. [ ] CI 通过（lint + 单测 + 集成 + 覆盖率门 ≥70%）
3. [ ] 运行 migration 019（migrate.yml job，prod/staging 分环境）→ head=019
4. [ ] 部署 backend（alert_router + listener 注册）
5. [ ] 部署 celery-worker（notify_control_group + check_anomaly_and_alert 任务可被发现）
6. [ ] 部署 celery-beat（check-anomaly-hourly schedule 生效）
7. [ ] 验证 /metrics 暴露 wecom_group_notify_total / wecom_anomaly_alert_total
8. [ ] 管理员配置 /api/wecom/alert-config（control_group_webhook + 阈值 + alert_recipients + is_enabled）

---

## 3. 验证步骤（部署后）

1. GET /api/openapi.json 含 /api/reports... 与 `/api/wecom/alert-config`
2. 未登录调 alert-config → 401
3. admin PUT alert-config（return_rate_threshold=0.4 + recipients + webhook）→ 200；GET 回显 webhook 脱敏（末 6 位）
4. 触发一条 promotion publish（含 publish_url）→ 观察 celery-worker 日志 notify_control_group + 群机器人收到控评消息（或 webhook 未配置时 status=unconfigured warning）
5. 手动触发 check_anomaly_and_alert（或等整点）→ 构造退货率 >0.40 的投产数据 → 管理群收到预警 + wecom_alert_log 落一条
6. 二次触发同款式 → 去重 status=deduped，不重复推送
7. 调整阈值后下次运行立即生效（无缓存）
8. Sentry 验证：模拟任务异常 → capture 上报
9. 多租户：tenant A 配置不影响 tenant B；RLS 隔离
10. migration 回滚演练（staging）：down 019 → 升回 019，无数据破坏
11. 关闭 is_enabled → check_anomaly_and_alert 跳过该租户

---

## 4. 监控

| 指标 | 关注 |
|---|---|
| wecom_group_notify_total{status} | failed/unconfigured 占比（控评通知健康度） |
| wecom_anomaly_alert_total{alert_type,status} | sent/deduped/no_recipient/failed 分布 |
| wecom_send_duration_seconds | 自建应用推送耗时 |
| Sentry Celery 任务异常 | notify_control_group / check_anomaly_and_alert 失败 |

告警建议：failed 持续升高 → 检查企微凭据/频控/webhook 有效性。

---

## 5. 回滚

- 代码回滚：撤销 alert_router + listener 注册 + Beat schedule（功能下线，promotion 发布与报表不受影响）。
- DB 回滚：migration 019 down（drop 2 表 + 删 scope），无回填、无外键被引用，安全幂等。
- 配置回滚：is_enabled=false 可快速停用预警而不下线代码。

---

## 6. 一致性

- 与 infrastructure-design.md 一致（无新服务 + migration 019 + Beat 1 条）。
- 与 U01/U07 部署架构一致（复用 Zeabur 6 服务 + celery 队列 + Sentry + 企微出站）。
- V1 收官单元：部署后 V1 全部 8 个 sub-unit 交付完成。
