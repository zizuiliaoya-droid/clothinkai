# U10b 基础设施设计计划（Infrastructure Design Plan）

> 单元：U10b — 平台商品映射
> 范围：migration 014（1 表 + product.platform scope seed）；零新服务/依赖/桶/环境变量
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档，同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增 Zeabur 服务
- [Answer] **零**：复用 backend FastAPI。

### Q2 — 数据库变更
- [Answer] migration 014（接 013）：创建 platform_product 表（tenant_id + RLS + UNIQUE(tenant,platform,platform_id) + FK style RESTRICT / sku SET NULL + idx(tenant,style_id)）+ product.platform:read/write scope seed 绑角色（幂等）。

### Q3 — 环境变量 / Secrets / R2 / Celery / Redis
- [Answer] 全部**零新增**。

### Q4 — 部署顺序
- [Answer] 代码 + migration 014 同批；空表无回填；scope seed 幂等。

### Q5 — 回滚
- [Answer] migration 014 downgrade 删表 + 删 scope；代码回滚移除 platform_product_router；无数据迁移风险。

---

## 2. 执行步骤

- [x] 2.1 `U10b/infrastructure-design/infrastructure-design.md`
- [x] 2.2 `U10b/infrastructure-design/deployment-architecture.md`
- [x] 2.3 诊断器无警告（infrastructure-design.md spec-format 假阳性 IGNORE）

---

**等待用户"继续"；本轮直接生成 2 份文档。**
