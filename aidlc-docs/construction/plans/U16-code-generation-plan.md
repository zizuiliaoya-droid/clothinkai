# U16 代码生成计划（Code Generation Plan）

> 单元：U16 — 拍单 / 刷单 / 余额（EP06-S09、S10、S11）（V2）
> 分批：**2 批** + Build & Test
> Build & Test：Docker PG16:5559 + Redis7:6414 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 复用 modules/finance，追加 order_adjustment_models/schemas/repository/service + balance_service + api 6 文件 + 横切 11 改动。
- [Answer] migration 020：order_adjustment + balance_record 2 表 + promotion.in_store_order ALTER + finance.order/balance scope seed。
- [Answer] S09 拍单自动生成订阅 SettlementRequested 多 handler best-effort 幂等；S10 create_brushing + parse_amount_expr 正则不 eval + ROI 隔离 aggregate_by_style 减刷单；S11 add_record 自动余额 + 校验。

---

## 1. 步骤（2 批）

### Batch 1 — 模型 + Schema + 枚举 + 异常 + 权限 + promotion ALTER + 指标 + repository
- [x] 1.1 order_adjustment_models.py（OrderAdjustment + BalanceRecord ORM）
- [x] 1.2 order_adjustment_schemas.py（4 schema）
- [x] 1.3 enums +OrderType/OrderAdjustmentStatus/BalanceRecordType / exceptions +3 / permissions +4 scope
- [x] 1.4 promotion/models.py +in_store_order / core/metrics +order_adjustment_auto_created_total
- [x] 1.5 order_adjustment_repository.py（OrderAdjustmentRepository + BalanceRecordRepository）

### Batch 2 — Service + Listener + Deps + API + ROI 接入 + main + migration + conftest + 测试
- [x] 2.1 order_adjustment_service.py（parse_amount_expr + auto_create_from_promotion + create_brushing）
- [x] 2.2 balance_service.py（add_record 计算 + 校验）
- [x] 2.3 listeners.py +on_settlement_requested_auto_order（多 handler）
- [x] 2.4 deps.py +2 ServiceDep / order_adjustment_api.py（4 端点）
- [x] 2.5 ROI 接入：advanced_repository.aggregate_by_style +exclude_brushing / production_service 默认 true / advanced_api query 默认 true / style_roi 移除占位
- [x] 2.6 main.py 挂 router + migration 020 + conftest import
- [x] 2.7 测试 3 文件（unit/integration/api）

### Build & Test
- [x] B.1 Docker PG16:5559 + Redis7:6414；alembic upgrade head（含 020）；U16 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 2 批 + Build & Test。**
