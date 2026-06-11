# U08 基础设施设计计划（Infrastructure Design Plan）

> 单元：U08 — 发文进度看板
> 范围：**零基础设施增量**（纯读聚合层，复用 backend 服务；无新表/migration/服务/环境变量/Celery）
> 节奏：Infrastructure Design 阶段 = 本计划 + 2 文档（infrastructure-design.md + deployment-architecture.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 是否新增 Zeabur 服务
- [Answer] 否。4 个 GET 端点挂在现有 backend 服务（api 子域），复用既有部署。

### Q2 — 是否新增环境变量 / Secret
- [Answer] 否。无外部调用、无凭据、无配置项（阈值硬编码 MVP）。

### Q3 — 是否新增 migration
- [Answer] 否。无新表；聚合 promotion（U04）+ style（U02）现有表 + 现有索引。

### Q4 — CI/CD 影响
- [Answer] U08 测试纳入既有 pytest job（聚合正确性 + TimeRange + null + 多租户，全部用现有测试 DB，无外部依赖）；无新 CI job。

### Q5 — 性能/索引运维
- [Answer] MVP 实时聚合复用 U04 索引；若生产 cards GROUP BY style_id 慢，评估补 `idx_promotion_tenant_coop_style`（记入文档，作为可选优化，不在 U08 强制）。

---

## 2. 执行步骤

- [x] 2.1 `U08/infrastructure-design/infrastructure-design.md`：零增量声明 + 复用 backend 服务 + 聚合查询负载特征 + 索引复用 + 可选索引优化（不强制）+ 一致性校验（spec-format 假阳性 IGNORE）
- [x] 2.2 `U08/infrastructure-design/deployment-architecture.md`：部署即随 backend 镜像（无独立步骤）+ 无 migration/环境变量改动 + 回滚 = 代码回滚 + 监控（HTTP 时延 instrumentator）
- [x] 2.3 诊断器（infrastructure-design.md spec-format 假阳性 IGNORE）+ 与 nfr-design 一致

---

**等待用户"继续"；本轮直接生成 2 份基础设施设计文档。**
