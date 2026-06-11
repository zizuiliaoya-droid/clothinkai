# U10a 基础设施设计计划（Infrastructure Design Plan）

> 单元：U10a — 设计制版全流程
> 范围：migration 013（4 表 + design.* scope seed）；R2 公私桶复用；零新服务/依赖/环境变量
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档（infrastructure-design.md + deployment-architecture.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增 Zeabur 服务
- [Answer] **零新增服务**：复用 backend（FastAPI）；无 Celery 任务（通知同步同事务，无异步）。

### Q2 — 数据库变更
- [Answer] migration 013（接 012）：创建 4 表（style_fabric / style_pattern / style_craft / design_workflow_log），均 tenant_id + RLS；前 3 表 UNIQUE(style_id)（1:1）；4 表 FK(style_id) ondelete CASCADE；design_workflow_log idx(tenant_id, style_id, created_at)。+ design.* scope seed 绑角色（幂等）。

### Q3 — R2 桶
- [Answer] **零新增桶**：设计稿复用 public 桶（U02 main_image_key 规约）；版型文件复用 private 桶（U05 签名 URL 模式）。

### Q4 — 环境变量 / Secrets
- [Answer] **零新增**：复用既有 R2/DB/Redis 配置。

### Q5 — Celery / 队列
- [Answer] **无**：状态推进通知同步写 DB（同事务），不走 Celery。

### Q6 — 部署顺序
- [Answer] 代码 + migration 013 同批；migrate.yml `alembic upgrade head` 执行 013；4 表为全新空表，无回填；scope seed 幂等。

### Q7 — 回滚
- [Answer] migration 013 downgrade 删 4 表（CASCADE）+ 删 design.* 细分 scope（连带 role_permission 引用按 FK）；代码回滚移除 design_router；style.design_status 既有数据不受影响（字段未改）。

### Q8 — CI/CD / 监控
- [Answer] 复用 ci.yml（design 测试纳入 pytest）+ migrate.yml；无新 workflow；无新自定义指标；structlog 状态转移日志计入既有。

---

## 2. 执行步骤

- [x] 2.1 `U10a/infrastructure-design/infrastructure-design.md`：基础设施增量总览（仅 migration 013）+ 4 表 DDL 概要 + RLS + scope seed 清单 + R2 桶复用 + 复用清单 + 部署/回滚
- [x] 2.2 `U10a/infrastructure-design/deployment-architecture.md`：部署拓扑无变更 + checklist（代码 + migration 013）+ 验证步骤（4 表存在 + 状态机端到端 + 通知 + 自动核价）+ 回滚 + 本地 Docker（下一空闲端口 5552/6407）
- [x] 2.3 诊断器无警告（infrastructure-design.md spec-format 假阳性 IGNORE）+ 与 nfr-design 一致

---

**等待用户"继续"；本轮直接生成 2 份基础设施设计文档。**
