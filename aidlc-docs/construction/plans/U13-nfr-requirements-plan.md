# U13 NFR 需求计划（NFR Requirements Plan）

> 单元：U13 — 自动数据采集 Worker
> 增量式：复用 U01/U06a/U07/U12 基线 + 新增 Worker pull 安全 + crawler 队列

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否引入新依赖？
[Answer] 零新增依赖。worker_token 用标准库 secrets + hashlib；加密复用 U12 crypto.py；导入复用 U06a；通知复用 U07；Worker 端（外部 RPA）不在本仓库（仅提供启动模板文档）。

### Q2：Worker pull/exchange/result 性能 SLA？
[Answer] poll P95 ≤ 100ms（单行 FOR UPDATE SKIP LOCKED claim）；exchange P95 ≤ 50ms（解密 + 状态更新）；result P95 ≤ 300ms（upload + R2 写）；schedule_daily_tasks ≤ 30s/租户（批量 INSERT ON CONFLICT）。

### Q3：安全威胁模型（Worker API 网络暴露）？
[Answer] 核心安全单元：
- worker_token 鉴权（sha256 存储，明文一次性返回，独立于 JWT）
- IP allowlist 强制（空 allowlist 拒绝）
- 一次性 cred_token exchange（5min TTL + 用后失效），明文密码绝不进 poll 响应/日志
- poll/exchange/result 全审计（worker_token_id/ip/purpose）
- 连续 5 次鉴权失败自动吊销 token + 企微告警
- /api/crawler/* 不接受用户 JWT；Worker token 与用户体系隔离

### Q4：并发 poll 防重复领取？
[Answer] `FOR UPDATE SKIP LOCKED` 原子领取——多 Worker 并发 poll 不会领同一任务（PG 行锁跳过已锁行）。UNIQUE(tenant,platform,credential_id,target_date) 防同日重复派发。

### Q5：采集数据幂等？
[Answer] qianniu_daily / ad_daily UNIQUE(tenant,platform_id_snapshot,date) → adapter upsert ON CONFLICT DO UPDATE 幂等（重跑覆盖）；huitun 更新 blogger.audience_profile 按 xiaohongshu_id 幂等覆盖。

### Q6：容量假设？
[Answer] 单租户凭据 ≤ 30 → 每日任务 ≤ 30；qianniu_daily/ad_daily 每租户每日数百~数千行；data_quality_issue 低频。无高并发压力。

### Q7：可观测性指标？
[Answer] 追加 4 指标：
- `crawler_task_total{platform, status}`（success/failed 计数）
- `crawler_poll_total{result}`（assigned/empty/auth_failed）
- `worker_token_auth_failures_total`（鉴权失败计数）
- `data_quality_issue_total{source, severity}`（异常写入计数）
Sentry tag crawler_platform；actor_type=worker。

### Q8：可靠性 / 失败处理？
[Answer] result(failed) → CredentialService.report_failure（连续失败暂停凭据）；cred_token 过期任务可重新调度；Worker 上传失败走 U06a 既有 batch retry；schedule_daily_tasks 逐租户容错（单租户失败不中止，catch+log+Sentry）。

### Q9：多租户隔离？
[Answer] crawler_task/worker_token/data_quality_issue/qianniu_daily/ad_daily 全 RLS；schedule 逐租户 system_context；测试 bypass 角色显式 WHERE tenant_id。worker_token 跨租户隔离（A token 不可领 B 任务）。

### Q10：migration + 队列 + Beat？
[Answer] migration 017：5 表 + RLS + UNIQUE + idx + crawler.worker:write / crawler.task:read / data_quality:read / data_quality:write scope seed；新增 crawler Celery 队列；Beat schedule_daily_tasks 02:00（默认启用，与 03:00 备份错峰）。

### Q11：测试覆盖？
[Answer] service ≥ 80%、adapter ≥ 85%、api ≥ 60%；整体 ≥ 70%。Worker 安全测试矩阵必含：无 token 401 / IP 不匹配 403 / cred_token 过期 403 / cred_token 复用 403 / 跨租户隔离。

### Q12：Worker 端代码归属？
[Answer] 外部 RPA Worker（千牛/万相台/灰豚登录采集）不在本后端仓库——本单元只交付后端 API + 调度 + adapter + 数据表；Worker 启动模板放 `rpa-worker/README.md`（文档，非代码）。

---

## 1. 步骤

- [x] 1.1 编写 nfr-requirements.md（零依赖 + poll/exchange/result SLA + Worker 安全威胁模型 + FOR UPDATE SKIP LOCKED 并发 + 幂等 + 4 指标 + 多租户隔离 + migration 017 + crawler 队列 + Beat + 测试矩阵）
- [x] 1.2 编写 tech-stack-decisions.md（零依赖 secrets/hashlib + modules/collect 落点 + 3 adapter importer/adapters + ImportService.upload_for_crawler 封装 + WorkerToken 鉴权依赖 + 4 metrics + migration 017 片段 + crawler 队列/Beat + 测试落点）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
