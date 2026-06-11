# U13 业务规则（Business Rules）

> 单元：U13 — 自动数据采集 Worker
> 故事：EP07-S11~S14

---

## 1. Worker 鉴权与安全（§2.2.1）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-01 | `/api/crawler/*` 端点用 `X-Worker-Token` 头鉴权（独立于用户 JWT），不接受 Bearer JWT | §2.2.1 |
| BR-U13-02 | 校验 token_hash=sha256(明文) 命中 active WorkerToken；未命中 → 401 | §2.2.1 |
| BR-U13-03 | 校验请求 IP 在 worker_token.ip_allowlist 内（allowlist 为空视为拒绝）；不匹配 → 403 | §2.2.1 |
| BR-U13-04 | 连续 WORKER_AUTH_FAILURE_THRESHOLD（默认 5）次鉴权失败（token 不存在/IP 不匹配/已吊销）→ is_active=false + 企微告警 admin | §2.2.1 |
| BR-U13-05 | worker_token 明文仅签发时返回一次（DB 存 hash）；后续不可回显 | §2.2.1 |
| BR-U13-06 | poll/exchange/result 三动作各写 audit_log（worker_token_id, ip, purpose, timestamp） | §2.2.1 |

## 2. 任务调度（EP07-S11~S13）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-10 | Celery Beat `schedule_daily_tasks` 默认每天 02:00 触发（crawler 队列） | EP07-S11 |
| BR-U13-11 | 逐租户逐 active 凭据生成 CrawlerTask（target_date=昨天，status=pending） | EP07-S11 |
| BR-U13-12 | paused/disabled 凭据跳过（不生成任务） | BR-U12-42 |
| BR-U13-13 | UNIQUE(tenant,platform,credential_id,target_date) 防同日重复派发（已存在跳过） | 幂等 |

## 3. Worker poll / exchange / result

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-20 | poll 领取一个 pending 任务（原子 UPDATE status=assigned + worker_token_id + assigned_at） | EP07-S11 |
| BR-U13-21 | poll 响应含 cred_token（token_urlsafe(32) 一次性）+ expires_at（now+5min），**不含明文密码** | §2.2.1 |
| BR-U13-22 | 无 pending 任务 → poll 返回 204 / null | — |
| BR-U13-23 | exchange 校验 cred_token 匹配 + 未过期 + status=assigned → 调 CredentialService.decrypt_for_purpose(purpose="crawler_{platform}") 返回明文 | §2.2.1 |
| BR-U13-24 | exchange 成功后立即清空 cred_token（一次性，再次 exchange → 403） + status=exchanged | §2.2.1 |
| BR-U13-25 | cred_token 过期（>5min）→ exchange 403 + 任务可重新调度 | §2.2.1 |
| BR-U13-26 | result(success) → ImportService.upload_for_crawler(source, tenant_id) → import_batch_id 回填 + status=success + CredentialService.report_success | EP07-S11 |
| BR-U13-27 | result(failed) → status=failed + error_reason + CredentialService.report_failure（连续失败暂停凭据 + 告警） | EP07-S06 |
| BR-U13-28 | 整个链路日志/响应禁明文密码；只记 task_id/credential_id/masked username | §2.2.1 |

## 4. 3 Adapter upsert（EP07-S11~S13）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-30 | QianniuAdapter：find_by_platform_id(qianniu, platform_id) 反查 platform_product → 填 platform_product_id | EP07-S11 |
| BR-U13-31 | 未匹配 platform_product → platform_product_id=NULL + DataQualityIssue(source=qianniu, severity=warning, entity_ref=platform_id)，不阻塞入库 | §4.3 |
| BR-U13-32 | qianniu_daily UNIQUE(tenant,platform_id_snapshot,date) upsert 幂等（重跑覆盖） | EP07-S11 GWT |
| BR-U13-33 | WanxiangtaiAdapter → ad_daily 同模式 | EP07-S12 |
| BR-U13-34 | HuitunAdapter：按 xiaohongshu_id 匹配 blogger → 更新 audience_profile JSONB；未匹配 → DataQualityIssue(warning) | EP07-S13 |
| BR-U13-35 | 3 adapter 实现 ImportAdapter 协议，注册到 ImportAdapterRegistry（HTTP lifespan + worker_process_init 双进程） | U06a NF-4 |
| BR-U13-36 | adapter upsert 不自行 commit（runner 每行事务） | U06a FB-C |

## 5. 数据质量看板（EP07-S14）

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-40 | data_quality_issue 三级严重度 info/warning/error | §4.3 |
| BR-U13-41 | error 级别由消费方检查阻断业务（U13 只写入 + 看板，不主动阻断） | §4.3 |
| BR-U13-42 | `GET /api/data-quality/summary` 按 source × severity 分组计数 | EP07-S14 GWT |
| BR-U13-43 | `GET /api/data-quality/issues` 列表筛选（source/severity/status）+ 分页 | EP07-S14 |
| BR-U13-44 | `PUT /api/data-quality/issues/{id}` resolve(status=fixed/ignored) + audit | EP07-S14 |

## 6. 权限

| 编号 | 规则 | 来源 |
|---|---|---|
| BR-U13-50 | worker_token 管理（签发/吊销）= admin（crawler.worker:write） | §2.2.1 |
| BR-U13-51 | data_quality 看板 read = admin/operations（data_quality:read）；resolve = admin（data_quality:write） | EP07-S14 |
| BR-U13-52 | crawler_task 查看（运维监控）= admin（crawler.task:read） | — |
| BR-U13-53 | Worker API 不走 require_permission（用 worker_token 鉴权） | §2.2.1 |

---

## 7. 错误码矩阵

| 场景 | HTTP | code |
|---|---|---|
| worker_token 无效 | 401 | WORKER_TOKEN_INVALID |
| IP 不在 allowlist | 403 | WORKER_IP_FORBIDDEN |
| cred_token 无效/过期/已用 | 403 | CRED_TOKEN_INVALID |
| 任务不存在 | 404 | CRAWLER_TASK_NOT_FOUND |
| data_quality issue 不存在 | 404 | DQ_ISSUE_NOT_FOUND |
| 无 pending 任务 | 204 | — |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| Worker 安全边界全覆盖（token/IP/cred_token/TTL/审计/自动吊销） | ✅ §1 §3 |
| 调度 + 幂等防重复 | ✅ §2 |
| 3 adapter 反查 + 未匹配记 issue 不阻塞 | ✅ §4 |
| data quality 看板 + 三级严重度 | ✅ §5 |
| 失败联动凭据（report_failure） | ✅ BR-U13-27 |
| EP07-S11~S14 全覆盖 | ✅ |
