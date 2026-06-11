# U16 部署架构（拍单 / 刷单 / 余额）

> 拓扑无变更；唯一部署动作 = migration 020 + 代码发布。
> 单元：EP06-S09、S10、S11（V2）。

---

## 1. 部署拓扑（无变更）

```
[财务] ──POST /api/finance/order-adjustments/brushing──> backend → order_adjustment（刷单）
[财务] ──POST /api/finance/balance-records──────────────> backend → balance_record
[U04 审核通过] ──SettlementRequested event──> [backend 在线事务内]
       ├─ U05 on_settlement_requested → settlement
       └─ U16 on_settlement_requested_auto_order → order_adjustment（拍单，best-effort）
[投产报表] GET /api/reports/production?exclude_brushing=true（默认）
       → ProductionRepository.aggregate_by_style 减去刷单金额
```

无 Celery / Beat / 外部出站。

---

## 2. 部署 checklist

1. [ ] 合并 U16 代码（modules/finance 6 新建 + 11 横切 + migration 020 + 3 测试）
2. [ ] CI 通过（lint + 单测 + 集成 + 覆盖率门 ≥70%）
3. [ ] 运行 migration 020（migrate.yml job，prod/staging）→ head=020
4. [ ] 部署 backend（order_adjustment_router + finance auto-order listener + ROI 口径升级）
5. [ ] 验证 /metrics 暴露 order_adjustment_auto_created_total
6. [ ] 通知报表使用方：投产 ROI 默认口径升级为"真实 ROI"（剔除刷单）

---

## 3. 验证步骤（部署后）

1. GET /api/openapi.json 含 `/api/finance/order-adjustments/brushing` 与 `/api/finance/balance-records`
2. 未登录调上述端点 → 401
3. 财务 POST brushing（amount="100-30"）→ 200，amount=70，exclude_from_roi=true
4. 非法金额（"100-30-5" / "abc"）→ 422
5. promotion.in_store_order=true → 审核通过 → order_adjustment(拍单) 自动生成；二次审核（事件重放）→ 幂等不重复
6. promotion.in_store_order=false → 审核通过 → 不生成拍单
7. 财务 POST balance-record（充值 income=1000）→ balance_after 自动；expected_balance 不一致 → 422；充值填 expense → 422
8. 投产报表含刷单数据：exclude_brushing=true（默认）pay 减刷单；=false 含刷单
9. 多租户：tenant A 余额/拍单不影响 tenant B；RLS 隔离
10. migration 回滚演练（staging）：down 020 → 升回 020，无数据破坏
11. 自动拍单失败（构造异常）→ settlement 仍创建成功（best-effort 不阻塞）

---

## 4. 监控

| 指标 | 关注 |
|---|---|
| order_adjustment_auto_created_total{result} | created/skipped/failed 分布（拍单自动生成健康度） |
| report_query_duration_seconds{report="production"} | ROI 隔离后投产查询耗时（U14 复用） |
| U05 审计日志 | 拍单/刷单/余额录入留痕 |

---

## 5. 回滚

- 代码回滚：撤销 order_adjustment_router + auto-order subscribe + production_service 默认 exclude_brushing=false（ROI 口径回退）。
- DB 回滚：migration 020 down（drop 2 表 + drop column in_store_order + 删 4 scope），无回填、无外键被引用，安全幂等。
- 口径回退：临时可经 API query exclude_brushing=false 看旧口径，无需下线。

---

## 6. 一致性

- 与 infrastructure-design.md 一致（无新服务 + migration 020 + 口径升级）。
- 与 U01/U05 部署架构一致（复用 Zeabur 6 服务 + 事件 + 审计）。
- V2 第 1 单元；部署后 V2 进度 1/2（剩 U17）。
