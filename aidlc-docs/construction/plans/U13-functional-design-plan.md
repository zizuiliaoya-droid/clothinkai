# U13 功能设计计划（Functional Design Plan）

> 单元：U13 — 自动数据采集 Worker（EP07-S11~S14）
> 依赖：U06a-e（导入框架+适配器）、U10b（platform_product 反查）、U12（凭据）
> 被依赖：U14（报表读 qianniu_daily/ad_daily）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：U13 落在哪个模块？
[Answer] 复用 `modules/importer/`（追加 crawler_task_service.py / data_quality_service.py / worker_token_service.py / 3 adapters）+ `modules/product/`（qianniu_daily/ad_daily 数据表归 product 模块或新建 modules/collect？）→ 决策：新建 `modules/collect/`（采集专用：crawler_task/worker_token/data_quality_issue/qianniu_daily/ad_daily 模型 + 服务 + Worker API），3 adapters 放 `modules/importer/adapters/`（与现有适配器并列，注册到 ImportAdapterRegistry）。

### Q2：Worker pull 模型如何实现安全边界？
[Answer] 按 unit-of-work §2.2.1 落地：
- **WorkerToken 表**：token_hash(sha256)/name/ip_allowlist(JSONB)/is_active/consecutive_auth_failures；管理员签发/吊销。
- Worker API `/api/crawler/*` 用 `X-Worker-Token` 头鉴权（独立于 JWT）+ IP allowlist 校验。
- poll 响应中 password 是**一次性 cred_token**（非明文）；Worker 调 exchange 才换明文；exchange 立即失效 token + 5 分钟 TTL。
- poll/exchange/result 各写 audit_log。
- worker_token 连续 N 次鉴权失败自动吊销。

### Q3：crawler_task 表字段？
[Answer] TenantScopedModel：platform/credential_id(FK)/target_date/status(pending/assigned/exchanged/success/failed)/worker_token_id(可空)/cred_token(可空一次性)/cred_token_expires_at/assigned_at/import_batch_id(成功后回填)/error_reason/attempt。UNIQUE(tenant,platform,credential_id,target_date) 防重复派发。

### Q4：3 个 adapter 的目标表？
[Answer]
- qianniu adapter → `qianniu_daily` 表（UNIQUE(tenant,platform_product_id,date)；通过 PlatformProductService.find_by_platform_id 反查 style/sku；未匹配记 data_quality_issue warning 不阻塞）
- wanxiangtai adapter → `ad_daily` 表（广告投放数据）
- huitun adapter → 更新 `blogger.audience_profile`（U11 已加 JSONB 列）+ 灰豚画像；按 xiaohongshu_id 匹配 blogger，未匹配记 issue
3 adapter 实现 ImportAdapter 协议（parse_row/validate/upsert），注册到 ImportAdapterRegistry。

### Q5：Worker 上传结果如何触发导入？
[Answer] Worker result 上报（status=success + 文件已传 R2/multipart）→ CrawlerTaskService.report_result 调 ImportService.upload（用系统 actor / worker 上下文，source=qianniu/wanxiangtai/huitun）→ 返回 batch_id 回填 crawler_task.import_batch_id → run_import_batch 异步跑 3 adapter upsert。

### Q6：ImportService.upload 需要 User，Worker 无 User 怎么办？
[Answer] 重载支持系统 actor：新增 `ImportService.upload_system(content, source, tenant_id, actor_id=None, ...)` 或 upload 接受可选 user=None + tenant_id 显式参数。决策：加薄封装 `upload_for_crawler(content, source, tenant_id)`，内部复用 upload 主体（actor_id=None，audit actor_type=worker）。

### Q7：调度如何生成任务？
[Answer] Celery Beat `schedule_daily_tasks`（默认每天 02:00，crawler 队列）逐租户逐 active 凭据生成 crawler_task（target_date=昨天）；UNIQUE 防重复。失败采集回调 CredentialService.report_failure（连续失败暂停凭据）。

### Q8：data_quality_issue 表 + 看板？
[Answer] TenantScopedModel：source/severity(info/warning/error)/status(open/fixed/ignored)/entity_type/entity_ref/message/created_at。`GET /api/data-quality/summary` 按 source×severity 分组计数；`GET /api/data-quality/issues` 列表筛选；`PUT /api/data-quality/issues/{id}` resolve(fixed/ignored)。error 级别业务阻断（如禁止提交财务）由消费方检查，U13 只负责写入 + 看板。

### Q9：cred_token exchange 安全细节？
[Answer] cred_token = secrets.token_urlsafe(32) 随机；存 crawler_task.cred_token + expires_at；exchange 校验 token 匹配 + 未过期 + 未用过 → 调 CredentialService.decrypt_for_purpose(credential_id, purpose="crawler_{platform}") 返回明文 → 立即清空 cred_token（一次性）。日志/响应禁明文。

### Q10：Celery 队列 + Sentry tag？
[Answer] 新增 crawler 队列（shared-infrastructure 已预留）；Sentry tag crawler_platform=qianniu/wanxiangtai/huitun；actor_type=worker。

### Q11：worker_token 鉴权失败自动吊销阈值？
[Answer] WORKER_AUTH_FAILURE_THRESHOLD=5（代码常量）；连续 5 次鉴权失败（token 不存在/IP 不匹配/已吊销）→ is_active=false + 企微告警 admin。

### Q12：migration 编号？
[Answer] migration 017（接 016）：crawler_task + worker_token + data_quality_issue + qianniu_daily + ad_daily 5 表 + RLS + UNIQUE + idx + crawler.* / data_quality.* scope seed。

---

## 1. 步骤

- [x] 1.1 阅读 EP07-S11~S14 GWT + §2.2.1 Worker 安全边界 + CrawlerTaskService/DataQualityService 接口 + 现有 importer/credential/platform_product 契约
- [x] 1.2 编写 domain-entities.md（CrawlerTask/WorkerToken/DataQualityIssue/QianniuDaily/AdDaily ORM + 3 adapter 映射 + ER + cred_token 流转）
- [x] 1.3 编写 business-rules.md（BR-U13-01~ Worker 鉴权/IP/cred_token 一次性 TTL/调度生成/采集回调/3 adapter upsert/data quality 写入+看板/失败联动凭据/权限）
- [x] 1.4 编写 business-logic-model.md（UC + J3 端到端采集时序 + Worker poll/exchange/result 流 + 跨单元契约 U12/U10b/U06a/U14）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
