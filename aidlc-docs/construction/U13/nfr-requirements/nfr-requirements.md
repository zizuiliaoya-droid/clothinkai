# U13 NFR 需求（NFR Requirements）

> 单元：U13 — 自动数据采集 Worker
> 增量式：复用 U01/U06a/U07/U12 基线 + Worker pull 安全 + crawler 队列

---

## 1. 依赖与复用

| 项 | 决策 |
|---|---|
| 新增第三方依赖 | **零**——worker_token 用标准库 secrets/hashlib；加密复用 U12；导入复用 U06a；通知复用 U07 |
| 新表 | 5 表（migration 017）：worker_token / crawler_task / data_quality_issue / qianniu_daily / ad_daily |
| 新环境变量 | **无**——复用既有；Worker 端 RPA 凭据走 cred_token exchange |
| 新 Celery 队列 | crawler（shared-infrastructure 已预留） |
| 新 Beat 任务 | schedule_daily_tasks 02:00 |
| Worker 端代码 | 不在本仓库（外部 RPA）；仅交付后端 API + 启动模板文档 |

---

## 2. 性能需求

| 操作 | SLA | 实现 |
|---|---|---|
| poll | P95 ≤ 100ms | FOR UPDATE SKIP LOCKED 单行 claim |
| exchange | P95 ≤ 50ms | 解密 + 状态更新单行 |
| result | P95 ≤ 300ms | upload + R2 写 + run_import_batch.delay |
| schedule_daily_tasks | ≤ 30s/租户 | 批量 INSERT ON CONFLICT DO NOTHING |
| data-quality/summary | P95 ≤ 200ms | GROUP BY source,severity + idx |

---

## 3. 安全需求（核心 — Worker API 网络暴露）

### 3.1 Worker 鉴权威胁模型

| 威胁 | 缓解 |
|---|---|
| 伪造 Worker | worker_token sha256 校验（明文一次性返回，独立于用户 JWT） |
| token 泄露后滥用 | IP allowlist 强制（空 allowlist 拒绝）+ 连续 5 次失败自动吊销 |
| 凭据明文泄露 | poll 响应不含明文密码——返回一次性 cred_token；exchange 才换明文 |
| cred_token 重放 | 一次性（用后清空）+ 5min TTL；复用/过期 → 403 |
| 越权领任务 | worker_token 跨租户隔离（RLS + token 绑定 tenant） |
| 审计缺失 | poll/exchange/result 各写 audit_log（worker_token_id/ip/purpose） |

### 3.2 明文密码处理

| 项 | 措施 |
|---|---|
| 日志 | 整链路禁记明文密码；只记 task_id/credential_id/masked username |
| 响应 | 仅 exchange 响应含明文（一次性）；poll/result 不含 |
| 内存 | Worker 端明文仅内存使用，不写文件（外部 Worker 约束，文档强制） |

---

## 4. 可靠性需求

| 项 | 决策 |
|---|---|
| 并发 poll | FOR UPDATE SKIP LOCKED 防重复领取 |
| 重复派发 | UNIQUE(tenant,platform,credential_id,target_date) |
| 采集失败 | result(failed) → report_failure（连续失败暂停凭据 + 告警） |
| cred_token 过期 | 任务可重新 poll 调度 |
| 调度容错 | schedule_daily_tasks 逐租户 catch+log+Sentry，单租户失败不中止 |
| 数据幂等 | qianniu_daily/ad_daily UNIQUE upsert；blogger.audience_profile 覆盖 |

---

## 5. 数据质量需求

| 项 | 说明 |
|---|---|
| 三级严重度 | info/warning/error（§4.3） |
| 未匹配处理 | adapter find_by_platform_id 未命中 → warning issue + 不阻塞入库 |
| error 阻断 | error 级别由消费方检查（U13 只写入 + 看板） |
| 看板 | source × severity 分组计数 |

---

## 6. 可观测性需求

| 指标 | 类型 | 标签 |
|---|---|---|
| `crawler_task_total` | Counter | platform, status |
| `crawler_poll_total` | Counter | result（assigned/empty/auth_failed） |
| `worker_token_auth_failures_total` | Counter | — |
| `data_quality_issue_total` | Counter | source, severity |

> Sentry tag crawler_platform=qianniu/wanxiangtai/huitun；actor_type=worker。

---

## 7. 多租户隔离

| 措施 | 说明 |
|---|---|
| RLS | 5 表全启用 |
| 调度 | schedule_daily_tasks 逐租户 system_context |
| worker_token 隔离 | A 租户 token 不可领 B 任务 |
| 测试 | bypass 角色聚合查询显式 WHERE tenant_id |

---

## 8. 数据迁移

| 项 | 决策 |
|---|---|
| migration 017 | 5 表 + RLS + UNIQUE + idx + 4 scope seed |
| 回填 | 无（新表） |
| 回滚 | downgrade DROP 5 表 + DELETE scope |

---

## 9. 测试需求

| 类型 | 覆盖 | 关键场景 |
|---|---|---|
| 单元 | adapter ≥ 85% | 3 adapter parse/validate/upsert + 反查未匹配 issue + cred_token 生成/校验 |
| 集成 | service ≥ 80% | 调度生成 + poll claim 并发 + exchange 解密 + result→upload + 失败联动 + RLS |
| API | ≥ 60% | Worker API 鉴权矩阵（无 token 401 / IP 403 / cred_token 过期 403 / 复用 403）+ data-quality 看板 + OpenAPI |
| 整体 | ≥ 70% | — |

### Worker 安全测试矩阵

| 场景 | 期望 |
|---|---|
| 无 X-Worker-Token poll | 401 |
| IP 不在 allowlist | 403 |
| cred_token 过期 exchange | 403 |
| cred_token 复用 | 403 |
| 跨租户领任务 | 隔离（领不到） |
| 连续 5 次失败 | token 自动吊销 + 告警 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| poll/exchange/result SLA | ✅ §2 |
| Worker 安全威胁模型 + 明文处理 | ✅ §3 |
| FOR UPDATE SKIP LOCKED 并发 + 幂等 | ✅ §4 |
| 4 指标 + Sentry tag | ✅ §6 |
| migration 017 + crawler 队列 + Beat | ✅ §8 |
| Worker 安全测试矩阵 | ✅ §9 |
| 与 functional-design EP07-S11~S14 + §2.2.1 一致 | ✅ |
