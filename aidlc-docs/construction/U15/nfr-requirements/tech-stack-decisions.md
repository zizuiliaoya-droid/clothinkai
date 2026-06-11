# U15 技术栈决策（企微进阶）

> 零新依赖：复用 U07 WecomClient/httpx/cryptography + core/events + U14 ProductionService + U01 Celery/Sentry/metrics。

---

## 1. 依赖

**无新增第三方依赖。** 全部复用：
- `httpx`（WecomClient 已有）
- `cryptography`（webhook V1 明文，U16+ 升级时复用 crypto.py）
- Celery + Beat（U01）
- prometheus Counter（U01 metrics）

---

## 2. 文件落点（modules/wecom 追加）

### 新建（6）
| 文件 | 内容 |
|---|---|
| `alert_models.py` | WecomAlertConfig + WecomAlertLog ORM（TenantScopedModel + RLS） |
| `alert_schemas.py` | AlertConfigUpdate（阈值校验）+ AlertConfigResponse（webhook 脱敏） |
| `alert_config_service.py` | get_response / upsert（ON CONFLICT tenant_id）+ 阈值/webhook 校验 |
| `group_notify_service.py` | notify_publish（重读校验 + send_group_robot best-effort + 指标） |
| `anomaly_service.py` | check_and_alert（get_report → 阈值判定 → 去重 → send_app_message → log） |
| `alert_api.py` | GET/PUT /api/wecom/alert-config（require_permission） |

### 横切修改
| 文件 | 改动 |
|---|---|
| `wecom/client.py` | +send_group_robot(webhook, markdown) 直连无 token / +send_app_message(touser, markdown) /cgi-bin/message/send |
| `wecom/enums.py` | +AlertType（return_rate_high/roi_low/conversion_low） |
| `wecom/permissions.py` | +WECOM_ALERT_CONFIG_READ/WRITE scope 常量 |
| `wecom/listeners.py` | +on_promotion_published（enqueue notify_control_group）+ register() 追加 subscribe |
| `wecom/deps.py` | +AlertConfigServiceDep |
| `tasks/wecom_tasks.py` | +notify_control_group(promotion_id, tenant_id) / +check_anomaly_and_alert() 逐租户 |
| `core/metrics.py` | +wecom_group_notify_total / +wecom_anomaly_alert_total（+ __all__） |
| `core/celery_app.py` | Beat +check-anomaly-hourly（minute=0, default 队列）；autodiscover 已含 wecom_tasks |
| `main.py` | register_event_listeners 注册 wecom on_promotion_published（通知类，缺失只 warning）+ 挂 alert_router |
| `tests/conftest.py` | 追加 wecom.alert_models import（mapper 完整性） |

---

## 3. 客户端方法实现要点

```python
# send_group_robot：直连 webhook（无 access_token）
async def send_group_robot(self, webhook_url: str, markdown: str) -> dict:
    data = (await self._http.post(
        webhook_url, json={"msgtype": "markdown", "markdown": {"content": markdown}}
    )).json()
    if data.get("errcode"):
        raise WecomApiError(data["errcode"], data.get("errmsg"))
    return data

# send_app_message：自建应用（复用 _call 的 token + 频控）
async def send_app_message(self, touser: list[str], markdown: str) -> dict:
    with wecom_send_duration_seconds.time():
        return await self._call("POST", "/cgi-bin/message/send", json={
            "touser": "|".join(touser),
            "agentid": int(self._cfg.agent_id),
            "msgtype": "markdown",
            "markdown": {"content": markdown},
        })
```

---

## 4. 指标定义（core/metrics.py 追加）

```python
wecom_group_notify_total = Counter(
    "wecom_group_notify_total",
    "Total wecom group-robot control-comment notifications",
    labelnames=("status",),  # sent/failed/unconfigured/skipped
)
wecom_anomaly_alert_total = Counter(
    "wecom_anomaly_alert_total",
    "Total wecom anomaly alerts pushed to management group",
    labelnames=("alert_type", "status"),  # status: sent/failed/no_recipient/deduped
)
```

---

## 5. migration 019 片段

```python
revision = "019_u15_create_wecom_alert_tables"
down_revision = "018_u14_create_report_tables"

# wecom_alert_config：base_cols + control_group_webhook Text null +
#   return_rate_threshold Numeric(5,4) NOT NULL DEFAULT 0.4000 +
#   low_roi_threshold Numeric(8,4) null + low_conversion_threshold Numeric(5,4) null +
#   alert_recipients JSONB NOT NULL DEFAULT '[]' + is_enabled Boolean DEFAULT true
#   UNIQUE(tenant_id)
# wecom_alert_log：base_cols + alert_type String(24) + entity_type String(24) null +
#   entity_ref String(64) null + period_key String(10) + detail JSONB DEFAULT '{}' +
#   fired_at DateTime(tz) DEFAULT now()
#   UNIQUE(tenant_id, alert_type, entity_ref, period_key) + idx(tenant_id, fired_at)
# enable_rls_sql 两表
# seed: wecom.alert_config:read/write（operations 显式 + admin 通配）
```

- revision id `"019_u15_create_wecom_alert_tables"`，down_revision `"018_u14_create_report_tables"`。
- 复用 `from app.core.security.rls import disable_rls_sql, enable_rls_sql`。
- permission/role_permission seed 用 ON CONFLICT 幂等模式（同既有 migration）。

---

## 6. Beat schedule（celery_app.py 追加）

```python
# U15 每小时异常预警监控（default 队列，与 09:00 催发/02:00 采集错峰）
"check-anomaly-hourly": {
    "task": "app.tasks.wecom_tasks.check_anomaly_and_alert",
    "schedule": crontab(minute=0),
    "options": {"queue": "default"},
},
```

---

## 7. 测试落点

| 文件 | 重点 |
|---|---|
| `tests/unit/test_anomaly_rules.py` | 阈值判定（边界 == 不触发 / > 触发 / null 不检 / conversion 占位） |
| `tests/integration/test_wecom_alert.py` | config upsert + 即时生效 + check_and_alert 端到端 + 去重 + no_recipient + RLS |
| `tests/api/test_wecom_alert_api.py` | GET/PUT 401 + OpenAPI + webhook 脱敏 |

- monkeypatch WecomClient.send_group_robot / send_app_message 避免真实 HTTP。
- 复用 conftest fixtures：session/tenant_a/factory/admin_role/operations_role/product_factory/promotion_factory + U14 投产数据构造（platform_product + qianniu_daily + ad_daily）。

---

## 8. 本地验证环境

- Docker PG16:5558 + Redis7:6413 + Py3.12（U15 唯一端口）。
- alembic upgrade head 含 019；U15 子集 + 全量回归；覆盖率 ≥70%。
