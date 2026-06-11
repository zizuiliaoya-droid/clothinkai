# U02 基础设施设计计划（Infrastructure Design Plan）

> 单元：U02 — 商品 / SKU 基础  
> 阶段：MVP 第 2 个单元  
> 范围：U02 特异性基础设施增量；通用基础设施全部继承 `aidlc-docs/construction/U01/infrastructure-design/` + `aidlc-docs/construction/shared-infrastructure.md`

---

## 1. 单元上下文

### 1.1 与 U01 基础设施的关系

U01 已建立完整 6 服务部署 + PostgreSQL + Redis + Cloudflare R2 4 桶 + Sentry + GitHub Actions 4 workflows + 域名 + DNS + TLS 证书。

**U02 不新增任何基础设施服务、外部 API 集成、域名、密钥管理通道**。U02 在已有基础设施上的增量仅限：
- PostgreSQL：启用 `pg_trgm` 扩展 + 4 张新表 + 12 个索引（含 1 个 GIN trgm 索引）+ RLS 策略 4 条
- Cloudflare R2：使用 `public` 桶下 `{tenant_id}/styles/{style_id}/` 子路径（路径规约已在 shared-infrastructure §8 预定）
- 无新 Celery 队列 / Redis DB / Sentry 项目 / 域名 / 证书

### 1.2 输入文档
- `U02/functional-design/domain-entities.md`（4 ORM 实体 + 索引清单）
- `U02/nfr-requirements/tech-stack-decisions.md`（pg_trgm + migration 通道）
- `U02/nfr-design/nfr-design-patterns.md` + `logical-components.md`（25 新组件 + 17 复用 U01 组件）
- `U01/infrastructure-design/infrastructure-design.md`（基线）
- `U01/infrastructure-design/deployment-architecture.md`（部署细节）
- `aidlc-docs/construction/shared-infrastructure.md`（共享基础设施扩展协议）

### 1.3 输出文档
- `U02/infrastructure-design/infrastructure-design.md`（增量 + 引用 U01 基线）
- `U02/infrastructure-design/deployment-architecture.md`（migrate.yml 执行步骤 + 部署流程）

---

## 2. 计划步骤

### Step 1 — 分析设计文档
- [x] 1.1 读取 U02 4 份设计文档（functional + NFR）
- [x] 1.2 读取 U01 infrastructure-design + deployment-architecture 基线
- [x] 1.3 读取 shared-infrastructure §8 R2 路径规约 + §9 GitHub Actions

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U02 基础设施增量边界
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 infrastructure-design.md
- [x] 3.1 资源清单（继承 + 新增）
- [x] 3.2 PostgreSQL 增量（pg_trgm 扩展 + 4 表 + 12 索引 + 4 RLS 策略）
- [x] 3.3 R2 路径增量（public 桶 styles/ 子路径）
- [x] 3.4 环境变量增量（无）
- [x] 3.5 Sentry tag 增量（module=product）
- [x] 3.6 自定义 Prometheus 指标声明（指向 U02 NFR Design）
- [x] 3.7 与共享基础设施（shared-infrastructure.md）的对齐

### Step 4 — 生成 deployment-architecture.md
- [x] 4.1 部署服务变更（无新增服务，仅版本升级）
- [x] 4.2 alembic migration 004_u02 执行步骤（migrate.yml workflow_dispatch）
- [x] 4.3 staging / prod 双环境部署清单
- [x] 4.4 回滚步骤
- [x] 4.5 验证清单（健康检查 + 业务冒烟）

### Step 5 — 提交完成消息 + 等待审批
- [x] 5.1 展示 "🏢 Infrastructure Design Complete - U02"
- [x] 5.2 等待用户 P1/P2 反馈或批准
- [x] 5.3 批准后写入 audit.md

---

## 3. 澄清问题（请填 [Answer]）

> 由于 U02 基础设施几乎全部继承 U01，仅 7 个核心问题需要确认。每问预填合理默认值，作答即代表确认。

### 3.1 PostgreSQL 扩展

**Q1**：`pg_trgm` 扩展启用方式？

- [ ] **A. alembic migration 内 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`**（应用层负责）
- [ ] **B. Zeabur DB 控制台手动启用，应用不管**
- [ ] **C. 部署文档列为前置条件，DBA 启用**

[Answer]: A — alembic migration `004_u02_create_product_tables.py` 第一步执行 `CREATE EXTENSION IF NOT EXISTS pg_trgm;`；
- 启用扩展需要 PostgreSQL superuser 或 `CREATEROLE` 权限；Zeabur 默认 `clothing_app` 角色可能无此权限
- 解决方案：migration 通过 `clothing_bypass` 角色执行（已在 U01 配置过 alembic env.py 用 BYPASS_DATABASE_URL），该角色拥有创建扩展权限
- 如果生产 PG 实例不允许应用创建扩展（更严格的托管策略），降级方案：deployment-architecture.md 列出"前置 DBA 操作"，由 SRE 在 migrate.yml 执行前手动 `psql ... -c "CREATE EXTENSION pg_trgm;"`，并在 migration 中加 `IF NOT EXISTS` 防重复
- Zeabur PostgreSQL 16 实例默认允许 `CREATE EXTENSION pg_trgm`（PG 内置扩展，trusted），首选方案 A

### 3.2 R2 路径规约确认

**Q2**：U02 使用 R2 哪些路径？

> 复习：shared-infrastructure §8 已预留 `public` 桶 `{tenant_id}/styles/{style_id}/{filename}`；本问题确认使用范围。

[Answer]: 使用 `clothing-erp-public` 桶下两类路径：
- `{tenant_id}/styles/{style_id}/main/{filename}` — 款式主图（main_image_id 引用）
- `{tenant_id}/styles/{style_id}/details/{sort_order}/{filename}` — 款式详情图（StyleDetailImage 引用）

staging 环境前缀 `staging/`：
- `staging/{tenant_id}/styles/{style_id}/main/{filename}`
- `staging/{tenant_id}/styles/{style_id}/details/{sort_order}/{filename}`

不使用 `private` / `credentials` / `backups` 桶（U02 无敏感附件，cost_price 等字段在 DB）

### 3.3 环境变量

**Q3**：U02 是否需要新增环境变量？

[Answer]: 不需要。所有依赖（DATABASE_URL / REDIS_URL / R2_* / SENTRY_DSN / JWT_SECRET 等）已由 U01 配置。U02 业务规则常量在代码中（`PRICE_VISIBLE_ROLES` 等），不通过环境变量管理（避免运行时被错改）。

### 3.4 数据库连接池

**Q4**：U02 是否需要调整 backend 服务的 DB 连接池配置？

[Answer]: 不调整。U01 已配置 `pool_size=20, max_overflow=10`（每实例 30 连接），单 backend 实例 + Celery Worker 总连接数预估：
- backend：peak 200 QPS / 平均请求 50ms → 平均并发 10 连接，余裕充足
- celery-worker：1 实例 × 16 prefetch × ~ 70% 利用 ≈ 11 连接
- celery-beat：1 连接
- 合计 ≤ 22 连接，远低于 PostgreSQL `max_connections=100`（Zeabur 默认）

U02 模糊查询 + GIN 索引扫描比 U01 普通 select 略慢，但 P95 ≤ 300ms 在安全范围；如果未来发现连接池打满（`SQLAlchemy QueuePool limit reached`），扩容 backend 实例数即可。

### 3.5 Sentry 项目和 tag

**Q5**：U02 是否需要新建 Sentry 项目？

[Answer]: 不新建。复用 U01 已建的 `clothing-erp-backend` 和 `clothing-erp-frontend` 项目；U02 通过 `tag.module=product` 区分，前端通过 `transaction_name` 自动区分（如 `/styles`, `/skus/by-style/...`）。

### 3.6 部署服务变更

**Q6**：U02 上线时是否需要修改 Zeabur 服务定义（CPU/内存/replicas/healthcheck）？

[Answer]: 不修改。U02 不引入新依赖、不显著增加资源占用：
- 6 个服务（frontend/backend/celery-worker/celery-beat/postgres/redis）资源配置不变
- 健康检查 `/health` + `/ready` 不变
- replicas 不变（backend/frontend 保持 1，celery-beat 保持 1）

如果未来发现内存/CPU 紧张，由 SRE 调整资源（不在 U02 范围）。

### 3.7 部署顺序

**Q7**：U02 上线步骤 + 回滚预案？

[Answer]:

**部署顺序（与 U01 Q11=B 一致）**：

```
1. 代码 PR 合并到 main 分支
2. 手动触发 .github/workflows/migrate.yml（workflow_dispatch + environment=staging）
   - 拉最新 alembic/versions/004_u02_*.py
   - 执行 alembic upgrade head（创建 brand/style/sku/style_detail_image 4 表 + 12 索引 + RLS）
3. 验证 staging migration 成功
   - 连接 staging DB 检查表结构
   - 执行 SELECT 1 FROM style WHERE 1=0; （空查询不报错即 OK）
4. 触发 deploy-staging.yml（自动按 staging 分支触发）
   - 部署 backend/frontend/celery-worker/celery-beat 应用
5. staging 业务冒烟测试
   - 创建 brand → 创建 style → 创建 sku → match 接口
   - 多租户隔离回归
6. 重复 2-5 的 production 流程（migrate.yml + deploy-prod.yml）
```

**回滚预案**：

| 失败场景 | 回滚动作 |
|---|---|
| migrate.yml 失败（schema error） | alembic 自动 downgrade -1 → 应用层不部署 |
| deploy 失败（应用启动失败） | Zeabur 回滚到上一镜像 → DB schema 仍是新的（兼容性：U01 应用不引用 product 表） |
| 业务冒烟失败（match 慢） | hotfix PR 调整 SQL → 重走流程；不回滚 schema（产品表已建，无破坏性） |
| 数据腐败（不应发生） | `alembic downgrade -1` + 从 R2 backups 恢复（极端情况） |

**验证清单（migrate 后）**：
- [ ] `\dt` 显示 4 张新表
- [ ] `\d style` 显示 5 个索引（含 idx_style_search_trgm）
- [ ] `SELECT * FROM pg_extension WHERE extname='pg_trgm';` 返回 1 行
- [ ] `\d+ style` 显示 RLS 已 enabled
- [ ] 新 backend pod 健康检查通过（curl /ready 200）
- [ ] Prometheus `/metrics` 暴露 `style_search_results_count` 等新指标

---

## 4. 决策摘要（用户填答后由 AI 整理）

> 用户回复"继续"后，AI 总结 [Answer] 形成最终基础设施决策，作为 infrastructure-design.md / deployment-architecture.md 的输入。
