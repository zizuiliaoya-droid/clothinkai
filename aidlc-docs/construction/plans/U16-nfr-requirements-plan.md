# U16 NFR 需求计划（NFR Requirements Plan）

> 单元：U16 — 拍单 / 刷单 / 余额（EP06-S09、S10、S11）（V2）
> 增量式：复用 U01 NFR 基线 + U05 finance NFR（事件/审计/RLS）+ U14 投产报表 SLA
> 依赖：U05（finance + SettlementRequested 事件）、U14（ProductionService）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：拍单/刷单/余额 API 性能 SLA？
[Answer] 写入（create_brushing / add_record / 拍单查询）P95 ≤ 200ms（单行 + 1 次 last_balance 查询）；list 分页 P95 ≤ 300ms。复用 U01/U05 写 SLA，无特异高负载。

### Q2：拍单自动生成性能与事务影响？
[Answer] auto_create_from_promotion 在 SettlementRequested 同事务内同步执行（1 次 promotion 读 + 1 次 insert）；增量 < 10ms，不显著拖慢 review approve。best-effort try/except 保证失败不阻塞主流程。

### Q3：ROI 隔离对投产报表性能影响？
[Answer] aggregate_by_style 增加 1 个 order_adjustment 子查询（按 style_id + order_date 范围 SUM，命中 idx_order_adjustment_roi）；投产报表 SLA 仍 ≤800ms（U14 口径）。无刷单数据时子查询返回空，开销可忽略。

### Q4：金额表达式解析安全？
[Answer] parse_amount_expr 仅支持"数字"或"数字-数字"格式（单个减号），用 Decimal 解析 + 严格正则校验；**不使用 eval**（防注入）；非法/多运算符/负结果 → 422。

### Q5：余额并发安全？
[Answer] add_record 读"上一笔"（created_at DESC LIMIT 1）+ 计算 + insert。并发录入存在 race（两笔同时读到同一上一笔）；V2 量级低（财务单人录入），不加行锁；文档标注若需强一致可加 advisory lock / SELECT FOR UPDATE（后续增强）。expected_balance 校验提供人工兜底。

### Q6：威胁模型 / 多租户？
[Answer] order_adjustment / balance_record RLS + 显式 WHERE tenant_id（聚合/last_balance）；finance.order/balance scope 限财务+admin；金额表达式不 eval；付款截图复用 U05 attachment 校验（V2 简化）。

### Q7：可观测指标？
[Answer] 复用 U05 审计 + U01 metrics。新增可选指标：order_adjustment_auto_created_total{result}（拍单自动生成 created/skipped/failed）。其余复用现有。

### Q8：迁移与回滚？
[Answer] migration 020：order_adjustment + balance_record 2 表（RLS + idx + CHECK）+ promotion.in_store_order ALTER（DEFAULT false 无回填）+ finance.order/balance scope seed。down 安全 drop 2 表 + drop column + 删 scope。

### Q9：exclude_brushing 默认改 true 的兼容性？
[Answer] ProductionService.get_report 默认 exclude_brushing=true（V2 起）；API query 默认 true。无刷单数据时剔除 0，U14 既有测试结果不变（已验证：U14 测试无 order_adjustment 数据）。文档标注口径升级"真实 ROI"。

### Q10：测试矩阵？
[Answer] 测试 3 文件：unit（parse_amount_expr 表达式解析 + balance 计算/校验纯逻辑）+ integration（拍单自动生成 listener 幂等 + 刷单录入 + ROI 隔离剔除端到端 + 余额计算/不一致/类型错配 + RLS）+ api（order-adjustments/brushing + balance-records 401 + OpenAPI）。

---

## 1. 步骤

- [x] 1.1 阅读 U16 functional-design 3 文档 + U05 finance NFR（事件/审计/RLS）+ U14 ProductionService SLA + U01 NFR 基线
- [x] 1.2 编写 nfr-requirements.md（性能 SLA + 自动拍单同事务增量 + ROI 子查询性能 + 金额解析不 eval + 余额并发说明 + 多租户 + 1 指标 + migration 020 + 测试矩阵）
- [x] 1.3 编写 tech-stack-decisions.md（零新依赖复用 finance/events/ProductionService/Decimal；modules/finance 6 新建 + 11 横切落点；parse_amount_expr 正则；order_adjustment_auto_created_total 指标；migration 020 片段；测试 3 文件）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。nfr-requirements.md 的 spec-format 假阳性 IGNORE。**
