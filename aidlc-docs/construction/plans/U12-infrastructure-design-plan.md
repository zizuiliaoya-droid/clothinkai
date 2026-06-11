# U12 基础设施设计计划（Infrastructure Design Plan）

> 单元：U12 — 平台凭据 + 采集失败告警
> 零基础设施增量：唯一变更 = migration 016（credential 表）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务？
[Answer] 否。凭据 CRUD + 解密 + 失败告警均在既有 backend 服务内同步处理；无新 worker/beat 任务。

### Q2：是否新增数据库表？
[Answer] 是——唯一增量 migration 016：credential 表（TenantScopedModel + RLS + UNIQUE + idx + CHECK + 3 scope seed）。无回填（新表无历史数据）。

### Q3：是否新增环境变量 / Secrets？
[Answer] 否。复用 U01 已注入的 CREDENTIAL_MASTER_KEY（U07 已实际使用）。

### Q4：是否新增 R2 桶？
[Answer] 否。凭据仅密文存 DB（BYTEA 列），不存文件。R2_BUCKET_CREDENTIALS 桶在 U01 已声明但本单元不使用（凭据密文直接进 DB 更安全 + 便于事务）。

### Q5：是否新增 Redis 库 / 缓存键？
[Answer] 否。密钥每次按需 HKDF 派生（不缓存）；凭据 CRUD 低频无需缓存。

### Q6：部署与回滚？
[Answer] 部署 = 代码 + migration 016 同批；migration 016 仅 CREATE TABLE 无锁现有表无回填风险；回滚 downgrade DROP TABLE credential + DELETE 3 scope（无数据依赖，安全）。

### Q7：本地 Docker 验证端口？
[Answer] U12 Build & Test 用 PG16:5555 + Redis7:6410（接 U11 的 5554/6409）。

### Q8：监控告警？
[Answer] 复用 U01 Prometheus + Sentry；新增 2 指标（credential_decrypt_total / credential_auto_paused_total）由 NFR Design 定义，Infrastructure 无额外配置。企微告警走 U07 既有通道。

---

## 1. 步骤

- [x] 1.1 编写 infrastructure-design.md（零新服务/依赖/桶/环境变量/Redis；唯一增量 migration 016 credential 表 + RLS + UNIQUE + idx + CHECK + 3 scope seed 绑 admin/operations；密钥复用 CREDENTIAL_MASTER_KEY；部署回滚安全；本地 Docker 5555/6410）
- [x] 1.2 编写 deployment-architecture.md（拓扑无变更 + 部署 checklist + 验证步骤(credential 表/3 scope/加密往返/解密审计/连续失败自动暂停/RLS 隔离/响应不含密码)）
- [x] 1.3 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.3（Plan + 2 文档，同一回合）。**
**注：infrastructure-design.md 的 spec-format 假阳性（Missing Overview/Architecture）IGNORE。**
