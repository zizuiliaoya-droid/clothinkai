# U15 代码生成计划（Code Generation Plan）

> 单元：U15 — 企微进阶（发文通知控评 + 异常预警推送）（EP08-S09、S10 + EP10-NFR06）
> 分批：**2 批** + Build & Test
> Build & Test：Docker PG16:5558 + Redis7:6413 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 复用 modules/wecom，追加 alert_models/alert_schemas/alert_config_service/group_notify_service/anomaly_service/alert_api 6 文件 + 横切 10 改动。
- [Answer] 2 新表 migration 019（wecom_alert_config UNIQUE tenant + wecom_alert_log UNIQUE 去重）。
- [Answer] S09 listener 仅入队 notify_control_group；任务重读校验防回滚；group_robot best-effort。
- [Answer] S10 check_anomaly_and_alert Beat hourly 逐租户；复用 ProductionService(last_7d)；阈值实时读 + period_key 去重 + 自建应用推送。
- [Answer] 客户端 send_group_robot/send_app_message；指标 2 个；权限 wecom.alert_config:read/write seed operations。

---

## 1. 步骤（2 批）

### Batch 1 — 模型 + Schema + 枚举 + 权限 + 异常 + 客户端 + repository + 指标
- [x] 1.1 modules/wecom/alert_models.py（WecomAlertConfig + WecomAlertLog ORM）
- [x] 1.2 modules/wecom/alert_schemas.py（AlertConfigUpdate/Response 脱敏）
- [x] 1.3 enums +AlertType / permissions +2 scope / exceptions +AlertConfigInvalidError
- [x] 1.4 client.py +send_group_robot/send_app_message
- [x] 1.5 repository.py +WecomAlertConfigRepository/WecomAlertLogRepository
- [x] 1.6 core/metrics.py +wecom_group_notify_total/wecom_anomaly_alert_total

### Batch 2 — Service + Deps + API + Listener + Tasks + 横切 + migration + 测试
- [x] 2.1 alert_config_service.py（upsert + 脱敏 + 校验 + 审计）
- [x] 2.2 group_notify_service.py（S09 重读校验 + best-effort）
- [x] 2.3 anomaly_service.py（S10 判定 + 去重 + 推送）
- [x] 2.4 deps.py +AlertConfigServiceDep / alert_api.py 2 端点
- [x] 2.5 listeners.py on_promotion_published + tasks/wecom_tasks 2 任务 + celery_app Beat
- [x] 2.6 main.py 注册 listener + 挂 alert_router
- [x] 2.7 alembic/versions/019 + conftest import + 3 测试文件

### Build & Test
- [x] B.1 Docker PG16:5558 + Redis7:6413；alembic upgrade head（含 019）；U15 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 2 批 + Build & Test。**
