# U09 基础设施设计计划（Infrastructure Design Plan）

> 单元：U09 — 字段级权限 + 自定义权限
> 范围：基础设施增量极小 —— 仅 migration 012（字段 scope permission seed）；无新服务/表/依赖/环境变量
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档（infrastructure-design.md + deployment-architecture.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增 Zeabur 服务
- [Answer] **零新增服务**：复用既有 backend（FastAPI）；无 worker/beat 改动（无 Celery 任务）。

### Q2 — 数据库变更
- [Answer] **无 DDL / 无新表**；仅 migration 012 向既有 permission 表 INSERT 18 个字段 scope 定义（ON CONFLICT (scope) DO NOTHING）；不改 role_permission。接 011 head。

### Q3 — 环境变量 / Secrets
- [Answer] **零新增**：复用既有 JWT/DB/Redis 配置；字段权限纯代码常量，无配置项。

### Q4 — Redis 使用
- [Answer] 复用既有权限缓存库（cache）；新增 `fieldctx:user:<id>` key（与 perm cache 同库同 TTL=PERM_CACHE_TTL）；无新 Redis 实例/库分片。

### Q5 — 部署顺序
- [Answer] migration 012 经既有 migrate.yml job 执行（手动触发或部署前）；代码与 migration 同批；字段权限默认按注册表角色，seed 仅为支持自定义 grant/revoke 的 scope 存在性校验，无回填风险。

### Q6 — 回滚
- [Answer] migration 012 downgrade 删除 18 个字段 scope（连带级联清理引用这些 scope 的 user_permission_override，PG FK 行为按既有约束）；代码回滚即恢复 4 legacy 行为（注册表值与 legacy 一致，无数据迁移）。

### Q7 — CI/CD
- [Answer] 复用既有 ci.yml；migration 012 纳入 alembic upgrade head 全链路；无新 workflow；4 模块回归测试纳入既有 pytest 套件。

### Q8 — 监控告警
- [Answer] 无新增 Prometheus 指标；grant/revoke structlog + audit_log；FIELD_PERMISSION_DENIED 计入既有 HTTP 4xx 指标（instrumentator）。

---

## 2. 执行步骤

- [x] 2.1 `U09/infrastructure-design/infrastructure-design.md`：零基础设施增量说明 + migration 012 完整 DDL（INSERT 18 scope ON CONFLICT DO NOTHING + downgrade）+ Redis fieldctx key + 复用清单 + 部署/回滚
- [x] 2.2 `U09/infrastructure-design/deployment-architecture.md`：部署 checklist（migration 012 + 代码同批）+ 验证步骤（effective-permissions 端点 + 4 模块回归）+ 回滚步骤 + 无新服务确认
- [x] 2.3 诊断器无警告（infrastructure-design.md spec-format 假阳性 IGNORE）+ 与 nfr-design 一致

---

**等待用户"继续"；本轮直接生成 2 份基础设施设计文档。**
