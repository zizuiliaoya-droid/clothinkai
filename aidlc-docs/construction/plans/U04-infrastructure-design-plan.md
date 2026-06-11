# U04 基础设施设计计划（Infrastructure Design Plan）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性基础设施增量；通用基础设施全部继承 U01 + shared-infrastructure

---

## 1. 单元上下文

### 1.1 与 U01-U03 基础设施的关系

U01 已建立完整 6 服务部署 + PG + Redis + R2 4 桶 + Sentry + GitHub Actions。  
U02 启用 pg_trgm 扩展。

**U04 增量极小化**：
- 不新增 Zeabur 服务、域名、证书、Secrets、环境变量
- PostgreSQL：2 张新表（promotion / promotion_sequence）+ 13 个索引（含 GIN trgm + 复合索引）+ 1 条 RLS 策略
- R2：不使用（U04 暂无附件）
- Sentry：新增 `module=promotion` tag
- Prometheus：新增 4 个自定义指标
- 关键约束：**U04 必须与 U05 同批部署**（FB1 反馈对应）

### 1.2 关键部署一致性约束（FB1+FB10）
- migration 005_u04 + 006_u05 同批 alembic upgrade
- staging smoke test 强制：`test_review_approve_creates_settlement_via_event` 端到端通过
- CI grep 检查：`from app.modules.finance.listeners import register` 调用链存在
- 启动时 register_finance 失败 fail fast

---

## 2. 计划步骤

### Step 1 — 分析设计文档
- [x] 1.1 读取 U04 7 份设计文档
- [x] 1.2 与 U02/U03 部署模板对齐

### Step 2 — 创建本计划
- [x] 2.1 列出 U04 增量
- [x] 2.2 列出澄清问题（已预填）

### Step 3 — 生成 infrastructure-design.md
- [x] 3.1 资源清单
- [x] 3.2 PostgreSQL 增量（2 表 + 13 索引 + 1 RLS）
- [x] 3.3 Sentry tag 增量
- [x] 3.4 Prometheus 指标增量
- [x] 3.5 与 shared-infrastructure 对齐

### Step 4 — 生成 deployment-architecture.md
- [x] 4.1 Migration 005 完整代码
- [x] 4.2 三阶段部署流程 + **U04/U05 同批部署强约束**
- [x] 4.3 验证清单 + 端到端 smoke test
- [x] 4.4 回滚预案

### Step 5 — 完成消息

---

## 3. 澄清问题（请填 [Answer]）

### 3.1 R2 / 角色 / 队列

**Q1**：U04 是否使用 R2？

[Answer]: 不使用。U04 阶段 promotion 表无附件字段；后续若 V1+ 需要发布截图等，再加 R2 路径。

**Q2**：是否需要新增 PostgreSQL 角色？

[Answer]: 不需要。沿用 U01 三个角色（clothing_app / clothing_bypass / clothing_archiver）。

**Q3**：是否需要新增 Celery 队列？

[Answer]: 不需要。U04 内部事件总线本地同事务（无需异步），不引入 Celery 任务。

### 3.2 部署强约束

**Q4**：U04+U05 同批部署的 CI/CD 强制策略？

[Answer]: 多层防护：
- **同 PR**：U04 代码 + U05 代码必须在同一 PR 提交（或 U04 PR 引用 U05 PR 必须先合并）
- **migration 同批**：alembic upgrade 005 + 006 一次性执行
- **CI grep 检查**：`.github/workflows/ci.yml` 增加 `grep -rn "from app.modules.finance.listeners import register" backend/app/main.py` 必须命中
- **staging smoke**：`.github/workflows/deploy-staging.yml` 之后必跑 `test_review_approve_creates_settlement_via_event`，失败禁 production 部署
- **启动检查**：production 启动时 register_finance 抛 `ModuleNotFoundError` → 视为 ops 错误，container restart loop 直到正确部署

### 3.3 Migration 通道

**Q5**：U04 migration 执行方式？

[Answer]: 与 U01-U03 完全一致 — 通过 `migrate.yml` 专用 job，先 staging 验证后再 production。流程：
1. PR 合并到 main
2. 手动触发 migrate.yml(env=staging)，**一次性升 005_u04 + 006_u05**
3. staging schema 验证 + 端到端 smoke test
4. 触发 deploy-staging.yml 部署应用
5. 重复 2-4 在 production

---

## 4. 决策摘要
