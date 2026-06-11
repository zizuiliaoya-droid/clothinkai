# U16 NFR 需求（拍单 / 刷单 / 余额）

> 增量式：复用 U01 NFR 基线 + U05 finance NFR（事件/审计/RLS）+ U14 投产报表 SLA。本文仅列 U16 特异 NFR。
> 单元：EP06-S09、S10、S11（V2）

---

## 1. 性能

| 项 | 指标 | 说明 |
|---|---|---|
| 刷单录入 create_brushing | P95 ≤ 200ms | 单行 insert + 金额解析 |
| 余额录入 add_record | P95 ≤ 200ms | 1 次 last_balance 查询 + insert |
| 拍单/余额 list | P95 ≤ 300ms | 分页 + idx |
| 拍单自动生成 | 同事务增量 < 10ms | SettlementRequested 内 1 读 + 1 insert |
| 投产报表（ROI 隔离） | ≤ 800ms（U14 口径） | +1 order_adjustment 子查询命中 idx_order_adjustment_roi |

- 拍单自动生成在 review approve 事务内同步执行；增量极小，best-effort 失败不阻塞。
- ROI 隔离子查询无刷单数据时返回空，开销可忽略。

---

## 2. 安全

### 2.1 金额表达式解析（防注入）
- parse_amount_expr **不使用 eval**；仅支持严格格式：纯数字 或 "数字-数字"（单个减号）。
- 正则校验 + Decimal 解析；多运算符 / 非数字 / 负结果 → AmountExpressionInvalidError(422)。

### 2.2 多租户隔离
- order_adjustment / balance_record 继承 TenantScopedModel + RLS。
- last_balance / ROI 聚合显式 WHERE tenant_id（测试 bypass 角色 RLS OFF 防御）。
- 自动拍单 listener 在事件 session 内执行（已带 tenant 上下文）。

### 2.3 权限
- finance.order:read/write（拍单/刷单）+ finance.balance:read/write（余额）→ finance + admin。
- 付款截图复用 U05 attachment 校验（V2 简化）。

### 2.4 威胁模型
| 威胁 | 缓解 |
|---|---|
| 金额表达式注入 | 不 eval + 正则 + Decimal |
| 跨租户读写 | RLS + 显式 WHERE tenant_id + scope |
| 余额篡改 | balance_after 落库 + 审计 + 流水追加不可改 |
| 重复自动拍单 | UNIQUE(tenant,promotion_id) partial 幂等 |

---

## 3. 可靠性与一致性

- **自动拍单幂等**：UNIQUE(tenant,promotion_id) partial + SELECT 查重；并发 IntegrityError catch → 视为已创建。
- **best-effort**：自动拍单失败 catch + log + 指标，不冒泡（不阻塞 settlement 创建）。
- **余额并发**：V2 量级低（财务单人录入），add_record 不加行锁；存在 race window（两笔同读上一笔）。文档标注后续可加 SELECT FOR UPDATE / advisory lock；expected_balance 校验提供人工兜底。
- **ROI 默认口径升级**：exclude_brushing 默认 true（V2 真实 ROI）；无刷单数据剔除 0，U14 既有测试不破坏。

---

## 4. 多租户与数据迁移

- migration 020：order_adjustment + balance_record 2 表（RLS + idx + CHECK）+ promotion.in_store_order ALTER（DEFAULT false 无回填）+ finance.order/balance scope seed。
- down 安全：drop 2 表 + drop column in_store_order + 删 scope。

---

## 5. 可观测性

| 指标 | 类型 | labels | 用途 |
|---|---|---|---|
| order_adjustment_auto_created_total | Counter | result(created/skipped/failed) | 拍单自动生成结果 |

- 其余复用 U05 审计 + U01 metrics（prometheus /metrics）。
- 自动拍单失败 → log warning（best-effort，不 Sentry capture 以免噪声，可选）。

---

## 6. 测试矩阵

| 层 | 文件 | 覆盖 |
|---|---|---|
| unit | test_order_amount_balance.py | parse_amount_expr（"100-30"→70 / 纯数字 / 多减号 422 / 负结果 422）+ balance 计算（首笔/累加）+ 类型字段匹配逻辑 |
| integration | test_order_adjustment.py | 拍单自动生成 listener（in_store_order=true 创建 + 幂等二次跳过 + false 不创建）+ create_brushing exclude_from_roi + ROI 隔离投产剔除端到端 + 余额 add_record 计算/expected 不一致 422/类型错配 422 + RLS |
| api | test_order_adjustment_api.py | /api/finance/order-adjustments/brushing + /api/finance/balance-records 401 + OpenAPI 路径 |

- 覆盖率门 ≥70%（全量回归）。

---

## 7. 一致性校验

- 与 functional-design business-rules BR-U16-01~71 引用一致。
- 性能/安全口径复用 U05 finance + U01 基线，无重复。
- ROI 隔离接入 U14 ProductionService，不独立实现聚合。
