# 服务层与编排（Services）

> 列出跨组件编排场景，标注事务边界、异步/同步、关键 Service 协作。

## 1. 服务分类

| 类别 | 说明 | 示例 |
|---|---|---|
| **业务 Service**（同步） | 单一模块内编排，HTTP 请求线程内完成 | UserService, StyleService, PromotionService |
| **跨模块编排 Service**（同步） | 跨模块同步流程 | PromotionReviewOrchestrator（推广审核 → 触发结算生成） |
| **后台任务**（异步 / Celery） | 长任务、定时任务、外部调用密集 | scan_and_dispatch_urge, execute_wecom_message, run_import_batch |
| **领域服务**（无状态） | 纯计算函数，多 Service 共用 | UrgeStatusCalculator, MetricService |

---

## 2. 关键编排场景

### 2.1 推广审核 → 自动生成结算

**故事**：EP05-S13 + EP06-S02 (Journey J2 + J4)

**触发**：PR 主管调用 `POST /api/promotions/{id}/review` action="approve"

**编排步骤**（同步）：
```
PromotionService.review(action="approve")
  ├── Domain: PromotionStateMachine.transition("review_approve")
  │       ├── 校验 publish_status = 已发布
  │       └── settlement_status: 未结算 → 待付款
  ├── PromotionRepository.save(promotion)
  └── SettlementService.create_from_promotion(promotion_id)
          ├── 幂等检查（按 promotion_id）
          ├── 计算金额（promotion.quote_amount + 配置项）
          ├── SettlementRepository.create(settlement)
          └── NotificationService.notify(pr_managers, "新待付款结算")
```

**事务边界**：单个数据库事务（promotion update + settlement insert 原子）  
**单元归属**：U04 + U05

### 2.2 推广创建 → 实时计算 urge_status + 重复检测

**故事**：EP05-S02, S04, S05, S06

**编排**（同步）：
```
PromotionService.create(payload)
  ├── StyleRepository.get_by_code(payload.style_code)        # 自动填充
  ├── BloggerRepository.get(payload.blogger_id)
  ├── PromotionRepository.find_existing_active(style, blogger)
  │     └── 若存在 → 抛 RepeatedPromotionWarning（不阻塞，前端确认）
  ├── _check_dual_platform(style_id) → bool
  ├── _generate_internal_code()
  ├── PromotionRepository.create(promotion)
  └── 返回 Promotion（urge_status 在序列化时通过 UrgeStatusCalculator 实时算）
```

**单元归属**：U04

### 2.3 凭据解密 → 采集 Worker 拉任务

**故事**：EP07-S04 + EP07-S11~S13 (Journey J3)

**编排**（异步 + 同步混合）：
```
[Celery Beat 定时器，每天 02:00]
CrawlerTaskService.schedule_daily_tasks()
  ├── CredentialRepository.list_active_for_tenant(...)
  └── CrawlerTaskRepository.create(...)        # status=pending

[Worker 端异步轮询]
POST /api/crawler/tasks/poll
  └── CrawlerTaskService.poll_next_task(worker_id)
        ├── 锁 + SELECT FOR UPDATE SKIP LOCKED 选 1 个 pending
        ├── @audit("decrypt") CredentialService.decrypt_for_purpose(cred_id, "crawl")
        │       ├── core/security/crypto.decrypt_credential(...)
        │       └── AuditService.log("decrypt", credential_id, purpose, ...)
        ├── 更新 task.status = "running"
        └── 返回 (task_id, platform, account, decrypted_password)

[Worker 完成后]
POST /api/crawler/tasks/{id}/result + multipart 文件
  └── CrawlerTaskService.report_result(...)
        ├── 校验 worker_id 与 task 匹配
        ├── 更新 task.status = "success" / "failed"
        ├── if 成功:
        │     └── ImportService.upload(file, source) → 异步 run_import_batch
        └── if 失败:
              └── CredentialService.report_failure(cred_id, error)
                    ├── 失败计数 +1
                    ├── if 计数 >= 3: pause + 企微告警
                    └── 写 data_quality_issue
```

**事务边界**：每个步骤独立事务  
**单元归属**：U06a, U12, U13

### 2.4 自动催发扫描 → 企微群发 → 频控降级

**故事**：EP08-S05, S06, S07 (Journey J2)

**编排**（异步）：
```
[Celery Beat 每天 09:00]
scan_and_dispatch_urge()
  ├── PromotionRepository.find_urge_candidates()  # urge_status ∈ {催发, 重要催发, 超时}
  ├── 按 (blogger_id, pr_id) 聚合
  └── for each:
        └── WecomMessageRepository.create(status="pending")

[Celery Worker 消费]
execute_wecom_message(message_id)
  ├── WecomService.check_rate_limit(blogger_id, pr_id, today)
  │     ├── if 博主已收 1 条 / day: rate_limited
  │     └── if PR 已发 1 次 / day: rate_limited
  ├── if rate_limited:
  │     ├── 更新 wecom_message.status = "rate_limited"
  │     └── NotificationService.notify(pr, "请手动催发 {博主}")
  └── else:
        ├── 模板渲染（替换 {博主昵称} {商品简称} ...）
        ├── WecomClient.send_external_msg_template(...)
        └── 更新 wecom_message.status = "created"（待 PR 在企微端确认）

[企微回调]
POST /api/wecom/callback
  ├── 校验签名（WecomClient.verify_callback_signature）
  └── WecomService.handle_callback(msg_id, result)
        └── 更新 wecom_message.status = sent / rejected / failed
```

**事务边界**：每个 message 独立事务  
**单元归属**：U07

### 2.5 数据导入：批次 → 适配器分发 → 校验 → 入库

**故事**：EP07-S07~S10

**编排**（异步）：
```
[同步触发]
ImportService.upload(file, source)
  ├── 计算 file_hash
  ├── if 已存在 batch with same hash → return 409
  ├── 上传到 R2 attachment
  ├── ImportBatchRepository.create(status="processing")
  ├── 异步触发: run_import_batch.delay(batch_id)
  └── return batch（前端轮询查状态）

[Celery Worker]
run_import_batch(batch_id)
  ├── 加载 batch + attachment
  ├── adapter = ImportAdapterRegistry.get(source)
  ├── 加载 active FieldMapping
  ├── 解析行 → for each row:
  │     ├── ImportJobRepository.create(row_number, status="processing")
  │     ├── try:
  │     │     ├── parsed = adapter.parse_row(row, mapping)
  │     │     ├── errors = adapter.validate(parsed)
  │     │     ├── if errors: raise
  │     │     ├── adapter.upsert(parsed)
  │     │     └── job.status = "success"
  │     └── except:
  │           ├── job.status = "failed"
  │           ├── job.error_detail = str(e)
  │           └── DataQualityService.record_issue(...)
  └── 更新 batch.status = "completed" / "partial_failed" / "failed"
```

**事务边界**：每行独立事务（一行失败不影响其他）  
**单元归属**：U06a + U06b/c/d/e + U13

### 2.6 设计制版状态推进 + 通知

**故事**：EP03-S02~S14 (Journey J1)

**编排**（同步）：
```
DesignService.<action>(style_id, payload)
  ├── style = StyleRepository.get(style_id)
  ├── DesignStateMachine(style).transition(<action>, actor=user, **payload)
  │     ├── 校验 actor 角色匹配 transition_table
  │     ├── 校验 required_fields 完整
  │     ├── 写入子表（style_fabric / style_pattern / style_craft）
  │     ├── 更新 style.design_status
  │     └── 收集 side_effects（"notify_designer", "notify_pattern_maker"...）
  ├── StyleRepository.save(style)
  ├── for each side_effect:
  │     └── NotificationService.notify(target_role_users, message)
  └── return style
```

**事务边界**：单事务（status update + 子表 + notification 一起）  
**单元归属**：U10a

### 2.7 异常监控告警

**故事**：EP08-S10

**编排**（异步）：
```
[Celery Beat 每小时]
check_anomaly_and_alert()
  ├── for tenant in active_tenants:
  │     ├── thresholds = ConfigService.get_thresholds(tenant)
  │     ├── return_rate = MetricService.return_rate(...)
  │     ├── if return_rate > threshold.return_rate_warn:
  │     │     └── WecomClient.push_to_app(admin_group, alert_msg)
  │     ├── (transition_drop, net_roi_low ... 类似)
  └── 写 anomaly_log
```

**单元归属**：U15

### 2.8 备份与恢复

**故事**：EP10-NFR04

**编排**（异步）：
```
[Celery Beat 每天 03:00]
backup_database()
  ├── pg_dump 输出到本地临时文件
  ├── 上传到 R2 backups/ 桶（按 tenant 隔离 + 全局合集）
  ├── 删除本地临时文件
  ├── 清理 R2 中超过 30 天的每日备份（保留每月 1 份 1 年）
  └── 失败时通过企微 push_to_app 告警
```

**单元归属**：U07（同 NFR04）

---

## 3. 事务边界规则

| 场景 | 边界 |
|---|---|
| 单一 ORM CRUD | 由 Repository 包内 transaction 自动管理 |
| 跨模块同步编排（如 promotion review → settlement create） | Service 层显式 `async with session.begin()`，原子提交 |
| 异步任务每个 unit of work | 单独事务（如导入每行独立事务） |
| 状态机转移 + 子表写 + 通知 | 单事务（通知失败不回滚状态变更，通知改异步重试） |
| audit_log 写入 | 与主操作同事务（保证审计一致） |

---

## 4. 异步任务清单（Q5=A 决策）

> 走 Celery 异步队列：定时任务 + 长任务 + 外部调用密集任务

| 任务 | 触发 | 队列 | 单元归属 |
|---|---|---|---|
| `scan_and_dispatch_urge` | Beat 每天 09:00 | wecom | U07 |
| `execute_wecom_message(id)` | API 触发 / Beat | wecom | U07 |
| `check_anomaly_and_alert` | Beat 每小时 | monitor | U15 |
| `backup_database` | Beat 每天 03:00 | backup | U07 / U10-NFR04 |
| `run_import_batch(id)` | API 触发（upload 后） | importer | U06a |
| `schedule_daily_crawl_tasks` | Beat 每天 02:00 | crawler | U13 |
| `cleanup_expired_credentials_audit` | Beat 每月 | audit | U12 |
| `recompute_blogger_tags(tenant_id)` | API（阈值变更后） | bloggers | U11 |
| `precompute_report_cache` | Beat 每小时（V1 后） | report | U14 |
| `ai_advisory_request(payload)` | API 触发 | ai | U18 |

> 其他所有写操作（CRUD、状态推进）保持同步 API，避免引入不必要复杂度。

### Celery 队列拆分（v1+ 优化）

```
- default        # 通用
- importer       # 导入解析（重 IO）
- crawler        # 采集相关
- wecom          # 企微调用（受外部限速）
- monitor        # 监控告警
- backup         # 备份
- ai             # AI 调用
```

---

## 5. 服务编排矩阵（按工作单元）

| 单元 | 主要 Service | 依赖的横切组件 | 异步任务 |
|---|---|---|---|
| U01 | AuthService, UserService, PermissionService | tenancy, audit, security/permissions | — |
| U02 | StyleService, SkuService | attachment（主图） | — |
| U03 | BloggerService | — | — |
| U04 | PromotionService, UrgeStatusCalculator | state_machine | — |
| U05 | SettlementService（依赖 PromotionService） | state_machine, attachment（截图） | — |
| U06a | ImportService, FieldMappingService, ImportAdapterRegistry | attachment | run_import_batch |
| U06b-e | StyleSku/Blogger/Promotion/SettlementImportAdapter | — | — |
| U07 | WecomClient, WecomService, WecomConfigService | — | scan_and_dispatch_urge, execute_wecom_message |
| U08 | PublishProgressService（依赖 MetricService） | — | — |
| U09 | PermissionService（字段级 + 自定义） | security/permissions | — |
| U10a | DesignService, NotificationService | state_machine, attachment（设计稿/版型） | — |
| U10b | PlatformProductService | — | — |
| U11 | BloggerTagService（依赖 MetricService.blogger_quality） | — | recompute_blogger_tags |
| U12 | CredentialService | security/crypto, audit | cleanup_expired_credentials_audit |
| U13 | CrawlerTaskService + 各平台 ImportAdapter | tenancy, audit | schedule_daily_crawl_tasks |
| U14 | WorkProgressService, TargetPlanningService, StoreDailyService, ProductionService | — | precompute_report_cache |
| U15 | WecomService（异常预警） | — | check_anomaly_and_alert |
| U16 | OrderAdjustmentService, BalanceService | — | — |
| U17 | BundleService, ReportExportService | — | — |
| U18 | AiAdvisoryService, DeepSeekClient | — | ai_advisory_request |

---

## 6. 跨服务通信模式

| 通信 | 模式 | 例子 |
|---|---|---|
| 同模块内 | Python 函数调用 | UserService → UserRepository |
| 跨模块同步 | Python 函数调用，引入接口 Protocol 解耦 | PromotionService → SettlementService.create_from_promotion |
| 模块到 Celery 任务 | `task.delay(...)` 异步入队 | ImportService.upload → run_import_batch.delay |
| 主系统 ↔ 采集 Worker | HTTP API（pull + 回传） | Worker poll_next_task / report_result |
| 主系统 ↔ 企微 | HTTPS REST API | WecomClient → 企微 API |
| 主系统 ↔ DeepSeek | HTTPS REST API | DeepSeekClient → DeepSeek API |
| 缓存交互 | Redis 命令 | core/cache.py 封装 |

---

## 7. Service 层一致性校验

| 校验 | 结果 |
|---|---|
| 跨模块编排 Service 显式事务 | ✅ Session.begin() |
| 状态机变更与通知同事务（同步通知） | ✅ |
| 异步通知失败可重试 | ✅ Celery retry policy |
| 凭据解密所有路径都写 audit | ✅ @audit + AuditService.log 双重 |
| 导入失败行不影响成功行 | ✅ 行级独立事务 |
| Worker pull 模型 + HTTP 回传，无 Worker 直接 DB 访问 | ✅ |
| 频控规则在 WecomService 集中实现 | ✅ check_rate_limit 单一入口 |
