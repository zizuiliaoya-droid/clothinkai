# U13 NFR 设计计划（NFR Design Plan）

> 单元：U13 — 自动数据采集 Worker
> 模式：P-U13-01（Worker 鉴权+IP+自动吊销）、P-U13-02（poll SKIP LOCKED + 一次性 cred_token exchange）、P-U13-03（result→upload + 3 adapter 反查入库 + data quality）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：worker_token authenticate 失败计数的事务语义？
[Answer] authenticate 在独立小事务：命中且 IP 匹配 → 重置 consecutive_auth_failures=0 + last_seen_at；失败（token 不存在/IP 不匹配）→ 找到对应 token（按 token_hash）则 +1，达 5 自动吊销 + 通知；token 完全不存在则无计数对象（仅 401）。每次 authenticate 后 commit。

### Q2：poll 原子领取实现？
[Answer] 子查询 `SELECT id FROM crawler_task WHERE tenant_id=:t AND status='pending' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED` → UPDATE 该行 status='assigned'+worker_token_id+assigned_at+cred_token+expires_at RETURNING。无行 → 返回 None（204）。

### Q3：exchange 一次性如何保证？
[Answer] exchange 校验 status='assigned' + cred_token == 输入 + now < expires_at；通过则 decrypt + 清空 cred_token（设 NULL）+ status='exchanged' + commit。再次 exchange（cred_token 已 NULL 或 status≠assigned）→ 403。

### Q4：result 触发 upload 的事务边界？
[Answer] result(success)：先 upload_for_crawler（独立创建 import_batch + commit + run_import_batch.delay）→ 再更新 crawler_task.import_batch_id + status='success' + commit。upload 失败 → crawler_task status='failed' + report_failure。result(failed)：status='failed' + report_failure。

### Q5：3 adapter 的 data_quality issue 写入事务？
[Answer] adapter.upsert 内未匹配时调 DataQualityService.record（不自行 commit，复用 runner 每行事务）；record 仅 session.add（flush 由 runner 控制）。

### Q6：upload_for_crawler 如何复用 upload 主体？
[Answer] 抽取 upload 核心为 `_create_batch(content, source, tenant_id, actor_id, filename, content_type)`；upload（HTTP）和 upload_for_crawler（系统）都调它，actor_id 不同（worker 传 None，audit actor_type=worker via system_context）。

### Q7：schedule_daily_tasks 逐租户容错？
[Answer] 复用 U07 wecom_tasks 模式：AsyncSessionBypass 读全租户 → 逐租户 system_context + AsyncSessionApp + set_config + 查 active 凭据 + INSERT ON CONFLICT DO NOTHING + commit；单租户 catch+log+Sentry 不中止。

### Q8：指标埋点位置？
[Answer] crawler_poll_total 在 poll（assigned/empty/auth_failed）；crawler_task_total 在 report_result（success/failed）；worker_token_auth_failures_total 在 authenticate 失败；data_quality_issue_total 在 DataQualityService.record。

---

## 1. 步骤

- [x] 1.1 编写 nfr-design-patterns.md（P-U13-01 worker_token authenticate+IP+失败计数自动吊销 / P-U13-02 schedule + poll SKIP LOCKED + exchange 一次性 cred_token / P-U13-03 result→upload_for_crawler + 3 adapter find_by_platform_id 反查+未匹配 record issue+UNIQUE upsert + data quality summary 完整伪代码 + 一致性校验）
- [x] 1.2 编写 logical-components.md（modules/collect 14 文件 + importer/adapters 3 + 横切 5 改动(importer service/registry 调用/celery_app/metrics/main) + tasks/crawler_tasks + migration 017 + 依赖图无循环 + 4 测试文件）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
