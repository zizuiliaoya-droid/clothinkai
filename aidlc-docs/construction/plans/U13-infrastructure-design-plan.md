# U13 基础设施设计计划（Infrastructure Design Plan）

> 单元：U13 — 自动数据采集 Worker
> 增量：migration 017（5 表）+ crawler Celery 队列 + Beat 任务 + Worker 网络暴露 API + 外部 RPA Worker 部署

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务？
[Answer] 后端无新服务——crawler 任务在既有 celery-worker（处理 crawler 队列）+ celery-beat（schedule_daily_tasks 调度）。**外部 RPA Worker** 独立部署在自建 VM/Docker 主机（不在 Zeabur），通过 HTTPS 调 /api/crawler/* pull 模型，与后端解耦。

### Q2：新 Celery 队列？
[Answer] crawler 队列（shared-infrastructure 已预留）；celery-worker 启动需 `-Q default,backup,wecom,crawler`（部署文档更新 worker 队列订阅）。Beat 新增 schedule_daily_tasks 02:00（与 03:00 备份错峰）。

### Q3：新数据库表？
[Answer] migration 017：5 表（worker_token/crawler_task/data_quality_issue/qianniu_daily/ad_daily）+ RLS + UNIQUE + idx + 4 scope seed。无回填。

### Q4：新环境变量 / Secrets？
[Answer] 无新增。worker_token 明文由 issue API 生成返回（管理员配置到外部 Worker）；凭据走 U12 CREDENTIAL_MASTER_KEY（已有）。

### Q5：Worker API 网络安全（暴露端点）？
[Answer] /api/crawler/* 网络暴露——必须：worker_token 鉴权（X-Worker-Token）+ IP allowlist（在 worker_token 配置）+ HTTPS（Zeabur 自动 TLS）。建议条件允许时在 Zeabur 边缘加 IP 白名单或 mTLS（文档建议，非 U13 强制实现，应用层 IP allowlist 已覆盖）。绝不无鉴权暴露。

### Q6：R2 桶 / 路径？
[Answer] 复用 private 桶 imports/ 路径（U06a）——Worker result 上传文件经 ImportService.upload_for_crawler 走既有 R2 imports/{tenant}/{batch}/ 路径。无新桶/路径。

### Q7：Sentry？
[Answer] 复用既有 Sentry；新增 tag crawler_platform=qianniu/wanxiangtai/huitun；actor_type=worker（已在 tag 枚举）。

### Q8：部署与回滚？
[Answer] 部署 = 代码 + migration 017 + celery-worker 队列订阅更新 + celery-beat 新调度；回滚 downgrade 016（DROP 5 表 + DELETE scope）+ 移除 Beat 调度 + 代码回退。外部 Worker 可随时停（pull 模型，停止 poll 即停采集）。

### Q9：本地 Docker 验证端口？
[Answer] U13 Build & Test 用 PG16:5556 + Redis7:6411（接 U12 的 5555/6410）。

### Q10：外部 Worker 启动模板？
[Answer] rpa-worker/README.md 文档（非代码）：说明 poll→exchange→采集→result 循环 + worker_token 配置 + IP 注册 + 明文不落盘约束。

---

## 1. 步骤

- [x] 1.1 编写 infrastructure-design.md（无新 Zeabur 服务；crawler 队列 + celery-worker 队列订阅更新 + Beat schedule_daily_tasks 02:00；migration 017 5 表 DDL + RLS + 4 scope seed；Worker 网络安全 worker_token+IP allowlist+HTTPS；复用 private 桶 imports/；外部 RPA Worker 独立部署 pull 解耦；本地 Docker 5556/6411）
- [x] 1.2 编写 deployment-architecture.md（拓扑：后端无新服务 + 外部 Worker 旁路；部署 checklist + worker 队列订阅 + Beat 调度 + 验证步骤(5 表/scope/Worker 鉴权矩阵/poll-exchange-result/3 adapter 入库/data quality 看板)+ 外部 Worker 启动模板要点 + 回滚）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
**注：infrastructure-design.md 的 spec-format 假阳性（Missing Overview/Architecture）IGNORE。**
