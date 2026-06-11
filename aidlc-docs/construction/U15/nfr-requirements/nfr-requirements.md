# U15 NFR 需求（企微进阶：发文通知控评 + 异常预警）

> 增量式：复用 U01 NFR 基线（多租户/Sentry/structlog/Celery）+ U07 企微 NFR（WecomClient/token 缓存/频控）。本文仅列 U15 特异 NFR。
> 单元：EP08-S09、S10 + EP10-NFR06

---

## 1. 性能

| 项 | 指标 | 说明 |
|---|---|---|
| S09 控评通知 | 无在线 SLA | 异步 Celery，不阻塞发布主流程；群机器人 HTTP 超时复用 WECOM_HTTP_TIMEOUT=10s |
| S10 监控任务 | 单租户 ≤5s 目标 | 1 次 ProductionService.get_report(last_7d)（U14 ≤800ms）+ N 款异常推送；无硬 SLA |
| 监控频率 | 每小时 1 次 | Beat crontab(minute=0)；全租户串行（V1 租户量级小） |
| 配置读写 | P95 ≤ 200ms | alert-config GET/PUT 单行 upsert，复用 U01 写 SLA |

- 异步边界：S09/S10 均 Celery 任务，在线请求仅配置读写。
- S09 best-effort 不重试（仅 OperationalError 入队失败 autoretry 1 次）；S10 推送失败不重试（下次 Beat 重试）。

---

## 2. 安全

### 2.1 webhook 凭据保护（S09）
- control_group_webhook 含 key 属凭据；V1 明文存 wecom_alert_config（RLS 隔离 + 仅 admin/operations 读写）。
- **不可回显完整**：GET 响应脱敏（webhook_configured: bool + 末 6 位），不返回完整 URL（BR-U15-44）。
- **不入日志**：log 仅记 tenant_id/status，绝不记 webhook URL（防 key 泄露）。
- 演化：U16+ 可升级 AES-GCM 加密存储（复用 crypto.py，与 credential 同模式）。

### 2.2 access_token 复用（S10）
- send_app_message 复用 WecomClient.get_access_token（Redis 缓存 7000s + 40014/42001 刷新重试一次）+ decrypt_credential（解密审计 system actor purpose="wecom_alert"）。无新密钥。

### 2.3 威胁模型
| 威胁 | 缓解 |
|---|---|
| 跨租户读写配置/预警 | alert_config/alert_log RLS + 任务 set_config('app.tenant_id') + 显式 WHERE tenant_id |
| webhook key 泄露 | 脱敏回显 + 不入日志 |
| 预警轰炸用户 | period_key 当日去重（同款式同类型每日 ≤1）+ is_enabled 总开关 |
| 接收人越权配置 | alert_recipients 仅 admin/operations 可写；推送 system actor 审计可溯 |

---

## 3. 可靠性与去重

- **去重并发安全**：wecom_alert_log UNIQUE(tenant, alert_type, entity_ref, period_key) DB 兜底；service 先 SELECT 查重 + INSERT；并发同 key IntegrityError catch → 视为 deduped。
- **逐租户容错**：单租户 check_and_alert 异常 catch + log + Sentry capture，不中止其余租户（同 U07 scan / U13 schedule）。
- **防回滚误发**（S09）：notify_control_group 任务重读 promotion 校验 publish_status='已发布' + publish_url 非空，否则 skip。
- **接收人为空**：S10 不落 alert_log（配置补齐后可补推）；S09 webhook 缺失仅 warning。

---

## 4. 多租户隔离

- alert_config/alert_log 继承 TenantScopedModel + RLS。
- Beat 任务 bypass 读 `wecom_alert_config WHERE is_enabled=true` 租户清单 → 逐租户 set_config + AsyncSessionApp。
- 测试用 bypass 角色（RLS OFF）→ 聚合/查重必须显式 WHERE tenant_id。

---

## 5. 可观测性（NFR06）

| 指标 | 类型 | labels | 用途 |
|---|---|---|---|
| wecom_group_notify_total | Counter | status(sent/failed/unconfigured/skipped) | S09 控评通知结果 |
| wecom_anomaly_alert_total | Counter | alert_type, status(sent/failed/no_recipient/deduped) | S10 异常预警结果 |
| wecom_send_duration_seconds | Histogram（复用 U07） | — | send_app_message 计时 |

- NFR06 系统监控：Celery 任务失败 → Sentry capture_exception（复用 U01）。
- NFR06 业务监控：异常预警推送本身即业务层主动告警（退货率/投产比超阈值）。

---

## 6. 数据迁移

- migration 019：wecom_alert_config（UNIQUE tenant + RLS）+ wecom_alert_log（UNIQUE 去重 + RLS + idx）2 表。
- scope seed：wecom.alert_config:read/write（admin 通配已覆盖 + operations 显式 seed）。
- 无回填（新表）；部署回滚安全（drop 2 表）。

---

## 7. 测试矩阵

| 层 | 文件 | 覆盖 |
|---|---|---|
| unit | test_anomaly_rules.py | 阈值判定纯逻辑（退货率>阈值命中 / roi<阈值命中 / 阈值 null 不检 / conversion 占位 / 边界等于阈值不触发） |
| integration | test_wecom_alert.py | alert_config upsert + 阈值即时生效；check_and_alert 端到端（投产数据→命中→推送 monkeypatch→落 log→二次运行 deduped skip）；接收人空 no_recipient；RLS 隔离 |
| api | test_wecom_alert_api.py | GET/PUT /api/wecom/alert-config 401 + OpenAPI 路径 + webhook 脱敏（不回显完整 URL） |

- S09：monkeypatch WecomClient.send_group_robot 验证 sent/failed/unconfigured 三分支 + 任务重读 skip。
- 覆盖率门 ≥70%（全量回归）。

---

## 8. 一致性校验

- 与 functional-design business-rules BR-U15-01~82 引用一致。
- 性能/安全口径与 U07 企微 NFR（token/频控/Sentry）一致，无重复造轮子。
- S10 数据口径完全复用 U14 ProductionService，无独立聚合。
