# U03 基础设施设计计划（Infrastructure Design Plan）

> 单元：U03 — 博主库基础  
> 范围：U03 特异性基础设施增量；通用基础设施全部继承 U01 + U02 + shared-infrastructure

---

## 1. 单元上下文

### 1.1 与 U01 / U02 基础设施的关系

U01 已建立 6 服务部署 + PG + Redis + R2 4 桶 + Sentry + GitHub Actions 4 workflows。
U02 已启用 pg_trgm 扩展。

**U03 增量极小化**：
- 不新增 Zeabur 服务、域名、证书、Secrets、环境变量
- PostgreSQL：1 张新表（blogger）+ 10 个索引（含 GIN trgm + GIN JSONB）+ 1 条 RLS 策略
- R2：不使用（U03 暂无附件功能；后续 U04+ 启用博主头像时再加）
- Sentry：新增 `module=blogger` tag
- Prometheus：新增 1 个自定义指标（`blogger_search_results_count`）

### 1.2 输出文档
- `U03/infrastructure-design/infrastructure-design.md`
- `U03/infrastructure-design/deployment-architecture.md`

---

## 2. 计划步骤

### Step 1 — 分析设计文档
- [x] 1.1 读取 U03 7 份设计文档（functional + NFR Requirements + NFR Design）
- [x] 1.2 与 U02 部署模板对齐

### Step 2 — 创建本计划
- [x] 2.1 列出 U03 增量
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 infrastructure-design.md
- [x] 3.1 资源清单（继承 + 增量）
- [x] 3.2 PostgreSQL 增量（1 表 + 10 索引 + 1 RLS）
- [x] 3.3 Sentry / Prometheus tag / 指标
- [x] 3.4 与 shared-infrastructure 对齐

### Step 4 — 生成 deployment-architecture.md
- [x] 4.1 Migration 005 完整代码
- [x] 4.2 三阶段部署流程
- [x] 4.3 验证清单 + 回滚预案

### Step 5 — 完成消息 + 等待审批
- [x] 5.1 展示 "🏢 Infrastructure Design Complete - U03"
- [x] 5.2 等待用户审批

---

## 3. 澄清问题（请填 [Answer]）

> U03 仅 3 个核心问题（其他全部沿用 U02 决策）。

### 3.1 R2 桶使用

**Q1**：U03 是否使用 R2 桶（如博主头像）？

[Answer]: **不使用**。U03 阶段 Blogger 表不含附件字段；博主头像如有需要，在 U04（推广合作）或 V1 时由 PR 上传到 `public` 桶 `{tenant_id}/bloggers/{blogger_id}/avatar/{filename}` 路径再加。U03 完全不使用 R2。

### 3.2 PostgreSQL 角色

**Q2**：U03 是否需要新增 PostgreSQL 角色？

[Answer]: 不需要。沿用 U01 已建立的三个角色：
- `clothing_app`（业务连接 + RLS）
- `clothing_bypass`（migration / 系统任务）
- `clothing_archiver`（audit_log 归档）

### 3.3 Migration 通道

**Q3**：U03 migration 执行方式？

[Answer]: 与 U02 完全一致 — 通过 `.github/workflows/migrate.yml`（U01 已建立的专用 job），先 staging 验证后再 production。

详细步骤：
1. PR 合并到 main 分支
2. 手动触发 `migrate.yml`（env=staging）→ 执行 alembic upgrade 005_u03
3. 验证 staging schema
4. 自动触发 deploy-staging.yml 部署应用
5. staging 业务冒烟测试通过
6. 重复 1-5 在 production

---

## 4. 决策摘要（用户填答后由 AI 整理）
