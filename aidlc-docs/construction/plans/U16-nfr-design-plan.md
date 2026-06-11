# U16 NFR 设计计划（NFR Design Plan）

> 单元：U16 — 拍单 / 刷单 / 余额（EP06-S09、S10、S11）（V2）
> 产出：nfr-design-patterns.md（伪代码模式）+ logical-components.md（组件清单 + 依赖图）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：拍单自动生成 listener 模式？
[Answer] P-U16-01：on_settlement_requested_auto_order(event, session) → 读 promotion.in_store_order；false→return；true→OrderAdjustmentService.auto_create_from_promotion(promo)；幂等 get_by_promotion 查重 + IntegrityError catch；try/except 包裹不冒泡（best-effort）+ order_adjustment_auto_created_total{result}。register() 追加 subscribe("SettlementRequested")。完整伪代码 + 多 handler 顺序说明。

### Q2：auto_create_from_promotion 内部？
[Answer] P-U16-01 续：existing = repo.get_by_promotion(promo.id)；存在→return existing（skipped）；构建 OrderAdjustment(order_type=拍单/style_id/sku_id/blogger_identifier(博主快照)/promotion_id/exclude_from_roi=false/status=待付款)；add+flush；IntegrityError（并发）→ skipped。

### Q3：create_brushing + 金额解析？
[Answer] P-U16-02：parse_amount_expr 正则（不 eval）"数字|数字-数字"→Decimal；create_brushing：order_type=刷单/exclude_from_roi=true/amount=parse；order_no 非空时查重→duplicate 标志（warning 不阻断）；落库+审计+commit。完整伪代码 + 解析边界。

### Q4：余额 add_record？
[Answer] P-U16-03：类型字段匹配（充值仅 income/支出类仅 expense，错配 422）；last_balance（created_at DESC LIMIT 1 显式 tenant，无则 0）；balance_after=prev+income-expense；expected_balance 不一致 422；落库+审计+commit。完整伪代码。

### Q5：ROI 隔离聚合改造？
[Answer] P-U16-04：ProductionRepository.aggregate_by_style(exclude_brushing) → pay_amount 子查询减去 order_adjustment(刷单 AND exclude_from_roi=true，style_id+order_date BETWEEN) SUM(amount)；exclude_brushing=false 不减；style_roi.net_roi/return_rate 移除占位基于剔除后值；production_service 默认 true。完整 SQL 片段。

### Q6：logical-components 组件与依赖？
[Answer] modules/finance 新建 6 + 横切 11；依赖图：order_adjustment_api→Service→repo；listener→OrderAdjustmentService（事件驱动）；anomaly/production(U14)→aggregate_by_style(exclude_brushing)→order_adjustment 子查询；balance_service→repo.last_balance。无循环（U16→U05→U04；U16→U14→U13/U05）。

### Q7：repository 落点？
[Answer] 新建 order_adjustment_repository.py：OrderAdjustmentRepository（add/get_by_promotion/list/exists_order_no）+ BalanceRecordRepository（add/last_balance/list）。ROI 子查询在 report/advanced_repository 内联 SQL（不跨模块调用 finance repo）。

### Q8：测试设计映射？
[Answer] logical-components 末尾列 3 测试文件 → 组件/规则映射：unit(parse_amount_expr/_compute_balance/_validate_type_field)+integration(listener/create_brushing/ROI/balance)+api(401/OpenAPI)。

---

## 1. 步骤

- [x] 1.1 阅读 U16 functional-design + nfr-requirements + U05 finance listeners 模式 + U14 advanced_repository ROI 聚合
- [x] 1.2 编写 nfr-design-patterns.md（P-U16-01 自动拍单 listener+auto_create 幂等 / P-U16-02 create_brushing+parse_amount_expr / P-U16-03 balance add_record 计算+校验 / P-U16-04 ROI 隔离 aggregate_by_style 改造 完整伪代码）
- [x] 1.3 编写 logical-components.md（6 新建 + 11 横切 + repository + 依赖图无循环 + migration 020 DDL 概要 + 3 测试文件映射）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。**
