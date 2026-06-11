# U07 基础设施设计计划（Infrastructure Design Plan）

> 单元：U07 — 企微集成基础
> 范围：U07 基础设施增量（公开回调端点可达性 / 4 环境变量 / Beat 调度新增 / migration 011 / 出站企微 HTTPS）；无新增 Zeabur 服务
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档（infrastructure-design.md + deployment-architecture.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 公开回调端点暴露
- [Answer] 复用现有 backend 服务 + api 子域（无新服务）；`/api/wecom/callback/{tenant_id}` 在 Tenancy/Auth 中间件中加入**公开路径白名单**（无 JWT，仅签名校验）。企微后台配置回调 URL = `https://api.<domain>/api/wecom/callback/<tenant_id>`。

### Q2 — 出站企微 HTTPS（香港节点可达性）
- [Answer] Zeabur 香港节点可直连 `qyapi.weixin.qq.com`；WecomClient 走 httpx HTTPS，无需代理。`WECOM_API_BASE` 可配置（便于本地 mock/测试指向桩服务）。

### Q3 — 新增环境变量分布
- [Answer] backend + celery-worker + celery-beat 三服务均注入 WECOM_API_BASE / WECOM_HTTP_TIMEOUT / WECOM_TOKEN_TTL / WECOM_URGE_SCAN_CRON（worker 执行群发、beat 调度扫描、backend 配置/绑定/test/回调）；`CREDENTIAL_MASTER_KEY` 已在三服务（U01 既有）。均有默认值，未配 wecom_config 时扫描跳过。

### Q4 — Beat 调度新增
- [Answer] celery-beat 服务 beat_schedule 追加 `wecom-urge-scan`（crontab 09:00，default 队列）；与既有备份/清理调度并存，无冲突（错峰：备份 03:00/清理 04:xx/催发 09:00）。

### Q5 — migration 011 部署
- [Answer] 复用 U01 既有 migrate job（手动触发 deploy-prod/staging 前 `alembic upgrade head`）；011 接 010 head；含 5 表 + RLS + 权限 seed，幂等可重入（CREATE 受 alembic 版本控制）。

### Q6 — Redis 用量
- [Answer] 复用 cache db=0：新增 key `wecom:token:{tenant_id}`（TTL 7000s，每租户 1 key，量极小）；不影响 256MB Redis 容量规划。

### Q7 — 凭据密钥管理
- [Answer] 复用 U01 `CREDENTIAL_MASTER_KEY`（Zeabur Secrets，base64 32B）；U07 用其派生每租户密钥。无新增 Secret。生产/staging 各自独立 master key（轮换 P1+）。

---

## 2. 执行步骤

- [x] 2.1 `U07/infrastructure-design/infrastructure-design.md`：服务拓扑（复用 backend+worker+beat+redis+pg，无新服务）+ 公开回调路径白名单 + 出站企微 HTTPS + 4 环境变量映射 + Beat 调度 + Redis key + Secrets 复用 + migration 011
- [x] 2.2 `U07/infrastructure-design/deployment-architecture.md`：部署增量 checklist（环境变量注入三服务 + 回调 URL 配置 + migrate 011 + 企微后台配置步骤）+ 回滚 + 本地 docker-compose（mock 企微）+ 监控告警接入
- [x] 2.3 诊断器（infrastructure-design.md spec-format 假阳性 IGNORE）+ 与 nfr-design 一致

---

**等待用户"继续"；本轮直接生成 2 份基础设施设计文档。**
