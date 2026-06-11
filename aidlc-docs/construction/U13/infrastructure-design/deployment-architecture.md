# U13 部署架构（Deployment Architecture）

> 单元：U13 — 自动数据采集 Worker

---

## 1. 拓扑

```
                  ┌─────────── Zeabur ───────────┐
frontend ───────▶ backend (FastAPI)              │
                  │   /api/crawler/* (worker_token 鉴权)
                  │   /api/data-quality/*         │
                  ├─ celery-worker (-Q ...,crawler)
                  ├─ celery-beat (schedule_daily_tasks 02:00)
                  ├─ PostgreSQL (+5 表)            │
                  └─ Redis (crawler 队列)          │
                  └──────────────▲────────────────┘
                                 │ HTTPS pull (X-Worker-Token + IP)
                  ┌──────────────┴────────────────┐
                  │  外部 RPA Worker (自建 VM)      │
                  │  poll → exchange → 采集 → result │
                  └────────────────────────────────┘
```

后端无新服务；celery-worker 增订 crawler 队列；外部 RPA Worker 旁路。

---

## 2. 部署 Checklist

- [ ] 合并代码（modules/collect + 3 adapter + tasks/crawler_tasks + 横切改动）到 main
- [ ] migrate job 执行 alembic upgrade head（含 017）
- [ ] 更新 celery-worker 启动命令 `-Q default,backup,wecom,crawler`
- [ ] 确认 celery-beat 加载 schedule_daily_tasks（02:00）
- [ ] backend 重启加载 /api/crawler/* + /api/data-quality/* 路由
- [ ] 管理员 issue worker_token + 配置外部 Worker（token + IP allowlist）
- [ ] 验证 /api/crawler/* 无 token → 401

---

## 3. 验证步骤（部署后）

| # | 验证项 | 期望 |
|---|---|---|
| 1 | 5 表存在 | worker_token/crawler_task/data_quality_issue/qianniu_daily/ad_daily |
| 2 | scope seed | crawler.worker:write/crawler.task:read/data_quality:read|write；admin/operations 绑定 |
| 3 | RLS 生效 | 5 表 rowsecurity=true |
| 4 | Worker 无 token | poll 401 |
| 5 | IP 不匹配 | 403 |
| 6 | poll 领任务 | 返回 cred_token（无明文密码） |
| 7 | exchange | 返回明文（一次性）；再次 exchange → 403 |
| 8 | cred_token 过期 | exchange 403 |
| 9 | result success | 触发 import + import_batch_id 回填 + qianniu_daily 入库 |
| 10 | 未匹配 platform_id | data_quality_issue(warning) + 不阻塞 |
| 11 | result failed ×3 | 凭据自动暂停 + 企微告警 |
| 12 | data-quality/summary | source × severity 计数 |
| 13 | 连续 5 次鉴权失败 | worker_token 自动吊销 |

---

## 4. 外部 RPA Worker 启动要点（rpa-worker/README.md）

```
循环：
1. POST /api/crawler/tasks/poll (X-Worker-Token)  → task + cred_token (或 204 sleep)
2. POST /api/crawler/tasks/{id}/exchange {cred_token} → {username, password}
3. 浏览器自动化登录平台 → 导出昨日数据 CSV（明文密码仅内存）
4. POST /api/crawler/tasks/{id}/result (multipart file, status=success)
   失败 → status=failed + error
约束：明文密码不落盘/不写日志；token + IP 由管理员配置
```

---

## 5. 监控

| 项 | 说明 |
|---|---|
| crawler_task_total{platform,status} | 采集成功/失败率 |
| crawler_poll_total{result} | poll assigned/empty/auth_failed |
| worker_token_auth_failures_total | 鉴权失败（异常飙升=攻击/配置错） |
| data_quality_issue_total{source,severity} | 数据质量异常趋势 |
| Sentry | crawler_platform tag + 调度容错 capture |
| 企微告警 | worker_token 吊销 + 凭据连续失败 |

---

## 6. 回滚

| 步骤 | 命令 |
|---|---|
| 1. 移除 Beat 调度 | celery-beat 配置回退 |
| 2. DB 回滚 | alembic downgrade 016（DROP 5 表 + DELETE scope） |
| 3. worker 队列 | celery-worker -Q 移除 crawler |
| 4. 代码回滚 | Zeabur 切回上一版本 |
| 5. 外部 Worker | 停止 poll |
| 风险 | 中——确认无外部 Worker 仍在 poll（会收 401/404） |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 拓扑：后端无新服务 + 外部 Worker 旁路 | ✅ |
| Checklist + 13 验证步骤 | ✅ |
| 外部 Worker 启动模板要点 | ✅ |
| 监控 4 指标 + Sentry + 企微 | ✅ |
| 回滚（含 Beat + 队列 + Worker） | ✅ |
| 本地 Docker 5556/6411 | ✅ |
