# U15 领域实体（企微进阶：发文通知控评 + 异常预警）

> 单元：U15（EP08-S09、S10 + EP10-NFR06）
> 模块归属：复用 `modules/wecom`，追加 2 表 + 客户端 2 方法 + 3 service + 1 listener + 1 Celery 任务
> 依赖：U07（WecomConfig/WecomClient/事件总线）、U14（ProductionService 投产数据源）

---

## 1. 实体总览

| 实体 | 表名 | 用途 | 关键约束 |
|---|---|---|---|
| WecomAlertConfig | `wecom_alert_config` | 控评群机器人 webhook + 异常预警阈值 + 接收人 + 开关 | UNIQUE(tenant_id) 单租户单条 |
| WecomAlertLog | `wecom_alert_log` | 异常预警去重留痕 | UNIQUE(tenant_id, alert_type, entity_ref, period_key) |

两表均继承 `TenantScopedModel`（U01）：自动 id(UUID PK) + tenant_id(FK + ORM 钩子) + created_at/updated_at，启用 RLS。

---

## 2. WecomAlertConfig（企微预警配置，单租户单条）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | TenantScopedModel |
| tenant_id | UUID | FK tenant, NOT NULL, UNIQUE | 每租户单条 |
| control_group_webhook | Text | NULL | S09 控评群机器人完整 webhook URL（含 key），密文非必须（webhook 本身是凭据，建议加密；V1 明文存 + RLS 隔离，文档标注 U16 可升级加密） |
| return_rate_threshold | Numeric(5,4) | NOT NULL, DEFAULT 0.4000 | S10 退货退款率阈值（> 触发，默认 40%） |
| low_roi_threshold | Numeric(8,4) | NULL | S10 净投产比下限（< 触发；null=不检） |
| low_conversion_threshold | Numeric(5,4) | NULL | S10 加购转化率下限（V1 口径缺失占位；null=不检） |
| alert_recipients | JSONB | NOT NULL, DEFAULT '[]' | S10 管理群接收人企微 userid 数组 |
| is_enabled | Boolean | NOT NULL, DEFAULT true | 预警总开关（Beat 仅遍历启用租户） |

索引：`uq_wecom_alert_config_tenant` UNIQUE(tenant_id)。

### 设计要点
- 控评（S09）与预警（S10）配置合并一表，减少表数量；语义独立字段分组。
- `return_rate_threshold` 给默认 0.40（GWT「默认 40%」）；其余阈值可选（null 关闭该类检测）。
- `alert_recipients` 为 userid 数组（自建应用 `/cgi-bin/message/send` 的 touser）；空数组 → S10 跳过推送（仅 log 警告）。

---

## 3. WecomAlertLog（异常预警去重留痕）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | TenantScopedModel |
| tenant_id | UUID | FK tenant, NOT NULL | RLS |
| alert_type | String(24) | NOT NULL | 异常类型（见 AlertType 枚举） |
| entity_type | String(24) | NULL | 异常实体类型（默认 'style'） |
| entity_ref | String(64) | NULL | 异常实体引用（style_id 字符串；汇总类预警可空） |
| period_key | String(10) | NOT NULL | 去重周期键（当日 'YYYY-MM-DD'） |
| detail | JSONB | NOT NULL, DEFAULT '{}' | 异常详情快照（指标值/阈值/款号/建议） |
| fired_at | DateTime(tz) | NOT NULL, server_default now() | 触发时间 |

索引：
- `uq_wecom_alert_log` UNIQUE(tenant_id, alert_type, entity_ref, period_key) — 同款式同类型当日只推一次
- `idx_wecom_alert_log_fired` (tenant_id, fired_at)

### 设计要点
- `period_key` = 触发当天日期字符串 → 配合 UNIQUE 实现「同一异常每天至多推一次」去重。
- `entity_ref` 可空（UNIQUE 列含 NULL 时 PostgreSQL 默认每行不相等，故汇总类预警 entity_ref 用固定字符串 'GLOBAL' 而非 NULL，确保去重生效）。
- `detail` 留痕便于审计与前端回溯（V1 不建预警查询 API，仅留表）。

---

## 4. AlertType 枚举（预警类型）

| 值 | 含义 | 判定 | 数据源 |
|---|---|---|---|
| `return_rate_high` | 退货退款率过高 | return_rate > return_rate_threshold | U14 ProductionRow.return_rate |
| `roi_low` | 净投产比过低 | net_roi < low_roi_threshold（阈值非空） | U14 ProductionRow.net_roi |
| `conversion_low` | 加购转化率过低（V1 占位） | V1 口径缺失，默认不检 | 占位 |

---

## 5. WecomClient 客户端 2 新方法

| 方法 | 签名（语义） | 企微 API | 鉴权 |
|---|---|---|---|
| send_group_robot | `(webhook_url: str, markdown: str) -> dict` | 群机器人 `webhook/send` msgtype=markdown | webhook URL 自带 key，无需 access_token |
| send_app_message | `(touser: list[str], markdown: str) -> dict` | 自建应用 `/cgi-bin/message/send` agentid + msgtype=markdown | access_token（复用缓存 + 频控处理） |

- `send_group_robot` 直连传入的完整 webhook URL（不拼 WECOM_API_BASE）；错误码非 0 → WecomApiError（由调用方 best-effort 捕获）。
- `send_app_message` touser 用 `|` 连接；复用 `_call` 的 token 刷新 + 频控（WecomRateLimited）逻辑。

---

## 6. 组件清单（新建 / 修改）

### 新建（modules/wecom）
| 文件 | 职责 |
|---|---|
| `alert_models.py` | WecomAlertConfig + WecomAlertLog ORM |
| `alert_schemas.py` | AlertConfigUpdate / AlertConfigResponse |
| `alert_config_service.py` | get/update 配置（阈值 + webhook + 接收人） |
| `group_notify_service.py` | S09 控评通知（校验 publish + 发 group_robot best-effort） |
| `anomaly_service.py` | S10 投产聚合 → 阈值判定 → 去重 → 自建应用推送 |
| `alert_api.py` | GET/PUT /api/wecom/alert-config |

### 修改（横切）
| 文件 | 改动 |
|---|---|
| `wecom/client.py` | +send_group_robot / +send_app_message |
| `wecom/enums.py` | +AlertType |
| `wecom/permissions.py` | +wecom.alert_config:read/write |
| `wecom/listeners.py` | +on_promotion_published（enqueue notify_control_group） |
| `wecom/deps.py` | +AlertConfigServiceDep |
| `tasks/wecom_tasks.py` | +notify_control_group（S09 任务）+check_anomaly_and_alert（S10 Beat 逐租户） |
| `core/metrics.py` | +wecom_group_notify_total / +wecom_anomaly_alert_total |
| `core/celery_app.py` | Beat 注册 check_anomaly_and_alert（每小时） |
| `main.py` | register_event_listeners 注册 wecom on_promotion_published + 挂 alert_router |
| `alembic/versions/019_*.py` | 2 表 + RLS + 2 scope seed |

---

## 7. ER 关系

```
tenant 1───* wecom_alert_config（UNIQUE tenant，单条）
tenant 1───* wecom_alert_log（每异常每日一条）

PromotionPublished(event, U04) ──listener──> notify_control_group(Celery)
                                                  └─读 promotion + wecom_alert_config.control_group_webhook
ProductionService.get_report(U14) ──> AnomalyAlertService ──> wecom_alert_log + send_app_message
```

---

## 8. 演化说明
- control_group_webhook V1 明文存（RLS + 仅 admin 可读写）；U16+ 可升级 AES-GCM 加密（复用 crypto.py）。
- conversion_low 待 U13/U16 补齐加购转化口径后启用（当前占位 null 不检）。
- 预警查询/已读 API 不在 V1（仅落 wecom_alert_log 留痕）；U17 BI 看板可消费。
