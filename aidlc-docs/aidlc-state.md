# AI-DLC 项目状态

## 项目信息
- **项目名称**: 服装电商运营管理系统 - 全栈交付（React + FastAPI）
- **语言**: 中文
- **项目类型**: Greenfield（全新项目）
- **当前阶段**: CONSTRUCTION
- **当前步骤**: U18 AI 决策建议全部 5 阶段完成 + Build & Test（794 passed / 80.75%）；**全部 23/23 sub-unit 交付完成（MVP 12 + V1 8 + V2 2 + P3 1）；项目 CONSTRUCTION 全部交付完成** 🎉🎉
- **交付方式**: 分阶段（MVP/P0 → V1/P1 → V2/P2 → P3）

## 工作区状态
- **现有代码**: 否
- **逆向工程需要**: 否
- **工作区根目录**: e:\work\Pycharm_Projection\eCommerce_v4\
- **技术栈**: React 18 + FastAPI + PostgreSQL 16 + Redis 7 + Celery + Cloudflare R2 + Zeabur

## 代码位置规则
- **应用代码**: 工作区根目录（绝不放在 aidlc-docs/ 中）
- **文档**: 仅在 aidlc-docs/ 中

## 执行计划摘要
- **MVP 工作单元**: U01-U08（含 U06a~U06e 适配器，共 12 sub-unit）
- **V1 工作单元**: U09-U15（含 U10a/U10b 拆分，共 8 sub-unit）
- **V2 工作单元**: U16-U17（2 个 sub-unit）
- **P3 工作单元**: U18（1 个 sub-unit）
- **合计**: 23 sub-unit

## 工作流进度

### INCEPTION 阶段
- [x] Workspace Detection
- [x] Reverse Engineering - SKIPPED（Greenfield）
- [x] Requirements Analysis
- [x] User Stories
- [x] Workflow Planning
- [x] Application Design
- [x] Units Generation

### CONSTRUCTION 阶段

#### U11 — 博主智能标签 + 灰豚展示（V1，进行中）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：tag_service.py 新建 + 阈值 tag_config 代码常量 + 实时/异步分离 + audience_profile JSONB ALTER 字段由 U13 写入 U11 读 + read_like_ratio 读时衍生不存 DB + quality_tags 依赖 U04 promotion 历史 + 触发时机 + 零依赖 + migration 015 ALTER + recompute admin 端点）
- [x] Functional Design — 3 文档完成（domain-entities：audience_profile JSONB 结构 + 阈值 5 常量 + 衍生 read_like_ratio + BloggerTagService 5 方法 I/O；business-rules：BR-U11-01~60 blogger_type 阈值/ratio 分母 0→null/假号判定/质量多标签/展示 null/批量重算 Celery+Beat/权限；business-logic-model：6 UC + J3 端到端 + 跨单元契约 U03/U04/U13/U01 + BloggerResponse 追加字段）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：零依赖 + 批量重算后台≤10min不影响在线 + Celery autoretry+单 blogger 失败不中止 + 读时 ratio O(1) + quality 聚合≤200ms + 客户端不可伪造 + recompute admin + 多租户逐 tenant + migration 015 ALTER 1 列 + Celery autodiscover+选装 Beat；tech-stack-decisions：tag_config 常量 + tag_service + blogger_quality 聚合 + Celery blogger_tasks + recompute API + schema 扩展 + migration 015 + 测试 3 文件）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U11-01 tag_config 常量+compute_blogger_type O(1)+recompute Celery 逐 tenant 容错(catch+log+继续)+autoretry 完整伪代码；P-U11-02 compute_read_like_ratio 分母 0/null 安全+is_fake 无数据→false 保守+quality_tags 聚合 avg_cpl/hit_rate 显式 tenant+LIMIT 截断+safe_div；logical-components：新建 4(tag_config/tag_service/blogger_quality/blogger_tasks)+修改 5(service 追加 type 调用/schemas+2 字段/api+recompute 端点/celery_app autodiscover/migration 015)+依赖图无循环+测试 3 文件）
- [x] Infrastructure Design — 2 文档完成（零新服务/表/桶/依赖/环境变量；唯一增量 migration 015 ALTER blogger ADD audience_profile JSONB NULL 不锁表无回填 + Celery Beat 选装 02:00 默认注释 + celery_app autodiscover 追加 tasks.blogger_tasks；部署回滚安全；本地 Docker 5554/6409；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成（tag_config 5 阈值 + services/metric/blogger_quality 聚合 avg_cpl/hit_rate/compute_quality_tags + tag_service BloggerTagService(compute_blogger_type/read_like_ratio/is_fake_account/recompute_for_tenant) + tasks/blogger_tasks recompute_all_blogger_tags 逐 tenant 容错 + blogger models +audience_profile JSONB + schemas +2 字段 + service create/update 自动分级 type+_to_response 衍生 ratio+替换 4 NotImplementedError 钩子 + api +POST /recompute-tags + celery_app autodiscover+Beat 注释 + migration 015 ALTER+scope seed + 3 测试文件）
- [x] Build and Test — Docker（PG16:5554 + Redis7:6409 + Py3.12）；alembic 001→015 全链路成功（含 015 blogger.audience_profile + blogger.tag:recompute scope）；U11 子集 29 passed；全量 666 passed/0 failed；覆盖率 80.23%（blogger_quality 100%）；修 1 测试问题（blogger_factory 未支持新列 audience_profile → conftest 工厂追加 kwarg，非生产 bug）；**U11 博主智能标签+灰豚展示全单元交付完成** 🎉

#### U12 — 平台凭据 + 采集失败告警（V1，进行中）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：新建 modules/credential 独立模块 + credential 表 TenantScopedModel(platform/username/password_ciphertext BYTEA/status/consecutive_failures/last_failure_reason/last_failure_at/privacy_consent_at/remark) + UNIQUE(tenant,platform,username) + 状态 active/paused + 硬删安全要求 + 隐私 privacy_consent bool 后端仅校验 + CredentialPublic 永不含密码 + 连续 3 次失败自动 paused+企微告警复用 U07 + 解密 AuditService.log 显式调用 + 权限 credential:read/write/delete seed admin+operations + resume 重置 failures=0）
- [x] Functional Design — 3 文档完成（domain-entities：Credential ORM 13 字段 + UNIQUE + CHECK + RLS + CredentialCreate/Update/Public schemas + CredentialPlatform Enum + 状态转换图 + 加密复用 crypto.py；business-rules：BR-U12-01~74 创建加密/不回显/解密审计/暂停恢复/硬删/失败告警/安全约束 + 错误码 6 种；business-logic-model：6 UC + J5 生命周期时序 + CredentialService 10 方法接口 + 7 API 端点 + 跨单元契约 U07/U13/U01）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：零依赖 + 加密 SLA <5ms + API SLA + 解密 ≤50ms + 威胁模型(跨租户 salt/篡改 tag/master key 分离)+ 不可回显 3 层(schema/日志/错误响应)+ 解密审计 append-only 复用 U01 + 失败阈值常量 3 + 通知 best-effort 容错 + 多租户隔离测试矩阵 + migration 016 + 2 指标 + 测试矩阵；tech-stack-decisions：零依赖复用 cryptography/NotificationService/AuditService + modules/credential 11 文件落点 + crypto.py 复用 + CONSECUTIVE_FAILURE_THRESHOLD=3 + migration 016 完整片段 + credential_decrypt_total/auto_paused_total + NotificationType +CREDENTIAL_FAILURE + 测试 3 文件）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U12-01 create+IntegrityError→409/加密/_to_public 不回显/update 密码脱敏审计/pause/resume 重置/硬删 完整伪代码 + P-U12-02 decrypt_for_purpose 审计 success/failed 双分支+指标+不静默/report_failure 自动暂停同事务+通知 best-effort commit 后/report_success 重置 完整伪代码；logical-components：modules/credential 11 文件 + 横切 3 改动(metrics/wecom enums/main) + migration 016 + 依赖图无循环 + 方法→故事/规则映射 + 3 测试文件）
- [x] Infrastructure Design — 2 文档完成（零新服务/依赖/桶/环境变量/Redis/Celery；唯一增量 migration 016 = credential 表(RLS+UNIQUE(tenant,platform,username)+idx+CHECK+FK tenant RESTRICT) + credential:read/write/delete 3 scope seed 绑 admin(全部)/operations(read) 幂等；密钥复用 CREDENTIAL_MASTER_KEY 存储分离；部署回滚无回填安全；本地 Docker 5555/6410；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成（modules/credential 11 文件：enums(CredentialPlatform/Status)+config(THRESHOLD=3)+exceptions(3 继承 core)+permissions(3 scope)+models(Credential ORM LargeBinary)+schemas(Create/Update/Public 无密码/Page)+repository+service(10 方法 create 加密+IntegrityError→409/decrypt 审计+指标双分支/report_failure 自动暂停+通知 best-effort)+deps+api(7 端点)；横切 core/metrics +2 counter + wecom enums +CREDENTIAL_FAILURE + main 注册 credential_router + migration 016 + 3 测试文件）
- [x] Build and Test — Docker（PG16:5555 + Redis7:6410 + Py3.12）；alembic 001→016 全链路成功（含 016 credential 表 + 3 scope seed）；U12 子集 18 passed；全量 684 passed/0 failed；覆盖率 80.32%（blogger_quality/metric 100%）；**首次运行全通过无生产 bug**；**U12 平台凭据+采集失败告警全单元交付完成** 🎉

#### U13 — 自动数据采集 Worker（V1，进行中）

- [x] Functional Design Plan 已生成（12 澄清 [Answer]：新建 modules/collect(crawler_task/worker_token/data_quality_issue/qianniu_daily/ad_daily 5 表+服务+Worker API)+3 adapter 放 importer/adapters；Worker pull 模型 + worker_token 鉴权独立 JWT + IP allowlist + 一次性 cred_token exchange 5min TTL + poll/exchange/result 审计 + 连续 5 次鉴权失败自动吊销；3 adapter qianniu→qianniu_daily/wanxiangtai→ad_daily/huitun→blogger.audience_profile + find_by_platform_id 反查未匹配记 issue；ImportService.upload_for_crawler 系统 actor；Beat schedule_daily_tasks 02:00 crawler 队列；data_quality 三级严重度看板；migration 017 5 表）
- [x] Functional Design — 3 文档完成（domain-entities：WorkerToken/CrawlerTask(状态机 pending→assigned→exchanged→success/failed)/DataQualityIssue/QianniuDaily/AdDaily 5 ORM + 3 adapter 映射 + cred_token 一次性流转 + ER；business-rules：BR-U13-01~53 Worker 鉴权/IP/cred_token 一次性 TTL/自动吊销/调度幂等/poll-exchange-result/3 adapter 反查未匹配 issue/data quality 看板/失败联动凭据/权限 + 错误码 6 种；business-logic-model：7 UC + J3 端到端采集时序 + Worker poll/exchange/result 流 + 3 service 接口 + 跨单元契约 U12/U10b/U06a/U11/U07/U14）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：零依赖 + poll≤100ms/exchange≤50ms/result≤300ms SLA + Worker 安全威胁模型(伪造/泄露/cred_token 重放/越权/审计)+ 明文密码处理(日志/响应/内存)+ FOR UPDATE SKIP LOCKED 并发防重复 + 幂等 + 4 指标 + 多租户隔离 + migration 017 5 表 + crawler 队列 + Beat 02:00 + Worker 安全测试矩阵 6 场景；tech-stack-decisions：零依赖 secrets/hashlib + modules/collect 14 文件落点 + 3 adapter importer/adapters + upload_for_crawler 系统 actor 封装 + WorkerTokenDep 鉴权 + cred_token 生成/hash + 4 metrics + crawler 队列/Beat + migration 017 + 测试 4 文件）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U13-01 worker_token authenticate+IP allowlist+失败计数自动吊销+issue 明文一次性 / P-U13-02 schedule 逐租户容错+UNIQUE 幂等+poll FOR UPDATE SKIP LOCKED 原子领取+exchange 一次性 cred_token+5min TTL / P-U13-03 report_result→upload_for_crawler+import_batch_id 回填+report_success/failure 联动+QianniuAdapter find_by_platform_id 反查未匹配 record warning+UNIQUE upsert 幂等+HuitunAdapter 更新 audience_profile+DataQualityService.summary source×severity 完整伪代码；logical-components：modules/collect 14 文件+3 adapter+tasks/crawler_tasks+横切 6 改动(importer service upload_for_crawler/注册 adapter/celery_app/metrics/main/migration 017)+依赖图无循环+migration 017 5 表 DDL+4 测试文件）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：后端无新服务 复用 celery-worker(+crawler 队列)/celery-beat(schedule_daily_tasks 02:00) + 外部 RPA Worker 旁路自建 VM pull 解耦不在 Zeabur；migration 017 5 表 DDL+RLS+UNIQUE+4 scope seed；Worker 网络安全 worker_token+IP allowlist+HTTPS 不无鉴权暴露；复用 private 桶 imports/；零新依赖/环境变量；本地 Docker 5556/6411；deployment-architecture：拓扑+部署 checklist+celery-worker -Q 更新+13 验证步骤+外部 Worker 启动模板要点 rpa-worker/README+监控 4 指标+回滚；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 全部 4 批完成（modules/collect 14 文件 + 3 adapter(qianniu/wanxiangtai/huitun) + tasks/crawler_tasks + 横切 6 改动 + migration 017 + 4 测试文件）
  - [x] Batch 1 — 模块基础+模型+Schema（enums/config/exceptions/permissions + models 5 表(WorkerToken/CrawlerTask/DataQualityIssue/QianniuDaily/AdDaily) + schemas）— 诊断器无警告
  - [x] Batch 2 — Repository + Service + Deps（WorkerTokenService issue/revoke/authenticate 失败计数自动吊销 + DataQualityService record/summary/list/resolve + CrawlerTaskService schedule_for_tenant/poll SKIP LOCKED/exchange 一次性/report_result + WorkerTokenDep 鉴权依赖）— 诊断器无警告
  - [x] Batch 3 — API + Adapter + 横切（crawler_api poll/exchange/result + worker_token_api + data_quality_api + 3 adapter find_by_platform_id 反查+record issue+UNIQUE upsert/huitun 更新 audience_profile + ImportService.upload_for_crawler 系统 actor + tasks/crawler_tasks + core/metrics 4 指标 + celery_app crawler 队列/autodiscover/Beat 02:00 + main 注册 3 router+3 adapter）— 诊断器无警告
  - [x] Batch 4 — migration 017（5 表+RLS+UNIQUE+idx+4 scope seed admin/operations）+ conftest 追加 collect/credential models import + tests/unit/test_crawler_adapters + tests/integration/test_crawler_flow + tests/api/test_crawler_api — 诊断器无警告
- [x] Build and Test — Docker（PG16:5556 + Redis7:6411 + Py3.12；本轮 Docker Desktop 重启后就绪）；alembic 001→017 全链路成功（含 017 5 表 + 4 scope seed）；U13 子集 18 passed；全量 702 passed/0 failed；覆盖率 80.24%；修 2 真实 bug（crawler poll 路由 `CrawlerTaskAssignment|Response` 返回类型致 FastAPI response_field 构建失败→app.main import 失败级联所有 api 测试 → +response_model=None / 单元测试 validate 传了合法日期断言失败 → 改空字典）；**U13 自动数据采集 Worker 全单元交付完成** 🎉

#### U14 — 工作进度/爆款约篇/店铺数据/投产报表（V1，进行中）

- [x] Functional Design Plan 已生成（11 澄清 [Answer]：复用 modules/report 追加 4 service + services/metric 3 子模块；2 新表 target_planning(UNIQUE tenant,pr,style,month)+store_daily(UNIQUE tenant,date 手动 3 字段)；工作进度纯读聚合 promotion 按 cooperation_date 月+pr KPI 复用 URGE_STATUS_SQL_EXPR+like_sum_expr；爆款约篇 set_target+list_with_actuals 达标/缺口；店铺看板 qianniu_daily SUM+store_daily 左联+手动 upsert；投产 5 公式 safe_div+周环比等长上期+exclude_brushing 占位默认 False；qianniu_daily 缺列从 extra JSONB 取；爆文统计阈值 500≠标记 1000；权限 report.*:read 通配+report.target/store_daily:write seed；precompute 任务占位 migration 018）
- [x] Functional Design — 3 文档完成（domain-entities：TargetPlanning/StoreDaily 2 表 + 4 报表读模型(PrWorkProgress/TargetWithActual/StoreDailyRow/ProductionRow) + 聚合来源时间维度表 + HIT_STAT_THRESHOLD 500；business-rules：BR-U14-01~52 工作进度 KPI 口径/爆款约篇达标/店铺聚合+手动/投产 5 公式 safe_div+除零 null+周环比+exclude_brushing 占位/纯读+显式 tenant/错误码；business-logic-model：4 UC + 跨表聚合 SQL 模式(投产 style 维度 LEFT JOIN qianniu/ad/promotion + service safe_div 分离) + 周环比等长上期 + 4 service 接口 + 6 API 端点 + 跨单元契约 U04/U05/U13/U08/U16/U17）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：零依赖 + 4 报表 SLA(工作进度/店铺≤500ms/投产≤800ms 跨表+周环比) + 跨表聚合优化(子查询预聚合避免笛卡尔积+idx 复用) + 除零 safe_div + 1 指标 report_query_duration + 多租户隔离测试矩阵 + migration 018 2 表 + precompute 占位 + 测试矩阵；tech-stack-decisions：零依赖复用 text() SQL/safe_div/resolve_time_range/like_sum_expr/URGE_STATUS_SQL_EXPR + modules/report 追加 4 service+advanced_repository/schemas/api + services/metric 3 子模块(work_progress HIT_STAT_THRESHOLD=500/store_daily/style_roi exclude_brushing 占位) + report_query_duration histogram + migration 018 片段 + 测试 3 文件）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U14-01 工作进度 GROUP BY pr+FILTER(URGE/已发布/爆文≥500)+like_sum_expr+service safe_div 比率 / 爆款约篇 set_target ON CONFLICT+actual 子查询+达标 gap；P-U14-02 店铺 qianniu SUM+store_daily 左联+手动 upsert ON CONFLICT；P-U14-03 投产 style 主查询+ad/promo 子查询预聚合防笛卡尔积+extra JSONB COALESCE+周环比等长上期两次聚合+service safe_div 5 公式+exclude_brushing 占位 完整伪代码；logical-components：modules/report 追加 9 文件+services/metric 3 子模块+横切 4 改动(metrics/main/celery_app/tasks)+migration 018 2 表 DDL+依赖图无循环+3 测试文件）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新服务；migration 018 target_planning(UNIQUE tenant,pr,style,month+FK user RESTRICT/style CASCADE+CHECK min>=0)+store_daily(UNIQUE tenant,date) 2 表+RLS+6 scope seed(report.target:write→pr_manager/store_daily:write→operations)；report 队列+precompute_report_cache 占位 V1 不强制；复用索引；零新依赖/环境变量/R2/Redis；本地 Docker 5557/6412；deployment-architecture：拓扑无变更+部署 checklist+11 验证步骤+report_query_duration 监控+回滚；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 全部 3 批完成（modules/report 追加 10 文件 + services/metric 3 子模块 + tasks/report_tasks + 横切 4 改动 + migration 018 + 3 测试文件）
  - [x] Batch 1 — 模型+Schema+Permissions+Metric 子模块（work_progress_models 2 ORM(TargetPlanning/StoreDaily) + advanced_schemas 7 schema + advanced_permissions 6 scope + services/metric/work_progress HIT_STAT_THRESHOLD=500/store_daily 占位/style_roi 5 公式 safe_div+exclude_brushing 占位）— 诊断器无警告
  - [x] Batch 2 — Repository + Service + Deps（advanced_repository 4 报表聚合 SQL(WorkProgress GROUP BY pr+FILTER/TargetPlanning actual 子查询/StoreDaily qianniu SUM+store_daily 左联/Production style 维度子查询预聚合防笛卡尔积+extra JSONB)+style_exists + 4 service(work_progress/target_planning ON CONFLICT+审计/store_daily upsert+审计/production 周环比等长上期) + deps 追加 4 service deps）— 诊断器无警告
  - [x] Batch 3 — API + 横切 + migration + 测试（advanced_api 6 端点(POST /targets 与 PUT /store-daily 返回 dict 不声明 response_model)+core/metrics report_query_duration_seconds histogram+celery_app report 队列+autodiscover report_tasks+tasks/report_tasks precompute 占位+main 注册 report_advanced_router + migration 018 + conftest 追加 report.work_progress_models import + tests/unit/test_style_roi + tests/integration/test_advanced_reports + tests/api/test_advanced_report_api）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5557 + Redis7:6412 + Py3.12）；alembic 001→018 全链路成功（含 018 target_planning/store_daily 2 表 + 6 scope seed）；U14 子集 24 passed；全量 726 passed/0 failed；覆盖率 80.70%（work_progress/target_planning/style_roi/work_progress_models 100%）；修 1 测试数据问题（投产周环比上期日期算错——跨度 0 天上期=前一天非 7 天前，非生产 bug）；**U14 报表进阶全单元交付完成** 🎉

#### U15 — 企微进阶（发文通知控评 + 异常预警推送）（V1，进行中）

- [x] Functional Design Plan 已生成（12 澄清 [Answer]：复用 modules/wecom 追加 2 表(wecom_alert_config 控评 webhook+3 阈值+接收人+开关 UNIQUE tenant / wecom_alert_log 去重留痕 UNIQUE tenant,type,entity,period_key)+客户端 2 方法(send_group_robot 直连 webhook 无 token/send_app_message 自建应用)+3 service+1 listener+1 Beat 任务；S09 复用 U04 PromotionPublished 通知类事件→listener 仅 enqueue notify_control_group→任务重读校验 publish_status 防回滚误发+webhook 缺失 warning 不阻塞+best-effort；S10 check_anomaly_and_alert 每小时逐租户→复用 U14 ProductionService.get_report last_7d→退货率>0.40/net_roi<阈值判定→period_key 当日去重→自建应用推送 alert_recipients+阈值实时读即时生效；权限 wecom.alert_config:read/write；指标 2 个；NFR06 落点）
- [x] Functional Design — 3 文档完成（domain-entities：WecomAlertConfig/WecomAlertLog 2 表字段规范+AlertType 枚举(return_rate_high/roi_low/conversion_low 占位)+WecomClient 2 新方法 I/O+组件清单 6 新建+10 横切修改+ER+演化(webhook 明文 V1→U16 加密/conversion 占位)；business-rules：BR-U15-01~82 S09 触发/事务安全仅入队/防回滚重读校验/webhook 缺失容错/best-effort+S10 投产数据源/3 类阈值/阈值即时生效无缓存/period_key 去重/接收人空 no_recipient 不落 log/推送失败不落 log 可重试+配置 upsert/阈值校验/webhook 脱敏回显+逐租户容错+权限+NFR06 监控+错误码矩阵；business-logic-model：UC-1 发布→事件→任务→群机器人 best-effort/UC-2 Beat→逐租户→投产聚合→阈值判定→去重→自建应用推送/UC-3 配置即时生效+跨单元契约 U04 事件/U07 客户端/U14 投产/U01 core+故事覆盖 EP08-S09/S10+NFR06）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：S09/S10 异步无在线 SLA+监控≤5s/租户+webhook 凭据脱敏+不入日志+威胁模型 4 项+去重 UNIQUE 并发 IntegrityError catch+防回滚重读+逐租户容错+多租户 RLS+2 指标 wecom_group_notify_total/wecom_anomaly_alert_total+NFR06 Sentry+migration 019+Beat hourly+测试矩阵 3 文件；tech-stack-decisions：零新依赖复用 WecomClient/events/ProductionService/crypto+modules/wecom 6 新建+10 横切落点+send_group_robot/send_app_message 实现要点+2 指标定义+migration 019 片段+Beat check-anomaly-hourly+测试 3 文件+本地 Docker 5558/6413）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U15-01 S09 listener 仅入队+任务重读校验防回滚+GroupNotifyService best-effort 4 分支/P-U15-02 check_anomaly 逐租户 bypass 元数据+set_config+catch Sentry 不中止/P-U15-03 check_and_alert _evaluate_row 阈值判定+_fire 去重+no_recipient+IntegrityError deduped+send_app_message+markdown 建议文案/P-U15-04 AlertConfigService upsert ON CONFLICT+webhook 脱敏末6位+阈值校验/P-U15-05 send_group_robot 直连+send_app_message 完整伪代码；logical-components：6 新建+10 横切+repository 追加 2 repo+依赖图无循环(U15→U07→U01;U15→U14→U13/U05)+migration 019 2 表 DDL 概要+启动序列 register_event_listeners+3 测试文件组件映射）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新服务/进程/资源变更；migration 019 wecom_alert_config(UNIQUE tenant+RLS)+wecom_alert_log(UNIQUE 去重+RLS+idx fired)+wecom.alert_config:read/write seed(operations 显式+admin 通配)；零新依赖/环境变量/R2/Redis 库；企微出站复用 U07；Beat check-anomaly-hourly minute=0 错峰；部署一致性 PromotionPublished required_handler=False 无逆向风险；本地 Docker 5558/6413；deployment-architecture：拓扑无变更+部署 checklist 8 步+验证 11 步+监控 2 指标+Sentry+回滚 3 层(代码/DB/is_enabled 停用)）；infrastructure-design.md spec-format 假阳性 IGNORE
- [x] Code Generation — 全部 2 批完成（modules/wecom 6 新建 + 横切 10 改动 + migration 019 + 3 测试文件）
  - [x] Batch 1 — 模型+Schema+枚举+权限+异常+客户端+repository+指标（alert_models 2 ORM + alert_schemas 脱敏 + enums AlertType + permissions 2 scope + exceptions AlertConfigInvalidError + client send_group_robot/send_app_message + repository 2 repo + metrics 2 Counter）— 诊断器无警告
  - [x] Batch 2 — Service+Deps+API+Listener+Tasks+横切+migration+测试（alert_config_service upsert+脱敏+校验+审计 / group_notify_service 重读校验+best-effort / anomaly_service _evaluate_row+_fire 去重+推送 / deps AlertConfigServiceDep / alert_api 2 端点 / listeners on_promotion_published / wecom_tasks notify_control_group+check_anomaly_and_alert / celery_app Beat check-anomaly-hourly / main 注册 listener+挂 router / migration 019 + conftest import + 3 测试）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5558 + Redis7:6413 + Py3.12）；alembic 001→019 全链路成功（含 019 wecom_alert_config/log 2 表 + 2 scope seed）；U15 子集 16 passed；全量 742 passed/0 failed；覆盖率 80.44%（anomaly/alert_config/alert_models 高覆盖）；修 1 bug（migration revision id 33 字符 > alembic_version VARCHAR(32) → 缩短为 019_u15_wecom_alert_tables）；**U15 企微进阶全单元交付完成；V1 全部 8/8 交付完成** 🎉

#### U16 — 拍单 / 刷单 / 余额（V2，进行中）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：复用 modules/finance 追加 order_adjustment_models(OrderAdjustment 拍单/刷单统一建模 + BalanceRecord 余额流水)+schemas/repository/service/api；migration 020 = 2 表 + promotion.in_store_order ALTER + finance.order/balance scope seed；S09 拍单自动生成订阅 U04 SettlementRequested 事件读 in_store_order best-effort 幂等 UNIQUE(tenant,promotion_id) partial；S10 create_brushing exclude_from_roi 默认 true + 金额"原价-返现"解析 + ROI 隔离接入 ProductionRepository.aggregate_by_style exclude_brushing 剔除刷单 + ProductionService 默认 true；S11 BalanceService.add_record 自动余额计算+expected_balance 校验+类型字段匹配；权限 finance.order/balance:read/write）
- [x] Functional Design — 3 文档完成（domain-entities：OrderAdjustment(13 字段+UNIQUE promotion_id partial+ROI idx)+BalanceRecord(balance_after 自动)+promotion.in_store_order+OrderType/BalanceRecordType 枚举+ROI 隔离口径+组件清单 6 新建+11 横切+ER；business-rules：BR-U16-01~71 拍单自动生成幂等 best-effort/刷单 exclude_from_roi/金额表达式解析/ROI 剔除 SUM/余额自动计算+expected 校验+类型字段匹配/权限/状态机/错误码矩阵；business-logic-model：UC-1 SettlementRequested 多 handler 自动拍单/UC-2 刷单录入+金额解析+投产剔除/UC-3 余额录入校验+跨单元契约 U04/U05/U14+故事覆盖 EP06-S09/S10/S11）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：写入 P95≤200ms/list≤300ms/自动拍单同事务增量<10ms/ROI 子查询投产≤800ms+金额表达式不 eval 正则+Decimal+余额并发 V2 不加锁说明+expected 兜底+威胁模型 4 项+多租户 RLS+1 指标 order_adjustment_auto_created_total+migration 020+exclude_brushing 默认 true 兼容+测试矩阵 3 文件；tech-stack-decisions：零新依赖复用 finance/events/ProductionService/Decimal/re+modules/finance 6 新建+11 横切落点+parse_amount_expr 正则实现+balance 计算+ROI 隔离 SQL 减项+指标+migration 020 片段(revision 020_u16_order_adjustment_balance)+测试 3 文件+本地 Docker 5559/6414）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U16-01 on_settlement_requested_auto_order 多 handler+best-effort try/except+auto_create_from_promotion 幂等 get_by_promotion+IntegrityError catch/P-U16-02 parse_amount_expr 正则不 eval+create_brushing exclude_from_roi=true+order_no 重复 warning/P-U16-03 add_record 类型字段匹配+last_balance+balance_after 计算+expected 不一致 422/P-U16-04 aggregate_by_style exclude_brushing 子查询减刷单 SUM+style_roi 移除占位+production 默认 true 完整伪代码；logical-components：6 新建+11 横切+OrderAdjustmentRepository/BalanceRecordRepository+依赖图无循环(U16→U05→U04;U16→U14→U13/U05)+migration 020 DDL 概要+finance.listeners.register 多 handler+3 测试文件映射）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新服务/进程/Celery/Beat；migration 020 order_adjustment(UNIQUE promotion_id partial+RLS+idx+CHECK)+balance_record(RLS+idx)+promotion.in_store_order ALTER(DEFAULT false 无回填)+finance.order/balance:read/write seed；零新依赖/环境变量/R2/Redis；ROI 口径升级 exclude_brushing 默认 true 部署即生效无刷单数据剔除 0；部署一致性 SettlementRequested 多 handler U05 先部署无逆向风险；本地 Docker 5559/6414；deployment-architecture：拓扑无变更+部署 checklist 6 步+验证 11 步+ROI 口径变更通知+监控 order_adjustment_auto_created_total+回滚 3 层(代码/DB/口径 query 回退)）；infrastructure-design.md spec-format 假阳性 IGNORE
- [x] Code Generation — 全部 2 批完成（modules/finance 6 新建 + 横切 11 改动 + migration 020 + 3 测试文件）
  - [x] Batch 1 — 模型+Schema+枚举+异常+权限+promotion ALTER+指标+repository（order_adjustment_models 2 ORM + 4 schema + OrderType/OrderAdjustmentStatus/BalanceRecordType + 3 异常 + finance.order/balance 4 scope + promotion.in_store_order + order_adjustment_auto_created_total + 2 repo）— 诊断器无警告
  - [x] Batch 2 — Service+Listener+Deps+API+ROI 接入+main+migration+测试（parse_amount_expr 正则 + auto_create_from_promotion 幂等 + create_brushing + balance add_record 计算/校验 + on_settlement_requested_auto_order 多 handler best-effort + 2 ServiceDep + 4 端点 + aggregate_by_style exclude_brushing 减刷单 + production 默认 true + advanced_api query true + style_roi 移除占位 + main 挂 router + migration 020 + conftest import + 3 测试）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5559 + Redis7:6414 + Py3.12）；alembic 001→020 全链路成功（含 020 order_adjustment/balance_record 2 表 + promotion.in_store_order + 4 scope seed）；U16 子集 24 passed；全量 766 passed/0 failed；覆盖率 80.60%；**首次运行全通过无生产 bug**；**U16 拍单/刷单/余额全单元交付完成；V2 进度 1/2** 🎉


#### U17 — 套装 / BI 看板 / 报表导出（V2 收官单元，进行中）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：套装落 modules/product(bundle_models BundleProduct+BundleItem)+BI/导出落 modules/report(bi_service/export_service/user_preference)；migration 021 = bundle_product+bundle_item+user_preference 3 表+product.bundle/report.export scope seed；EP02-S08 BundleService split_quantities 销量按 item 数量拆分；EP09-S06 BiService.get_dashboard 复用 report service 聚合 cards+charts+布局存 user_preference；EP09-S08 ReportExportService openpyxl 流式 xlsx StreamingResponse+report.export:read 403；复用 openpyxl 无新依赖）
- [x] Functional Design — 3 文档完成（domain-entities：BundleProduct/BundleItem/UserPreference 3 表+销量拆分口径+BI 数据集结构(cards+line/bar/pie)+导出 report_type 映射+组件 11 新建+7 横切+ER；business-rules：BR-U17-01~63 bundle 唯一/item sku 校验/quantity≥1/split_quantities/BI 聚合复用+布局 upsert/导出 report_type+openpyxl 流式+report.export 403/权限/错误码；business-logic-model：UC-1 套装创建+销量拆分/UC-2 BI 聚合+布局/UC-3 报表导出 Excel+跨单元契约 U02/U14+故事覆盖 EP02-S08/EP09-S06/EP09-S08）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：bundle≤200ms/BI get_dashboard≤1s 串行 3 service/导出 work-progress·store-daily≤2s·production≤3s+openpyxl write_only 流式 BytesIO 内存安全+时间跨度≤366 天+威胁模型 5 项(跨租户/sku 跨租户/他人偏好/越权导出)+report.export:read 独立 scope+并发 UNIQUE+1 指标 report_export_total+migration 021+测试矩阵 3 文件；tech-stack-decisions：零新依赖复用 openpyxl/report service/product+11 新建+7 横切落点+openpyxl write_only 导出片段+split_quantities+BI 数据集组装+report_export_total+migration 021 片段(revision 021_u17_bundle_bi_export)+测试 3 文件+本地 Docker 5560/6415）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U17-01 BundleService.create UNIQUE→409+sku 跨租户校验+split_quantities 纯函数/P-U17-02 BiService.get_dashboard 复用 Production/StoreDaily 聚合 cards+charts(line/bar/pie)+UserPreferenceService get_or_default/upsert ON CONFLICT+_DEFAULT_BI_LAYOUT/P-U17-03 ReportExportService.export openpyxl write_only 流式 BytesIO+_fetch_rows 3 report_type+_cell 序列化+report_export_total+403 在 api 层 完整伪代码；logical-components：product 5 新建+report 6 新建+横切 7+BundleRepository+依赖图无循环(U17→U02/U14→U13/U05→U01)+migration 021 DDL 概要+3 测试文件映射）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新服务/进程/Celery/Beat；migration 021 bundle_product(UNIQUE bundle_code)+bundle_item(FK sku+quantity≥1+UNIQUE bundle×sku)+user_preference(UNIQUE user×key) 3 表+RLS+product.bundle/report.export scope seed；零新依赖(复用 openpyxl)/环境变量/R2/Redis；导出 StreamingResponse 流式部署面；部署一致性 U02/U14 已部署+migration 021 紧接 020；本地 Docker 5560/6415；deployment-architecture：拓扑无变更+部署 checklist 6 步+验证 11 步+监控 report_export_total+回滚）；infrastructure-design.md spec-format 假阳性 IGNORE
- [x] Code Generation — 全部 2 批完成（product 5 新建 + report 6 新建 + 横切 7 + migration 021 + 3 测试文件）
  - [x] Batch 1 — 模型+Schema+权限+异常+指标+repository（bundle_models 2 ORM + user_preference_models + bundle_schemas 4 + product.bundle/report.export scope + ReportExportTypeInvalidError + report_export_total + BundleRepository）— 诊断器无警告
  - [x] Batch 2 — Service+API+Deps+main+migration+测试（BundleService create/get_with_items/split_quantities + UserPreferenceService + BiService get_dashboard + ReportExportService openpyxl 流式 + bundle_api/bi_api/export_api + product/report deps + main 挂 3 router + migration 021 + conftest import + 3 测试）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5560 + Redis7:6415 + Py3.12）；alembic 001→021 全链路成功（含 021 bundle_product/bundle_item/user_preference 3 表 + 3 scope seed）；U17 子集 15 passed；全量 781 passed/0 failed；覆盖率 80.67%；**首次运行全通过无生产 bug**；**U17 套装/BI/导出全单元交付完成；V2 全部 2/2 交付完成** 🎉


#### U18 — AI 决策建议（P3 项目收官单元，进行中）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：新建 modules/ai(DeepSeekClient httpx 封装+降级 / AiAdvisoryService 数据准备+prompt+留痕+降级 / AiAdviceLog)；migration 022 = ai_advice_log 1 表 + ai.advice scope seed；EP11-S01 strategy_advice 数据充足≥6 月+ProductionService/WorkProgressService 摘要；EP11-S02 anomaly_diagnosis 读 wecom_alert_log(U15)+款式投产；EP11-S03 blogger_suggest 候选筛选+Top N；优雅降级 AI 不可用 503 不阻塞页面；prompt 脱敏+API key 仅环境变量；同步 API 为主 Celery 占位）
- [x] Functional Design — 3 文档完成（domain-entities：AiAdviceLog 1 表+AdviceType/AdviceStatus 枚举+DeepSeekClient I/O+3 advice 数据准备口径+组件 11 新建+5 横切+ER；business-rules：BR-U18-01~96 策略数据充足/异常归因 404/博主匹配候选/优雅降级 503 不阻塞+短超时/留痕 success·degraded·failed/prompt 脱敏+key 不回显/权限/可观测/错误码矩阵；business-logic-model：UC-1 策略建议/UC-2 异常归因/UC-3 博主选择+优雅降级流+跨单元契约 U14/U15/U03/U02+故事覆盖 EP11-S01/S02/S03）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：AI 受外部主导无 SLA+超时 30s 降级+数据准备≤1s；优雅降级全捕获(timeout/HTTPError/非200/JSON)→503 不阻塞+未配置 key 视为不可用+留痕+独立性；安全 key 仅环境变量+prompt 脱敏+威胁模型 5 项；成本 数据不足不调 AI+候选限数+无缓存；2 指标 ai_advice_total/ai_advice_latency_seconds+Sentry 非降级类；migration 022+测试矩阵 mock；tech-stack-decisions：零新依赖复用 httpx/report/blogger+modules/ai 11 新建+5 横切落点+DeepSeekClient 降级片段+DEEPSEEK_* 配置+2 指标+migration 022 片段(revision 022_u18_ai_advice_log)+测试 3 文件 monkeypatch+本地 Docker 5561/6416）；nfr-requirements.md spec-format 假阳性 IGNORE
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U18-01 DeepSeekClient 未配置即降级+httpx catch(timeout/HTTPError/非200/JSON)→503+latency/P-U18-02 AiAdvisoryService._run 统一 chat+留痕 success·degraded+指标+strategy 数据充足/anomaly 404/blogger 候选筛选+_parse_advice confidence 启发式/P-U18-03 API 3 端点 require_permission+AppException 全局映射 503/422/404 完整伪代码；logical-components：11 新建+5 横切+AiAdviceLogRepository+依赖图无循环(U18→U14/U15/U03/U02→U13/U05→U01)+migration 022 DDL 概要+DeepSeek 未配置不影响启动+3 测试文件映射 monkeypatch）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新服务/进程/Celery/Beat；migration 022 ai_advice_log 1 表+RLS+idx+ai.advice:read scope seed(pr/pr_manager/operations+admin)；零新依赖(复用 httpx)+DEEPSEEK_* 4 环境变量(API_KEY Zeabur Secrets)；DeepSeek HTTPS 出站部署面+未配置全 503 不出站；部署一致性 U14/U15/U03/U02 已部署+migration 022 紧接 021+AI 独立不影响启动；本地 Docker 5561/6416；deployment-architecture：拓扑无变更+部署 checklist 7 步+验证 11 步+监控 ai_advice_total/latency+回滚+密钥 Secrets 轮换+留空降级）；infrastructure-design.md spec-format 假阳性 IGNORE
- [x] Code Generation — 全部 2 批完成（modules/ai 11 新建 + 横切 5 + migration 022 + 3 测试文件）
  - [x] Batch 1 — 模型+Schema+枚举+异常+权限+配置+指标+client+repository（enums AdviceType/AdviceStatus + exceptions 503/422 + AiAdviceLog + 5 schema + ai.advice scope + DEEPSEEK_* config + ai_advice_total/latency + DeepSeekClient 降级 + AiAdviceLogRepository/AiDataRepository）— 诊断器无警告
  - [x] Batch 2 — Service+Deps+API+main+migration+测试（AiAdvisoryService _run 统一编排+strategy(数据充足)/anomaly(404)/blogger(候选+Top N)+留痕+降级 + AiAdvisoryServiceDep + 3 端点 + main 挂 ai_router + migration 022 + conftest import + 3 测试）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5561 + Redis7:6416 + Py3.12）；alembic 001→022 全链路成功（含 022 ai_advice_log 表 + ai.advice scope seed）；U18 子集 13 passed；全量 794 passed/0 failed；覆盖率 80.75%；**首次运行全通过无生产 bug**（DeepSeek 全程 monkeypatch）；**U18 AI 决策建议全单元交付完成；全部 23/23 sub-unit 交付完成** 🎉🎉


#### U10b — 平台商品映射（V1，已完成）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：追加 modules/product；platform_product 表(platform/platform_id/style_id 必填/sku_id 可空/title/is_active)；UNIQUE(tenant,platform,platform_id) 重复 409；platform VARCHAR 不硬编码 Enum；create_or_update 幂等供 U13/U14；find_by_platform_id 反查；引用校验；硬删；product.platform:read/write scope；migration 014）
- [x] Functional Design — 3 文档完成（domain-entities：PlatformProduct 实体 + UNIQUE + 2 FK + idx + ER；business-rules：BR-U10b-01~51 唯一性/引用校验/upsert/反查/删除/权限/错误码；business-logic-model：5 UC + 导入关联契约 U13/U14 未匹配不阻塞 + 跨单元）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：UNIQUE 并发 IntegrityError→409 防 TOCTOU + 反查命中 UNIQUE 索引 ≤100ms + 引用校验防跨租户挂接 + RLS + migration 014 + 测试矩阵；tech-stack-decisions：零依赖 + modules/product 追加落点 + PlatformProductService(create/create_or_update 幂等/find_by_platform_id) + IntegrityError catch + migration 014 1 表+scope seed + 测试落点）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U10b-01 create+IntegrityError→409/create_or_update SELECT→insert|update 幂等/find_by_platform_id 命中 UNIQUE 索引/引用校验 RLS 防跨租户 完整伪代码；logical-components：新建 4 文件(platform_product_models/schemas/service/api) + permissions.py 追加 + migration 014 + main 注册 + 2 测试文件 + 依赖图无循环）
- [x] Infrastructure Design — 2 文档完成（零新服务/依赖/桶/环境变量/Celery；唯一增量 migration 014 = platform_product 表(RLS+UNIQUE(tenant,platform,platform_id)+FK style RESTRICT/sku SET NULL+idx) + product.platform:read/write scope seed 绑角色幂等；部署回滚无回填；本地 Docker 5553/6408；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成（platform_product_models/schemas/service/api + permissions.py 追加 + migration 014 + main 注册 + 6 集成 + 3 API 测试）；修 1 bug（lookup 端点 SessionDep Annotated+Depends 冲突 → 移 session 为位置参数）
- [x] Build and Test — Docker（PG16:5553 + Redis7:6408 + Py3.12）；alembic 001→014 全链路成功（含 014 platform_product 表 + scope seed）；U10b 子集 9 passed；全量 637 passed/0 failed；覆盖率 80.24%；**U10b 平台商品映射全单元交付完成** 🎉

#### U10a — 设计制版全流程（V1，已完成）

- [x] Functional Design Plan 已生成（10 澄清 [Answer]：新建 modules/design + design_status 7 态 Enum 扩展（DB 不变）+ 3 子表(fabric/pattern/craft)+design_workflow_log 历史表 + 复用 core/state_machine + 驳回回退映射 + 自动核价写 SKU + 复用 U07 NotificationService + design.* scope migration 013）
- [x] Functional Design — 3 文档完成（domain-entities：DesignStatus 7 态 + StyleFabric/StylePattern/StyleCraft/DesignWorkflowLog + NotificationType 扩展 + ER；business-rules：BR-U10a-01~72 状态机转移表/驳回回退/自动核价/吊牌价/原地动作/取消不可逆/通知/权限矩阵/错误码；business-logic-model：13 UC + J1 端到端时序 + 跨单元契约 + available_actions 矩阵）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：状态推进单事务 + 乐观并发 UPDATE WHERE from RETURNING + 通知同事务 + 自动核价同事务写 SKU + R2 public/private + 4 表 RLS + 性能 ≤300ms + driven_by 服务端推断防伪 + 测试矩阵；tech-stack-decisions：零依赖 + DesignStateMachine 复用 core + REJECT_PREVIOUS 映射 + 核价求和 + 文件落点 + RoleRepository.list_user_ids_by_role_code + NotificationType DESIGN_* + migration 013 4 表+scope seed + 组件/测试落点）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U10a-01 DESIGN_TRANSITIONS 转移表 + DesignStateMachine 校验 + update_design_status 乐观并发 RETURNING + 副作用同事务编排 + 原地动作 + reject/cancel 动态目标(REJECT_PREVIOUS/DRIVEN_BY) 完整伪代码；P-U10a-02 自动核价求和 + bulk_update_sku_cost_price 系统口径绕过 U09 + audit 脱敏；P-U10a-03 driven_by/NOTIFY_ROLE 服务端推断防伪 + list_user_ids_by_role_code + notify 同事务无人跳过；logical-components：modules/design 13 文件 + 横切 3 改动(wecom enums/auth repo/main) + migration 013 4 表 + 依赖图无循环 + 5 测试文件）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：零新服务/依赖/桶/环境变量/Celery；唯一增量 migration 013 = 4 表(style_fabric/pattern/craft UNIQUE(style_id) 1:1 + design_workflow_log idx)+RLS+FK CASCADE + design.* scope seed 绑角色幂等；设计稿 R2 public/版型 private 复用；部署回滚无回填；infrastructure-design.md spec-format 假阳性 IGNORE；deployment-architecture：拓扑无变更 + checklist + 验证(4 表/scope≥7/状态机端到端/核价/通知/reject/cancel/非法 422)+ 本地 Docker 5552/6407）
- [ ] Code Generation
  - [x] Plan 已生成（4 批 + Build & Test；§0 修订：Sku 无 tag_price → migration 013 ALTER ADD + Sku 模型加字段）
  - [x] Batch 1 — 模块基础 + 模型 + Schema（design/{__init__,enums(DesignStatus 7 态+REJECT_PREVIOUS/DRIVEN_BY/NOTIFY_ROLE/TERMINAL),permissions(8 scope),exceptions}+models(StyleFabric/StylePattern/StyleCraft/DesignWorkflowLog 4 表)+schemas（提交+响应）；product/models.py Sku +tag_price+ck）— 诊断器无警告
  - [x] Batch 2 — 状态机 + domain + repository（state_machines：DESIGN_TRANSITIONS 5 推进规则 + make_design_state_machine；domain：compute_total_cost + compute_available_actions(状态×角色矩阵+admin cancel) + can_reject；repository：get_style/style_code_exists/add_style + update_design_status 乐观并发 UPDATE RETURNING + upsert_fabric/pattern/craft + add_workflow_log/list_workflow_log + bulk_update_sku_cost_price/tag_price 系统口径 + list_grouped 显式 tenant + list_by_status）— 诊断器无警告
  - [x] Batch 3 — service + deps + api + 横切（service：DesignService 13 方法 _validate_rule 不 setattr 防 autoflush 抢先 + _advance 乐观并发 + _notify_role 角色解析 + create_design/submit_fabric/grading/craft/costing(自动核价绕过 U09)/confirm_price + 原地 submit_pattern/complete_fabric/set_tag_price + reject(回退+通知上游)/cancel(admin 校验+终态拒绝) + list_designs/get_detail；deps；api 13 端点 require_permission design.*；横切 wecom enums +DESIGN_ADVANCE/REJECT/DONE + auth repo +list_user_ids_by_role_code + main 注册 design_router）— 诊断器无警告
  - [x] Batch 4 — migration 013（4 表 op.create_table + RLS enable + UNIQUE(style_id)×3 + design_workflow_log idx + FK CASCADE + ALTER sku ADD tag_price+ck + design.* 8 scope seed 绑 5 角色，幂等）+ tests/unit/test_design_state_machine.py（转移表+available_actions+回退映射）+ test_design_costing.py（求和）+ tests/integration/test_design_workflow.py（J1 端到端→大货 + reject 回退 + reject 缺 reason + cancel 不可逆 + cancel 非 admin 403 + 非法转移 422）+ test_design_notification.py（通知写入 + 无人跳过 + 自动核价写 SKU）+ tests/api/test_design_api.py（401 + OpenAPI）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5552 + Redis7:6407 + Py3.12）；alembic 001→013 全链路成功（含 013 4 表 + sku.tag_price + design.* scope seed）；U10a 子集 35 passed；全量 628 passed/0 failed；覆盖率 80.47%；**首次运行全通过无生产 bug**（_validate_rule 不 setattr 防 autoflush 抢先验证有效）；**U10a 设计制版全流程全单元交付完成** 🎉

#### U09 — 字段级权限 + 自定义权限（V1，已完成）

- [x] Functional Design Plan 已生成（8 澄清问题预填 [Answer]；统一 4 个 legacy_field_permissions → core 注册表 + 字段 scope 叠加 override；EP01-S05 自定义权限底层 U01 已就绪缺 API；EP01-S06 字段级读/写 + 回归 4 模块）
- [x] Functional Design — 3 文档完成（domain-entities：FieldRule 注册表 + 字段 scope field.<entity>.<field>:read|write + 复用 user_permission_override + 有效字段权限算法 + 4 模块字段清单；business-rules：BR-U09-01~80 注册表/算法/读过滤/写拒绝/grant-revoke API/keyword 侧信道/动作权限/回归；business-logic-model：5 UC + 字段权限上下文 + 4 模块回归映射 + 与 U01 merge_permissions 契约）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：零新增依赖 + 内存 dict/set 字段过滤 O(字段数) 无额外 DB + effective-permissions 复用权限加载 + 复用 merge_permissions/list_scopes_for_user + 回归 4 legacy 兼容 + 安全移除字段防存在性泄露/keyword 侧信道/撤销优先 + migration 012 仅 seed 字段 scope + structlog grant/revoke + 测试矩阵；tech-stack-decisions：零依赖 + FieldRule/FIELD_PERMISSION_REGISTRY 结构 + can_read/write_field + field_filter 移除 + merge_permissions 复用 + migration 012 seed 片段 + 4 模块回归落点）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U09-01 FIELD_PERMISSION_REGISTRY/FieldRule/FieldPermissionContext/can_read_field/can_write_field/field_filter 移除非 null/ctx 构造复用 list_codes_for_user+list_scopes_for_user/撤销>授予>角色/admin 通配/blogger.wechat keyword 侧信道 + P-U09-02 PermissionService grant/revoke/get_effective 复用 merge_permissions+双缓存失效+audit+未知 scope 422 + 3 API 端点 200{"ok"}；logical-components：新建 core/security/field_permissions.py + PermissionService + migration 012；改 core/exceptions+FieldPermissionDenied + auth deps/schemas/repository/api + 4 模块 service 重构；删 4 legacy + 重复异常；无循环依赖 core 单向；migration 012 seed 18 字段 scope 不绑角色幂等；4 测试文件）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量：无新服务/表/依赖/环境变量/Celery/R2；唯一增量 migration 012 向 permission 表 INSERT 18 字段 scope（settlement.amount/total_amount 仅 read）ON CONFLICT DO NOTHING 幂等不绑角色 + downgrade DELETE；Redis 复用 cache 库新增 fieldctx:user:<id> 同 TTL；复用 U01 权限基础设施；部署 = 代码 + migration 012 同批无回填风险 + 回滚恢复 legacy 行为；infrastructure-design.md spec-format 假阳性 IGNORE）
- [ ] Code Generation
  - [x] Plan 已生成（4 批 + Build & Test；§0 关键修订：响应保持 None 投影回归兼容，决策统一经 core can_read/write_field + 字段级 override；proof_upload → finance.settlement:pay scope 已存在）
  - [x] Batch 1 — Core 基础（core/security/field_permissions.py 新建：FieldRule + FIELD_PERMISSION_REGISTRY 10 字段 + FieldPermissionContext + can_read_field/can_write_field/field_filter + build_field_perm_context；core/exceptions.py +FieldPermissionDenied(field+entity 兼容)；product/exceptions.py 改 re-export core）— 诊断器无警告
  - [x] Batch 2 — 4 模块 service 重构（product SkuService _check_price/_to_response per-field can_*_field("sku")；blogger _check_sensitive/_to_response/list_bloggers wechat 侧信道 → can_*_field("blogger")；promotion _check_amount/_to_response → can_*_field("promotion") 拆 quote_amount/cost_snapshot；finance add_extra_item/list/2×daily_summary/payment 写/proof_upload(EffectivePermissions finance.settlement:pay)/_to_response → can_*_field("settlement")）+ 4 service 加 self._perms=PermissionRepository + 删 4 legacy 模块 + 删 4 obsolete legacy 测试（test_field_permissions/promotion_field_perms/blogger_field_perms/settlement_field_perms）— 诊断器无警告，无残留 import
  - [x] Batch 3 — auth 自定义权限 API（repository.upsert_override UNIQUE 冲突切换 effect/reason + tenant 自动注入；schemas PermissionOverrideIn + EffectivePermissionsView；service PermissionService.grant/revoke/get_effective 复用 merge_permissions + invalidate cache + audit permission.grant/revoke + 未知 scope ValidationError 422 + 用户不存在 404；api 3 端点 grant/revoke(200 {"ok":True})+effective-permissions 鉴权 SCOPE_PERMISSION_GRANT；deps get_field_perm_context 未单独加—service 内联 build_field_perm_context）— 诊断器无警告
  - [x] Batch 4 — migration 012（18 字段 scope category='field' INSERT ON CONFLICT DO NOTHING + downgrade DELETE ANY；不绑角色；接 011）+ tests/unit/test_field_permissions.py（注册表值 + can_read/write_field 角色/grant/revoke/admin/不在注册表/superuser + field_filter 移除）+ tests/integration/test_custom_permission.py（grant→可见 / revoke→屏蔽 / get_effective 结构 / 未知 scope 422 / 用户不存在 404）+ tests/api/test_permission_api.py（3 端点 401 + OpenAPI）— 诊断器无警告
- [x] Build and Test — Docker（PG16:5551 + Redis7:6406 + Py3.12）；alembic 001→012 全链路成功（含 012 字段 scope seed）；U09 子集 31 passed；全量 593 passed/0 failed；覆盖率 80.09%；修 1 真实 bug（promotion _to_response 残留 can_see_amount → can_see_quote，cpl 字段；refactor 遗漏第 3 处）+ 集成测试加 stub_cache fixture（redis 跨事件循环）；**U09 字段级权限+自定义权限全单元交付完成** 🎉

#### U01 — 认证 + 多租户 + 备份框架（已完成）
- [x] Functional Design / NFR Req / NFR Design / Infra Design / Code Gen — 88 文件
- [x] Build and Test - MVP-end 集成验证通过（U01-U05 一并）

#### U02 — 商品 / SKU 基础（已完成）
- [x] 全部 5 阶段 — 32 新文件 + 4 修改 + 3 文档

#### U03 — 博主库基础（已完成）
- [x] 全部 5 阶段 — 25 新文件 + 3 修改 + 3 文档

#### U04 — 推广合作核心（已完成）
- [x] Functional Design — 3 文档（28 字段 + 3 状态机 + 2 事件）
- [x] NFR Requirements — 2 文档（10 决策 + 25 测试场景）
- [x] NFR Design — 2 文档（含 8 P1 反馈修正全部落地）
- [x] Infrastructure Design — 2 文档
- [x] Code Generation — 全部 4 批 13 Step 完成（约 42 新文件 + 6 修改 + 3 文档摘要）
  - [x] Batch 1（Step 1-3）— 11 新建 + 2 修改：模块基础 + core/events.py + 模型/Schema/状态机/事件
  - [x] Batch 2（Step 4-5）— 4 新建：urge_calculator + metrics_calculator + domain + repository
  - [x] Batch 3（Step 6-7）— 3 新建 + 1 修改：service + deps + api + main.py
  - [x] Batch 4（Step 8-12）— 23 新建 + 3 修改：migration + 13 测试 + 2 frontend + 2 ci/cd 修改 + 3 文档摘要

#### U05-U18（待执行）
- [ ] Functional Design / NFR Req / NFR Design / Infra Design / Code Gen
- [ ] Build and Test - EXECUTE 阶段末

#### U06a — 统一导入框架（已完成）
- [x] Functional Design Plan 已生成 + P1/P2 反馈修订（5 条 FB-A~E）
- [x] Functional Design — 3 文档完成（domain-entities + business-rules + business-logic-model）
- [x] NFR Requirements Plan 已生成（12 澄清问题，预填默认值；异步导入/解析/R2/Celery 重试增量 NFR）
- [x] NFR Requirements — 2 文档完成（nfr-requirements + tech-stack-decisions）
- [x] NFR Design Plan 已生成 + P1/P2 反馈修订（6 条 NF-1~6：连接池串租改 per-row SET LOCAL / TOCTOU+孤儿改 DB 先行+补偿 / 批次并发互斥原子 claim / Celery autodiscover import_tasks / 权限命名 importer.batch:* / multipart body 网关上限）
- [x] NFR Design — 2 文档完成（nfr-design-patterns 5 模式 P-U06a-01~05 + logical-components）
- [x] Infrastructure Design Plan 已生成（12 澄清问题；Celery 发现 + nginx body 上限 + R2 imports/ + migration 010 + permission seed）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design + deployment-architecture 含 migration 010 完整 DDL + permission seed）
- [x] Code Generation Plan 已生成（10 Step 分 3 批，约 35 新文件 + 6 修改；FB-A~E + NF-1~6 守护）
- [x] Code Generation Batch 1-3 — 全部完成
  - [x] Batch 1（Step 1-3）— 8 新建 + 3 修改：模块基础(enums/permissions/exceptions) + 横切(metrics +5/config +4/attachment get_object_bytes) + 模型/Schema/Adapter 协议/Registry
  - [x] Batch 2（Step 4-6）— 5 新建 + 2 修改：domain + repository（claim_for_retry NF-3）+ field_mapping_service + service（upload DB 先行 NF-2 + retry 两类分流 FB-E + csv_safe）+ tasks/import_tasks（per-row SET LOCAL NF-1 + 双 session + worker_process_init NF-4）+ deps/api（8 端点）+ celery_app/main 修改
  - [x] Batch 3（Step 7-10）— migration 010（3 表 + 4 UNIQUE + 3 RLS + permission seed）+ default_roles + 10 测试文件（61 用例）+ frontend types/api + ci.yml import 框架校验 + nginx 21m + openpyxl/freezegun + 3 文档
- [x] Build & Test（U06a 子集 + 全量回归）— Docker（PG16+Redis7+Py3.12）真实跑通；migration 010 全链路成功；importer 61 测试全绿；全量 494 passed/0 failed；覆盖率 77.89%；修复 3 个真实/测试问题（asyncpg SET LOCAL 参数化 → set_config / upload SAVEPOINT 补偿 / identity-map populate_existing）

#### U05 — 财务结款核心（进行中）
- [x] Functional Design Plan 已生成（18 澄清问题，预填默认值 + 8 P1 反馈修正落地）
- [x] Functional Design — 3 文档完成（domain-entities + business-rules + business-logic-model）
- [x] NFR Requirements Plan — 已生成（13 澄清问题，预填默认值；5 个新增指标；34 测试场景）
- [x] NFR Requirements — 2 文档完成（nfr-requirements + tech-stack-decisions）
- [x] NFR Design Plan — 已生成（10 澄清问题，4 个新增模式 P-U05-01~04）
- [x] NFR Design — 2 文档完成（nfr-design-patterns + logical-components）
- [x] Infrastructure Design Plan — 已生成（13 澄清问题，首次使用 R2 private 桶 + 008 backfill + e2e-smoke 启用）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design + deployment-architecture）
- [x] Code Generation Plan — 已生成（13 Step 分 4 批，约 47 新文件 + 5 修改）
- [x] Code Generation Batch 1-4 — 全部完成（Step 1-13）：约 44 新文件 + 5 修改，与 U04 同批部署激活 SettlementRequested 事件链路

##### Batch 1（Step 1-3）— 已完成 ✓
- [x] Step 1 — 模块基础（5 文件）：__init__ + enums + permissions + legacy_field_permissions + exceptions
- [x] Step 2 — 横切扩展（1 修改）：core/metrics.py 追加 5 个 settlement 指标
- [x] Step 3 — 模型 + Schema（4 文件）：models（22 字段无 is_active FB3）+ schemas + events（SettlementPaid required_handler=False FB5）+ state_machines（5 状态 6 转移）

##### Batch 2（Step 4-5）— 已完成 ✓
- [x] Step 4 — Domain 层 + shared attachment 基础设施补齐（4 修改/新建）：core/attachment.py 修改（追加 Attachment ORM + 3 方法）+ core/attachment_api.py 新建 + domain.py 新建（含 format_settlement_no）+ attachment_validator.py 新建（FB4 6 项强校验 + 跨租户 4 层防御）
- [x] Step 5 — Repository 层（1 文件）：repository.py（next_settlement_sequence FB2 + update_state FB7 + 双口径 daily_summary FB7 + 列表查询）+ SettlementListFilters dataclass

##### Batch 3（Step 6-7）— 已完成 ✓
- [x] Step 6 — Service + 双向 Listener（3 文件）：service.py（4 状态推进 + add_extra_item + daily_summary × 2 + 失败处理不对称 FB5）+ finance/listeners.py（强一致 + flush FB1/FB6）+ promotion/listeners.py（通知类反向 FB5）
- [x] Step 7 — API + main.py（3 文件）：deps.py + api.py（8 端点 + DELETE 405 FB3）+ main.py（注册 finance_router + attachment_router + register_event_listeners 双向扩展）

##### Batch 4（Step 8-13）— 已完成 ✓
- [x] Step 8 — 3 Alembic migration：007（两段：shared attachment 表 + settlement/extra_item/sequence + FK + 永久 UNIQUE + GIN trgm，FB3）+ 008（backfill PL/pgSQL FB8 + 不可逆 downgrade）+ 009（staging seed）
- [x] Step 9 — 5 单元测试 + conftest（settlement_factory + attachment_factory + cross_unit_event_bus）：state_machine / domain / field_perms / paid_event / attachment_validator（FB4）
- [x] Step 10 — 7 集成测试（按内聚合并）：create_via_event（FB1+FB3+FB6）/ lifecycle / mark_paid（FB4+FB5）/ concurrency（FB7）/ daily_summary（FB7+FB8）/ attachment_upload / e2e_review_to_paid（J4）
- [x] Step 11 — 1 API + 2 性能测试：test_settlement_api（鉴权 + OpenAPI + DELETE 405）+ list_perf + daily_summary_perf
- [x] Step 12 — frontend types.ts/api.ts + ci.yml（双向 listener 校验）+ deploy-staging.yml（真实 e2e-smoke 启用）+ 3 文档摘要
- [x] Step 13 — 完成校验：全部诊断器无警告 + Plan 全 [x] + EP06-S02~S08 覆盖 + 8 P1 守护测试 + 双向 listener 框架完整

### OPERATIONS 阶段
- [ ] Operations - PLACEHOLDER

#### U08 — 发文进度看板（已完成）

- [x] Functional Design Plan 已生成（9 澄清问题预填 [Answer]；MVP 最后单元 —— 纯读聚合层无新表/migration；仅 EP09-S01 三层看板 + EP09-S07 时间筛选；modules/report + services/metric/publish_progress + common.safe_div；TimeRange 5 preset 按 cooperation_date 聚合；9 汇总指标 + 折算 + 卡片 GROUP BY style + 详情双维度；分母 0→null）
- [x] Functional Design — 3 文档完成（domain-entities：5 读模型 TimeRange/ProgressSummary/StyleCard/PrDetail/TimeSeriesPoint + 聚合来源映射 + 无新实体；business-rules：BR-U08-01~61 时间解析/9 指标口径/折算/卡片/详情/分母 0/阈值着色/权限；business-logic-model：4 UC + TimeRange 解析 + SQL 聚合 + 跨单元契约）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：聚合 SLA P95≤500ms + 实时聚合复用索引 + 只读 RLS + null 安全 safe_div + TimeRange≤366 天 + 多租户隔离测试 + 无新增指标；tech-stack-decisions：零新增依赖/表/migration + safe_div + resolve_time_range + 聚合 FILTER/CASE SQL 模式 + 索引复用 + modules/report+services/metric 落点）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U08-01 TimeRange 解析+编排 + P-U08-02 聚合 SQL FILTER+CASE 折算+URGE_EXPR + P-U08-03 safe_div null 后处理+level 着色，含完整伪代码；logical-components：modules/report 9 文件 + services/metric common+publish_progress + main 改动 + 无 migration + 依赖图 + 4 测试文件）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量：无新服务/表/migration/环境变量/Celery；复用 backend + U04/U02 表+索引 + report.publish_progress:read 已 seed；可选索引优化不强制；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成：services/metric（common.safe_div + publish_progress.like_sum_expr）+ modules/report 9 文件（domain resolve_time_range/level + repository 4 聚合显式 tenant 过滤 + service safe_div 组装 + api 4 GET）+ main 注册 report_router + 16 unit + 5 integration + 5 api；不改 migration/config/metrics/celery/default_roles
- [x] Build and Test — Docker（PG16:5550+Redis7:6405+Py3.12）；alembic 001→011 成功（无新 migration）；U08 子集 26 passed；全量 638 passed/0 failed；覆盖率 79.73%；**首次运行全通过，无生产 bug**；report 模块 domain/repository/schemas 100%

#### U07 — 企微集成基础（已完成）

- [x] Functional Design Plan 已生成（14 澄清问题预填 [Answer]；MVP 首个凭据加密单元 —— U07 落地 crypto.py 真实 AES-256-GCM + 每租户 HKDF；独立 wecom_contact 表不改 blogger；独立 message_template 表 + 变量白名单；wecom_message 6 态状态机；频控以 DB 当天计数为权威源；U07 创建 notification 表 + NotificationService；回调签名校验 403+audit；access_token Redis 缓存 7000s）
- [x] Functional Design — 3 文档完成（domain-entities：5 实体 wecom_config/wecom_contact/message_template/wecom_message/notification + 6 态状态机 + ER 图；business-rules：BR-U07-01~82 配置加密/绑定/模板/扫描聚合/频控降级/回调签名/token 缓存/权限多租户 + 错误码矩阵；business-logic-model：7 UC + J2 端到端时序 + 跨单元契约）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：加密/token/发送/扫描/频控 SLA + 异步失败语义 + 凭据加密&回调安全威胁模型 + 4 Prometheus 指标 + 测试；tech-stack-decisions：零新增依赖 httpx/cryptography 已 pin + AESGCM+HKDF 落地 crypto.py + token 缓存 + WecomClient 骨架 + 4 配置项 + Beat 注册）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U07-01 凭据加密落地 AESGCM+HKDF + P-U07-02 WecomClient 异步+token 缓存+失效重试 + P-U07-03 扫描编排逐租户聚合幂等 + P-U07-04 群发执行+频控降级每消息事务 + P-U07-05 回调签名+幂等推进，含完整伪代码；logical-components：modules/wecom 21 文件 + wecom_tasks + crypto/config/metrics/celery/main/promotion 改动 + migration 011 + 9 测试文件 + 依赖图 + 索引 + RLS）
- [x] Infrastructure Design — 2 文档完成（infrastructure-design：无新增 Zeabur 服务 + 公开回调路径白名单 + 出站企微 HTTPS + 4 环境变量三服务分布 + Beat 调度 09:00 错峰 + Redis token key + migration 011 + 复用 CREDENTIAL_MASTER_KEY；deployment-architecture：部署 checklist + 企微后台配置步骤 + 本地 mock + 回滚 + 监控；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 全部 5 批完成（modules/wecom 19 文件 + wecom_tasks + crypto/config/metrics/celery/main/promotion/conftest/.env 改动 + migration 011 + 19 单元 + 17 集成）
  - [x] Batch 1 — 基础+横切（crypto AESGCM+HKDF + config/metrics + wecom 基础 + 权限）
  - [x] Batch 2 — models + schemas + repository + domain
  - [x] Batch 3 — client + 7 services
  - [x] Batch 4 — api + callback_api + notification_api + deps + wecom_tasks + celery/main + promotion.find_urge_candidates
  - [x] Batch 5 — migration 011（5 表 + 索引 + RLS + 权限 seed）+ 19 单元 + 17 集成 + conftest + .env.example + 3 文档
  - [x] Build & Test — Docker（PG16:5549+Redis7:6404+Py3.12）；alembic 001→011 成功；U07 子集 36 passed；全量 612 passed/0 failed；覆盖率 79.20%；修 2 真实 bug（hashlib→hmac.compare_digest / notification 204→200 致 app 构造失败）+ 2 测试断言（session.refresh 丢未 flush 变更）

#### U06e — 结算导入适配器（已完成）
- [x] Functional Design Plan 已生成（12 澄清问题；语义敏感 —— 历史结算数据迁移 INSERT-only + promotion FK 派生 blogger/style/pr + 合成 request_event_id + UNIQUE(promotion_id) 一对一冲突失败 + 不触发事件；source=manual_settlement）
- [x] Functional Design — 3 文档完成（domain-entities：SettlementImportAdapter 契约 + manual_settlement 9 列映射 + promotion 派生 + 合成 event_id + INSERT-only 历史迁移；business-rules：BR-U06e-01~70；business-logic-model：5 UC + 端到端样本）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：4 项增量 Decimal/date/status + promotion 派生 + UNIQUE 一对一幂等 + 不触发事件 + 每行 3 往返；tech-stack-decisions：唯一增量 adapters/settlement.py + 合成 uuid4 event_id + IntegrityError catch）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U06e-01 INSERT-only + promotion 派生 + UNIQUE 冲突 catch + 合成 event_id + 不触发事件含完整伪代码；logical-components：唯一新组件 adapters/settlement.py + 复用 U04/U05 + 注册序列）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量，同 U06b/c/d；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成：adapters/settlement.py（INSERT-only 历史迁移 + promotion 派生 blogger/style/pr + settlement_no FB2 序列 + 合成 request_event_id uuid4 + UNIQUE(promotion_id) IntegrityError catch FB3 + 不触发事件）+ test_settlement_adapter（22 unit）+ test_import_settlement（2 integration）+ 3 文档；不改 main.py/celery_app.py/migration
- [x] Build & Test — Docker（PG16:5548+Redis7:6403+Py3.12）真实跑通；migration 001→010 全链路成功（无新 migration）；U06e 子集 24 passed；全量 576 passed/0 failed；覆盖率 79.30%；仅修 1 个测试断言（all-success runner 状态为 "completed" 非 "success"）；无生产 bug

#### U06d — 推广导入适配器（已完成）
- [x] Functional Design Plan 已生成（12 澄清问题；比 U06b/c 复杂 —— INSERT-only + 2 必需 FK 解析(style/blogger) + internal_code 系统生成 + 快照 + 3 状态默认；source=manual_promotion；幂等限制记入文档）
- [x] Functional Design — 3 文档完成（domain-entities：PromotionImportAdapter 契约 + manual_promotion 10 列映射 + _to_date + FK 解析 + INSERT-only；business-rules：BR-U06d-01~70；business-logic-model：5 UC + 端到端样本）
- [x] NFR Requirements — 2 文档完成（4 项增量 Decimal/date + FK 必需性 + 序列并发 + INSERT-only 幂等限制 + 每行 4-5 往返）
- [x] NFR Design — 2 文档完成（P-U06d-01 INSERT-only + FK 解析 + 序列生成含完整伪代码）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成：adapters/promotion.py（95% 覆盖）+ test_promotion_adapter（17 unit）+ test_import_promotion（2 integration）+ 3 文档；不改 main.py/celery_app.py/migration
- [x] Build & Test — Docker（PG16+Redis7+Py3.12）真实跑通；migration 001→010 全链路成功（无新 migration）；U06d 子集 19 passed；全量 552 passed/0 failed；覆盖率 78.87%；adapter 一次实现通过无生产 bug 无测试修复

#### U06c — 博主导入适配器（已完成）
- [x] Functional Design Plan 已生成（10 澄清问题；与 U06b 同构但更简单 —— 单实体 Blogger upsert，无 style-reuse；source=manual_blogger，新增标签 list/JSONB 解析）
- [x] Functional Design — 3 文档完成（domain-entities：BloggerImportAdapter 契约 + manual_blogger 13 列映射 + _split_tags/int/Decimal；business-rules：BR-U06c-01~60；business-logic-model：5 UC + 端到端样本）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：3 项增量 list/int/Decimal 解析 + 单实体每行1往返 + 幂等 + 跨租户；tech-stack-decisions：唯一增量 adapters/blogger.py + _split_tags/_to_int 标准库）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U06c-01 单实体 upsert + 多类型解析含完整伪代码；logical-components：唯一新组件 adapters/blogger.py + 复用 U03/U06a + 注册序列）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量，同 U06b；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成：adapters/blogger.py（99% 覆盖）+ test_blogger_adapter（20 unit）+ test_import_blogger（2 integration）+ 3 文档；不改 main.py/celery_app.py/migration
- [x] Build & Test — Docker（PG16+Redis7+Py3.12）真实跑通；migration 001→010 全链路成功（无新 migration）；U06c 子集 22 passed；全量 533 passed/0 failed；覆盖率 78.50%；修复 1 个测试数据问题（CSV 未引号逗号）；adapter 一次实现通过无生产 bug

#### U06b — 商品/SKU 导入适配器（已完成）
- [x] Functional Design Plan 已生成（12 澄清问题预填默认值；复用 U06a 框架 + U02 仓储，无新表/无新端点/不改 runner；source=manual_style_sku，一行=style 复用/建+sku upsert）
- [x] Functional Design — 3 文档完成（domain-entities：StyleSkuImportAdapter 契约 + manual_style_sku 默认映射；business-rules：BR-U06b-01~60 解析/校验/upsert/事务边界；business-logic-model：5 UC + 端到端样本 CSV）
- [x] NFR Requirements Plan 已生成（10 澄清问题；极小增量，无新依赖/服务/配置/指标）
- [x] NFR Requirements — 2 文档完成（nfr-requirements：4 项增量 Decimal/幂等/每行≤2-3 DB 往返/跨租户；tech-stack-decisions：唯一增量 adapters/style_sku.py + Decimal 标准库）
- [x] NFR Design Plan 已生成（8 澄清问题；1 增量模式 P-U06b-01，其余继承 U06a P-U06a-01~05 + U02 P-U02-03）
- [x] NFR Design — 2 文档完成（nfr-design-patterns：P-U06b-01 单行两实体 upsert 编排含 adapter 完整伪代码；logical-components：唯一新组件 adapters/style_sku.py + 复用清单 + 注册序列）
- [x] Infrastructure Design — 2 文档完成（零基础设施增量；infrastructure-design.md spec-format 假阳性 IGNORE）
- [x] Code Generation — 单批完成：adapters/__init__.py + adapters/style_sku.py（96% 覆盖）+ test_style_sku_adapter（15 unit）+ test_import_style_sku（2 integration）+ 3 文档；不改 main.py/celery_app.py/migration
- [x] Build & Test — Docker（PG16+Redis7+Py3.12）真实跑通；migration 001→010 全链路成功（无新 migration）；U06b 子集 17 passed；全量 511 passed/0 failed；覆盖率 78.17%；修复 2 个测试断言问题（sku 排序 / retry 状态守卫模拟）；adapter 一次实现通过无生产 bug

## 当前状态
- **生命周期阶段**: CONSTRUCTION
- **当前阶段**: V1 — U14 报表进阶 Infrastructure Design 完成（Plan + 2 文档）；4 设计阶段全部完成，进入 Code Generation
- **下一阶段**: 用户回复"继续"后生成 U14 Code Generation Plan + 代码（多批 + Build & Test Docker PG16:5557/Redis7:6412）
- **状态**: MVP 全部完成；V1 U09 + U10a + U10b + U11 + U12 + U13 完成；U14 4 设计阶段完成
- **MVP 进度**: **12/12 全部完成（U01-U05 + U06a-e + U07 + U08）**
- **V1 进度**: **6/8 交付（U09 + U10a + U10b + U11 + U12 + U13 完成）**；U14 进行中（Functional Design 完成）；V1 单元 = U09✓/U10a✓/U10b✓/U11✓/U12✓/U13✓/U14/U15
- **MVP 进度**: **12/12 全部完成（U01-U05 + U06a-e + U07 + U08）**
- **V1 进度**: **5/8 交付（U09 + U10a + U10b + U11 + U12 完成）**；U13 进行中（Functional Design 完成）；V1 单元 = U09✓/U10a✓/U10b✓/U11✓/U12✓/U13/U14/U15
- **MVP 进度**: **12/12 全部完成（U01-U05 + U06a-e + U07 + U08）**
- **V1 进度**: **2/8 交付（U09 + U10a 完成）**；U10b 进行中（Functional Design 完成）；V1 单元 = U09✓/U10a✓/U10b/U11/U12/U13/U14/U15
- **MVP 进度**: **12/12 全部完成（U01-U05 + U06a-e + U07 + U08）**
- **V1 进度**: **1/8 交付（U09 完成）**；U10a 进行中（Functional Design 完成）；V1 单元 = U09✓/U10a/U10b/U11/U12/U13/U14/U15

## 历史完成时间表
- 2026-05-24 ~05:40 U01 全部 5 阶段
- 2026-05-24 ~07:50 U02 全部 5 阶段
- 2026-05-24 ~08:10-09:10 U03 全部 5 阶段
- 2026-05-24 ~09:35 U04 Functional Design
- 2026-05-24 ~09:55 U04 NFR Requirements
- 2026-05-24 ~10:25 U04 NFR Design（8 P1 反馈修正落地）
- 2026-05-24 ~10:40 U04 Infrastructure Design
- 2026-05-26 ~08:00 U04 Code Generation Batch 1（Step 1-3）— 13 文件无诊断警告
- 2026-05-26 ~08:30 U04 Code Generation Batch 2（Step 4-5）— 4 文件无诊断警告
- 2026-05-26 ~09:00 U04 Code Generation Batch 3（Step 6-7）— 4 文件无诊断警告，端到端业务流程就绪
- 2026-05-26 ~09:30 U04 Code Generation Batch 4（Step 8-12）— 23 文件无诊断警告，U04 全单元交付完成
- 2026-05-26 ~15:00 U05 Code Generation Batch 1（Step 1-3）— 11 文件无诊断警告
- 2026-05-26 ~15:45 U05 Code Generation Batch 2（Step 4-5）— 5 文件无诊断警告，含 shared attachment 基础设施补齐
- 2026-05-26 ~16:30 U05 Code Generation Batch 3（Step 6-7）— 6 文件无诊断警告，端到端业务流程 + 双向 listener 就绪
- 2026-05-26 ~17:30 U05 Code Generation Batch 4（Step 8-13）— 约 20 文件无诊断警告，U05 全单元交付完成（3 migration + 15 测试 + frontend + CI/CD e2e-smoke 启用 + 3 文档）
- 2026-06-03 ~12:00 MVP-end Build & Test — Docker（PG16+Redis7+Py3.12）真实跑通；alembic upgrade head 全链路成功 + 433 passed/0 failed + 覆盖率 77.32%；修复 16 个集成问题（含 4 个阻断 CI 的生产 bug：CORS 解析 / migration 缺角色 / RLS 多语句 / 204 路由）
- 2026-06-04 U06a Code Generation Batch 1（Step 1-3）— 8 新建 + 3 修改无诊断警告：模块基础 + 横切扩展 + 模型/Adapter 协议/Registry
- 2026-06-04 U06a Code Generation Batch 2（Step 4-6）— 5 新建 + 2 修改无诊断警告：domain/repository/field_mapping_service/service/import_tasks + celery_app/main；高风险批次落地 NF-1（per-row SET LOCAL）+ NF-2（DB 先行+补偿）+ NF-3（原子 claim）+ NF-4（worker_process_init + autodiscover）+ FB-A/C/E
- 2026-06-04 U06a Code Generation Batch 3（Step 7-10）— migration 010 + default_roles + 10 测试文件（61 用例）+ frontend + ci/nginx + openpyxl/freezegun + 3 文档；U06a 全单元交付完成
- 2026-06-04 U06a Build & Test — Docker（PG16+Redis7+Py3.12）真实跑通；migration 010 全链路 alembic upgrade head 成功；importer 61 passed；全量 494 passed/0 failed；覆盖率 77.89%；修复 3 个真实/测试问题（asyncpg SET LOCAL `$1` 语法错误 → set_config / upload session.rollback 污染事务 + MissingGreenlet → SAVEPOINT + 捕获本地变量 / FieldMapping 批量 UPDATE 后 identity-map 陈旧 → populate_existing）
- 2026-06-07 U06b/c/d Code Generation + Build & Test — 各单批：adapters/{style_sku,blogger,promotion}.py + 单测 + 集成 + 3 文档；全量 511/533/552 passed；覆盖率 78.17/78.50/78.87%
- 2026-06-07 U06e Code Generation + Build & Test — 单批：adapters/settlement.py（INSERT-only 历史迁移 + promotion 派生 + settlement_no FB2 序列 + 合成 request_event_id + UNIQUE(promotion_id) IntegrityError catch FB3 + 不触发事件）+ 22 unit + 2 integration + 3 文档；Docker（PG16:5548+Redis7:6403）全量 576 passed/0 failed；覆盖率 79.30%；仅修 1 测试断言（all-success 状态 "completed"）；无生产 bug；**导入支线 U06a-e 全部交付完成，MVP 10/12**
- 2026-06-07 U07 企微集成基础 全部 5 阶段 + Code Generation 5 批 + Build & Test — modules/wecom 19 文件 + wecom_tasks + crypto 落地 AESGCM+HKDF + migration 011（5 表）+ 36 测试；Docker（PG16:5549+Redis7:6404）全量 612 passed/0 failed；覆盖率 79.20%；修 2 真实 bug（hmac.compare_digest / notification 204→200 app 构造失败）；**MVP 11/12，剩 U08**
- 2026-06-07 U08 发文进度看板 全部 5 阶段 + Code Generation 单批 + Build & Test — services/metric（safe_div + like_sum_expr）+ modules/report 9 文件（resolve_time_range + 4 聚合显式 tenant 过滤 + safe_div 组装 + 4 GET）+ 26 测试；Docker（PG16:5550+Redis7:6405）全量 638 passed/0 failed；覆盖率 79.73%；首次运行全通过无 bug；**MVP 12/12 全部交付完成** 🎉
