# U16 业务逻辑模型（拍单 / 刷单 / 余额）

> 单元：U16（EP06-S09、S10、S11）（V2）
> 3 个核心用例 + 跨单元契约（U04 事件 / U05 finance / U14 投产报表）

---

## UC-1：拍单自动生成（EP06-S09）

**角色**：财务（受益）；系统（事件驱动）
**前置**：promotion.in_store_order=true；审核通过触发 SettlementRequested

```
[U04 PR 主管审核通过 promotion]
  PromotionService.review(approve) 事务内：
    dispatch(SettlementRequested, session)   ← required_handler=True
       ├─ [U05] on_settlement_requested → 创建 settlement（强一致）
       └─ [U16] on_settlement_requested_auto_order（best-effort）
              promo = PromotionRepo.get_by_id(event.promotion_id)
              if not promo or not promo.in_store_order: return        （BR-U16-02）
              try:
                  OrderAdjustmentService.auto_create_from_promotion(promo)
                    - 幂等：UNIQUE(tenant,promotion_id) partial；已存在→跳过（BR-U16-04）
                    - 填充 order_type=拍单/style_id/sku_id/blogger_identifier/promotion_id
                              /exclude_from_roi=false/status=待付款
              except Exception:
                  log + 指标，不冒泡（不阻塞 settlement 创建 BR-U16-05）
  commit
```

**关键点**：复用现有 SettlementRequested 事件作为"审核通过"信号；多 handler 顺序执行；拍单失败不影响结算（try/except）。

---

## UC-2：刷单录入 + ROI 隔离（EP06-S10）

```
[财务录入刷单]
  POST /api/finance/order-adjustments/brushing  (require finance.order:write)
    payload: order_date / order_no? / style_id / sku_id? / amount("100-30") / blogger_identifier?
    OrderAdjustmentService.create_brushing：
      1. amount = parse_amount_expr("100-30") → 70    （BR-U16-21；非法 422）
      2. order_type=刷单, exclude_from_roi=true（默认）
      3. order_no 重复 → 查询提示 duplicate=true（warning，不阻断 BR-U16-22）
      4. 落库 + 审计
    → OrderAdjustmentResponse（含 duplicate 标志）

[投产报表真实 ROI]
  GET /api/reports/production?exclude_brushing=true（默认）
    ProductionService.get_report(exclude_brushing=true)
      ProductionRepository.aggregate_by_style(exclude_brushing=true)：
        pay_amount := SUM(qianniu pay) - SUM(order_adjustment.amount
                        WHERE order_type=刷单 AND exclude_from_roi=true
                          AND style_id=s.id AND order_date BETWEEN ...)    （BR-U16-24）
      style_roi.net_roi / return_rate 基于剔除后金额（BR-U16-25）
```

**金额表达式解析**：
```
parse_amount_expr(s):
  s = s.strip()
  if '-' in s（单个，非首字符）: 原价, 返现 = split('-'); return Decimal(原价) - Decimal(返现)
  else: return Decimal(s)
  结果 < 0 或解析失败 → AmountExpressionInvalidError(422)
```

---

## UC-3：余额录入 + 校验（EP06-S11）

```
[财务录入余额流水]
  POST /api/finance/balance-records  (require finance.balance:write)
    payload: record_date / record_type / income? / expense? / expected_balance? / remark?
    BalanceService.add_record：
      1. 类型字段匹配（BR-U16-43/44）：
           充值 → income 必填 > 0, expense 空
           推广支出/刷拍单支出 → expense 必填 > 0, income 空
           错配 / 同填 / 都空 → BalanceTypeFieldMismatchError(422)
      2. prev = BalanceRepo.last_balance()（同租户 created_at DESC LIMIT 1，无则 0）
      3. balance_after = prev + COALESCE(income,0) - COALESCE(expense,0)   （BR-U16-40）
      4. if expected_balance is not None and expected_balance != balance_after:
           raise BalanceMismatchError(422)    （BR-U16-42 标红报错不保存）
      5. 落库 balance_after + 审计
    → BalanceRecordResponse（含 balance_after）

  GET /api/finance/balance-records?date_from&date_to  (require finance.balance:read)
    → list ORDER BY created_at（含 balance_after）
```

---

## 4. 跨单元契约

| 来源单元 | 契约 | U16 用法 |
|---|---|---|
| U04 promotion | `SettlementRequested` 事件 + promotion.in_store_order（U16 新增列） | 订阅事件触发自动拍单 |
| U04 promotion | PromotionRepository.get_by_id | 读 in_store_order + 博主/款式快照 |
| U05 finance | modules/finance 模块（settlement/付款语义）+ events 多 handler | 复用模块落点 + 同事务多 handler |
| U14 report | ProductionService / ProductionRepository.aggregate_by_style | 接入 exclude_brushing 剔除刷单 |
| U01 core | TenantScopedModel / AuditService / require_permission / events | RLS + 审计 + 权限 + 事件 |

---

## 5. 故事覆盖

| 故事 | 覆盖 |
|---|---|
| EP06-S09 拍单自动生成 | UC-1（SettlementRequested listener + in_store_order + 幂等自动填充） |
| EP06-S10 刷单录入与 ROI 隔离 | UC-2（create_brushing + 金额表达式 + exclude_brushing 投产剔除） |
| EP06-S11 余额核对 | UC-3（自动计算 + expected_balance 校验 + 类型字段匹配） |

---

## 6. 一致性

- 数据模型与开发文档 6.8/6.9/6.10 + order_adjustment 字段表一致。
- ROI 隔离口径与开发文档"刷单计算真实 ROI 时剔除"一致；接入 U14 ProductionService 不重复实现聚合。
- 复用 U05 finance 模块落点、U04 事件、U01 核心设施，无循环依赖（U16 → U05 → U04；U16 → U14 → U13/U05）。
