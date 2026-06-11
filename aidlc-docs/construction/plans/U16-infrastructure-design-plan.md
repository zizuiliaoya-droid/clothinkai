# U16 基础设施设计计划（Infrastructure Design Plan）

> 单元：U16 — 拍单 / 刷单 / 余额（EP06-S09、S10、S11）（V2）
> 产出：infrastructure-design.md + deployment-architecture.md
> 增量式：复用 U01/U05 基础设施（Zeabur 6 服务 + RLS + 审计 + 事件总线）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：是否新增 Zeabur 服务 / 计算资源？
[Answer] 无新服务/进程。复用 backend（拍单/刷单/余额 API）。无 Celery 任务/Beat（U16 同步 + 事件驱动，SettlementRequested 在 review approve 在线事务内）。

### Q2：数据库变更？
[Answer] migration 020：order_adjustment + balance_record 2 表（RLS + idx + CHECK + UNIQUE promotion_id partial）+ promotion.in_store_order ALTER（DEFAULT false 无回填）+ finance.order/balance:read/write scope seed（finance 显式 + admin 通配）。

### Q3：新增依赖 / 环境变量 / R2 / Redis？
[Answer] 全部零新增。金额解析用标准库 re/Decimal；事件总线 core/events 已有；ROI 隔离复用 U14 report 聚合。无 R2/Redis 新用量。

### Q4：部署一致性约束？
[Answer] U16 依赖 U05（finance 模块 + SettlementRequested 事件 + listeners.register）+ U14（report ProductionService）已部署。U16 在 finance.listeners.register() 内追加 subscribe，与 U05 同事件多 handler；U05 先部署即有该事件，无逆向风险。migration 020 紧接 019。promotion.in_store_order ALTER 兼容旧数据（DEFAULT false）。

### Q5：ROI 口径升级部署影响？
[Answer] exclude_brushing 默认改 true 后投产报表口径升级"真实 ROI"；部署即生效。无刷单数据时与 V1 一致（剔除 0）。前端/调用方需知晓口径变化（文档标注）；可传 exclude_brushing=false 看旧口径对比。

### Q6：监控？
[Answer] 复用 U01 prometheus /metrics（order_adjustment_auto_created_total 自动暴露）+ U05 审计。无新监控基础设施。自动拍单失败 log warning（best-effort）。

### Q7：本地验证环境？
[Answer] Docker PG16:5559 + Redis7:6414 + Py3.12（U16 唯一端口）；alembic upgrade head 含 020；U16 子集 + 全量回归；覆盖率 ≥70%。

### Q8：回滚策略？
[Answer] 代码：移除 order_adjustment_router + finance auto-order subscribe + 恢复 production_service 默认 exclude_brushing=false（口径回退）。DB：migration 020 down（drop 2 表 + drop column in_store_order + 删 scope），无外键被引用，安全。

---

## 1. 步骤

- [x] 1.1 阅读 U01/U05 infrastructure-design（Zeabur 服务/事件/审计）+ U16 nfr-design logical-components（migration 020）
- [x] 1.2 编写 infrastructure-design.md（无新服务；migration 020 2 表 + promotion ALTER + scope seed；零新依赖/环境变量/R2/Redis；ROI 口径升级说明；部署一致性；本地 Docker 5559/6414）
- [x] 1.3 编写 deployment-architecture.md（拓扑无变更+部署 checklist+验证步骤+ROI 口径变更说明+监控+回滚）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。infrastructure-design.md 的 spec-format 假阳性 IGNORE。**
