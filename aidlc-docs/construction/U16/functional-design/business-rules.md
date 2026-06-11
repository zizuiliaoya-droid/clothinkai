# U16 业务规则（拍单 / 刷单 / 余额）

> 单元：U16（EP06-S09、S10、S11）（V2）
> 错误码沿用 core/exceptions；金额/余额校验在 service 层

---

## 1. 拍单自动生成（EP06-S09）

- **BR-U16-01** 触发：U16 订阅 U04 `SettlementRequested` 事件（审核通过时发出）。handler 读 promotion.in_store_order。
- **BR-U16-02** 仅当 `promotion.in_store_order = true` 才创建拍单；否则 no-op。
- **BR-U16-03** auto_create_from_promotion 自动填充：order_type=拍单、style_id/sku_id（来自 promotion）、blogger_identifier（博主小红书ID/昵称）、promotion_id、exclude_from_roi=false、status=待付款。
- **BR-U16-04** 幂等：UNIQUE(tenant_id, promotion_id) partial；重复触发（事件重放）→ SELECT 命中已存在则跳过（不报错）；并发 IntegrityError catch 视为已创建。
- **BR-U16-05** best-effort：handler 内 try/except 包裹，拍单创建失败 catch + log + 指标，**不阻塞 settlement 创建**（同事务但不冒泡）。
- **BR-U16-06** 拍单详情：通过 promotion_id / 内部编码可查询关联博主与商品信息（EP06-S09 第二条）。

---

## 2. 刷单录入与 ROI 隔离（EP06-S10）

- **BR-U16-20** create_brushing：order_type=刷单，exclude_from_roi 默认 true（财务可不传，系统置 true）。
- **BR-U16-21** 金额表达式解析：`amount` 支持"原价-返现"格式（如 "100-30" → 70）。规则：含单个 `-` → 拆为 (原价, 返现)，amount = 原价 - 返现；纯数字 → 直接用；结果须 ≥ 0；其他格式（多个运算符 / 非数字 / 负结果）→ AmountExpressionInvalidError(422)。
- **BR-U16-22** order_no 重复：仅 warning（开发文档"提示并要求确认"），不硬拒；service 返回 duplicate 标志，由前端确认（V2 后端不阻断）。
- **BR-U16-23** ROI 隔离：投产报表计算真实 ROI 时，排除 exclude_from_roi=true 的刷单订单的支付金额。
- **BR-U16-24** ROI 剔除实现：ProductionRepository.aggregate_by_style(exclude_brushing=true) → pay_amount 减去该款式期内 order_adjustment(order_type=刷单 AND exclude_from_roi=true) 的 SUM(amount)（style_id + order_date BETWEEN date_from/date_to）。
- **BR-U16-25** style_roi.exclude_brushing 占位移除：净投产比/退货率等基于剔除后金额计算。
- **BR-U16-26** ProductionService.get_report 默认 exclude_brushing=true（V2 真实 ROI）；调用方可传 false 看含刷单口径。
- **BR-U16-27** 刷单数据单独展示（报表中不混入正常统计）；exclude_from_roi=false 的刷单（特例）仍计入。

---

## 3. 余额核对（EP06-S11）

- **BR-U16-40** add_record 自动计算：balance_after = 上一笔 balance_after + COALESCE(income,0) - COALESCE(expense,0)；首笔上一笔余额=0。
- **BR-U16-41** "上一笔" = 同租户 ORDER BY created_at DESC LIMIT 1（显式 WHERE tenant_id；RLS 兜底）。
- **BR-U16-42** 一致性校验：payload 可带 expected_balance；若提供且 ≠ 计算 balance_after → BalanceMismatchError(422)，不保存（"标红报错"）。
- **BR-U16-43** 类型字段匹配：record_type=充值 → 仅允许 income（expense 必须为空）；record_type ∈ {推广支出, 刷拍单支出} → 仅允许 expense（income 必须为空）；错配 → BalanceTypeFieldMismatchError(422)。
- **BR-U16-44** income/expense 至少一个非空且 > 0；两者同时非空 → 422。
- **BR-U16-45** 流水追加：V2 仅新增 + list（不改不删）；list 按 created_at 排序返回含 balance_after。

---

## 4. 权限

- **BR-U16-60** finance.order:read/write（拍单/刷单）→ finance + admin；finance.balance:read/write（余额）→ finance + admin。migration 020 seed（admin 通配 + finance 显式）。
- **BR-U16-61** 自动拍单 listener 以系统/事件上下文运行（无用户），创建人留空或事件 actor。

---

## 5. 状态机

- **BR-U16-70** order_adjustment status：待付款（起点）→ 已付款（mark_paid 需 payment_amount + payment_date + payment_proof）。V2 基础口径，mark_paid 简化校验。
- **BR-U16-71** balance_record 无状态（流水）。

---

## 6. 错误码矩阵

| 场景 | 异常 | HTTP |
|---|---|---|
| 金额表达式非法 | AmountExpressionInvalidError | 422 |
| 余额计算与填写不一致 | BalanceMismatchError | 422 |
| 余额类型与字段错配 | BalanceTypeFieldMismatchError | 422 |
| income/expense 同时填或都空 | BalanceTypeFieldMismatchError | 422 |
| 自动拍单重复 | 幂等跳过 | 无（不报错） |
| order_no 重复 | warning 标志返回 | 200（需前端确认） |
| 权限不足 | require_permission | 403 |
