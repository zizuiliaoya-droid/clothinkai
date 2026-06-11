# U16 功能设计计划（Functional Design Plan）

> 单元：U16 — 拍单 / 刷单 / 余额（EP06-S09、S10、S11）（V2）
> 依赖：U05（finance/settlement + SettlementRequested 事件）
> 复用：modules/finance + core/events + U14 ProductionService（启用 exclude_brushing）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：拍单/刷单/余额落在哪个模块、几张表？
[Answer] 复用 modules/finance，追加 order_adjustment_models（OrderAdjustment 拍单/刷单统一建模 + BalanceRecord 余额流水）+ schemas/repository/service/api。migration 020：order_adjustment + balance_record 2 新表 + promotion.in_store_order ALTER 字段 + finance scope seed。

### Q2：order_adjustment 统一建模字段？
[Answer] order_type(拍单/刷单)/order_date/order_no(可空)/blogger_identifier/style_id(FK)/sku_id(FK 可空)/amount/payment_amount/payment_date/payment_proof_attachment_id(FK)/exclude_from_roi(刷单默认 true)/status(待付款/已付款)/promotion_id(FK 可空，自动拍单来源)/remark。order_no 重复 = warning 不硬拒（开发文档）；自动拍单幂等用 UNIQUE(tenant,promotion_id) partial（promotion_id 非空）。

### Q3：EP06-S09 拍单自动生成触发时机？
[Answer] promotion 增加 in_store_order(店铺拍单) bool 字段。U16 订阅已有 `SettlementRequested` 事件（U04 审核通过时发出，含 promotion_id/blogger_id/style_id/pr_id）；handler 读 promotion.in_store_order，为 true 则 auto_create_from_promotion 创建拍单（order_type=拍单，自动填充博主/款式/sku，UNIQUE 幂等）。best-effort：handler 内 try/except 不阻塞 settlement 创建。

### Q4：EP06-S10 刷单录入 + 金额表达式解析？
[Answer] OrderAdjustmentService.create_brushing：order_type=刷单，exclude_from_roi 默认 true。金额支持"原价-返现"表达式（如"100-30"→70）：解析 `原价 - 返现`，单纯数字直接用；非法格式 422。也支持纯数字 amount。

### Q5：EP06-S10 ROI 隔离如何接入投产报表？
[Answer] 启用 services/metric/style_roi 的 exclude_brushing（移除占位）；ProductionRepository.aggregate_by_style 增加 exclude_brushing 参数：为 true 时从 pay_amount 减去该款式期内 order_adjustment(order_type=刷单 AND exclude_from_roi=true) 的 amount 合计（按 style_id + order_date 范围）。ProductionService.get_report 默认 exclude_brushing=true（V2 起真实 ROI）；API query 默认 true。无刷单数据时减 0，结果不变（U14 测试不破坏）。

### Q6：EP06-S11 余额核对计算 + 校验？
[Answer] BalanceRecord：record_date/record_type(充值/推广支出/刷拍单支出/其他)/income(可空)/expense(可空)/balance_after/remark/created_by。BalanceService.add_record：balance_after = 上一笔 balance_after + income - expense（按 created_at 顺序取最新一笔）；payload 可带 expected_balance，与计算值不一致 → 422 标红；类型与字段匹配（充值仅 income / 支出类仅 expense，错配 422）。

### Q7：余额"上一笔"如何确定？多租户？
[Answer] 上一笔 = 同租户按 created_at DESC LIMIT 1（显式 WHERE tenant_id）。首笔上一笔余额视为 0。balance_after 落库便于 O(1) 读 + 审计；list 按 created_at 排序。

### Q8：权限 scope？
[Answer] finance.order:read/write（拍单/刷单，财务 role）+ finance.balance:read/write（余额，财务 role）。migration 020 seed 绑 finance + admin 通配。

### Q9：状态机？
[Answer] order_adjustment status 简单 2 态（待付款/已付款），付款需 payment_amount+payment_date+payment_proof（复用 U05 付款语义，但不强制本单元做完整付款流，V2 基础口径：mark_paid 简化）。balance_record 无状态（流水追加 + 软不可改，V2 仅新增 + list）。

### Q10：错误码？
[Answer] 金额表达式非法 422 / 余额不一致 422 / 类型字段错配 422 / 自动拍单重复幂等跳过（不报错）/ 权限 403。

---

## 1. 步骤

- [x] 1.1 阅读 EP06-S09/S10/S11 GWT + 开发文档 6.8/6.9/6.10 + order_adjustment 字段表 + 已有 finance/settlement 模型 + U14 ProductionService
- [x] 1.2 编写 domain-entities.md（OrderAdjustment + BalanceRecord 2 表 + promotion.in_store_order + OrderType/BalanceRecordType 枚举 + 金额表达式 + ROI 隔离口径 + ER）
- [x] 1.3 编写 business-rules.md（BR-U16-01~ 拍单自动生成幂等/刷单 exclude_from_roi/金额表达式解析/ROI 隔离剔除/余额自动计算+校验+类型字段匹配/权限/错误码）
- [x] 1.4 编写 business-logic-model.md（3 UC：拍单自动生成 SettlementRequested listener / 刷单录入+ROI 隔离 / 余额录入校验 + 跨单元契约 U04/U05/U14）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
