# U15 逻辑组件（企微进阶）

> 单元：U15（EP08-S09、S10 + EP10-NFR06）
> 复用 modules/wecom，追加 6 新建组件 + 10 横切修改；无循环依赖。

---

## 1. 新建组件（modules/wecom，6）

| 组件 | 类型 | 职责 | 依赖 |
|---|---|---|---|
| `alert_models.py` | ORM | WecomAlertConfig + WecomAlertLog（TenantScopedModel + RLS） | core/db |
| `alert_schemas.py` | Pydantic | AlertConfigUpdate（校验）/ AlertConfigResponse（脱敏） | — |
| `alert_config_service.py` | Service | get_response（脱敏）/ upsert（ON CONFLICT + 校验 + 审计） | repository, AuditService |
| `group_notify_service.py` | Service | notify_publish（重读校验 + send_group_robot best-effort） | WecomClient, PromotionRepo, BloggerRepo, AlertConfigRepo |
| `anomaly_service.py` | Service | check_and_alert（投产聚合 → 判定 → 去重 → send_app_message） | ProductionService(U14), WecomClient, AlertConfigRepo, AlertLogRepo |
| `alert_api.py` | Router | GET/PUT /api/wecom/alert-config | deps, AlertConfigService |

---

## 2. 横切修改（10）

| 文件 | 改动 |
|---|---|
| `wecom/client.py` | +send_group_robot（直连 webhook 无 token）+ send_app_message（自建应用 _call） |
| `wecom/enums.py` | +AlertType（return_rate_high / roi_low / conversion_low） |
| `wecom/permissions.py` | +WECOM_ALERT_CONFIG_READ/WRITE scope 常量 |
| `wecom/repository.py` | +WecomAlertConfigRepository（get/upsert 用 service 内 pg_insert）+ WecomAlertLogRepository（exists/add） |
| `wecom/listeners.py` | +on_promotion_published（enqueue notify_control_group）；register() 追加 subscribe |
| `wecom/deps.py` | +AlertConfigServiceDep |
| `wecom/exceptions.py` | +AlertConfigInvalidError（400） |
| `tasks/wecom_tasks.py` | +notify_control_group（S09 任务，autoretry OperationalError 1）+check_anomaly_and_alert（S10 Beat 逐租户容错） |
| `core/metrics.py` | +wecom_group_notify_total{status} / +wecom_anomaly_alert_total{alert_type,status}（+ __all__） |
| `core/celery_app.py` | Beat +check-anomaly-hourly（crontab minute=0, default 队列）；autodiscover 已含 wecom_tasks |
| `main.py` | register_event_listeners 注册 wecom on_promotion_published（通知类，缺失只 warning）+ 挂 alert_router |
| `tests/conftest.py` | 追加 `import app.modules.wecom.alert_models`（mapper 完整性） |

> 注：repository/exceptions/conftest 与上表合并计 10 项主改动（exceptions 与 repository 同模块）。

---

## 3. 依赖图（无循环）

```
alert_api → AlertConfigService → WecomAlertConfigRepository → alert_models
                               → AuditService(U01)

listeners.on_promotion_published → notify_control_group.delay (Celery)
notify_control_group(task) → GroupNotifyService → WecomClient.send_group_robot
                                                 → PromotionRepo(U04) / BloggerRepo(U03)
                                                 → AlertConfigRepo

check_anomaly_and_alert(task) → AnomalyAlertService
        → ProductionService(U14) → ProductionRepository → qianniu_daily/ad_daily/promotion(U13/U04)
        → WecomClient.send_app_message → WecomConfig(U07) + decrypt_credential(U01)
        → WecomAlertConfigRepo / WecomAlertLogRepo
```

依赖层级：U15 → U07（wecom 基础）→ U01；U15 → U14 → U13/U05 → U01。无环（U04/U03 仅被读，不反向依赖 U15）。

---

## 4. migration 019 DDL 概要

```
revision = "019_u15_create_wecom_alert_tables"
down_revision = "018_u14_create_report_tables"

wecom_alert_config（base_cols + ）:
  control_group_webhook Text NULL
  return_rate_threshold Numeric(5,4) NOT NULL DEFAULT 0.4000
  low_roi_threshold Numeric(8,4) NULL
  low_conversion_threshold Numeric(5,4) NULL
  alert_recipients JSONB NOT NULL DEFAULT '[]'
  is_enabled Boolean NOT NULL DEFAULT true
  UNIQUE(tenant_id)  [uq_wecom_alert_config_tenant]

wecom_alert_log（base_cols + ）:
  alert_type String(24) NOT NULL
  entity_type String(24) NULL
  entity_ref String(64) NULL
  period_key String(10) NOT NULL
  detail JSONB NOT NULL DEFAULT '{}'
  fired_at DateTime(tz) NOT NULL server_default now()
  UNIQUE(tenant_id, alert_type, entity_ref, period_key)  [uq_wecom_alert_log]
  INDEX(tenant_id, fired_at)  [idx_wecom_alert_log_fired]

enable_rls_sql("wecom_alert_config"); enable_rls_sql("wecom_alert_log")
seed: wecom.alert_config:read/write（permission ON CONFLICT(scope) +
      role_permission operations 显式；admin 通配 "*" 已覆盖）
```

---

## 5. 启动序列影响

- `main.register_event_listeners()`：在 finance/promotion register() 之后追加 `wecom.listeners.register()`（含 PromotionPublished + 既有 SettlementPaid）。通知类，PromotionPublished 无 handler 时不报错（required_handler=False）。
- Beat：worker 启动加载 check-anomaly-hourly（与 09:00 催发 / 02:00 采集 / 03:00 备份错峰）。
- autodiscover：wecom_tasks 已在 celery_app include 列表（U07），新增任务自动可被 .delay 发现。

---

## 6. 测试组件映射（3 文件）

| 测试文件 | 目标组件 | 用例要点 |
|---|---|---|
| `tests/unit/test_anomaly_rules.py` | AnomalyAlertService._evaluate_row | 退货率 > 阈值命中 / == 阈值不触发 / roi < 阈值命中 / low_roi_threshold null 不检 / conversion 占位不检 |
| `tests/integration/test_wecom_alert.py` | AlertConfigService + AnomalyAlertService | upsert + 阈值即时生效；check_and_alert 端到端（投产数据→命中→monkeypatch send_app_message→落 log）；二次运行 deduped；alert_recipients 空 no_recipient 不落 log；RLS 租户隔离 |
| `tests/api/test_wecom_alert_api.py` | alert_api | GET/PUT /api/wecom/alert-config 401（无 token）+ OpenAPI 路径 + webhook 脱敏（响应不含完整 URL） |

- monkeypatch WecomClient.send_group_robot / send_app_message 避免真实企微 HTTP。
- S09 group_notify_service 在 integration 测 notify_publish 的 unconfigured/sent/skipped 分支（monkeypatch + 构造 promotion）。

---

## 7. 一致性校验

- 与 nfr-design-patterns P-U15-01~05 伪代码组件一致。
- 与 functional-design domain-entities 组件清单（6 新建 + 横切）一致。
- 复用 U07 Celery 逐租户 + best-effort 模式、U14 ProductionService、U01 events/metrics/Sentry，无重复实现。
- 依赖图无循环（拓扑：U01 → U07 → U15；U13/U05 → U14 → U15）。
