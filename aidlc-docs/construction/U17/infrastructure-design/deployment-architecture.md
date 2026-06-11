# U17 部署架构（套装 + BI 看板 + 报表导出）

> 拓扑无变更；唯一部署动作 = migration 021 + 代码发布。
> 单元：EP02-S08、EP09-S06、EP09-S08（V2 收官单元）。

---

## 1. 部署拓扑（无变更）

```
[跟单] ──POST /api/bundles──────────────────> backend → bundle_product + bundle_item
[运营] ──GET /api/reports/bi────────────────> backend → BiService → Production/StoreDaily(U14)
[运营] ──PUT /api/reports/bi/layout─────────> backend → user_preference upsert
[PR主管/运营] ──GET /api/reports/{type}/export──> backend → openpyxl 流式 xlsx StreamingResponse
```

无 Celery / Beat / 外部出站；导出为同步 HTTP 流式响应。

---

## 2. 部署 checklist

1. [ ] 合并 U17 代码（product 5 + report 6 新建 + 横切 7 + migration 021 + 3 测试）
2. [ ] CI 通过（lint + 单测 + 集成 + 覆盖率门 ≥70%）
3. [ ] 运行 migration 021（migrate.yml job，prod/staging）→ head=021
4. [ ] 部署 backend（bundle_router + bi_router + export_router）
5. [ ] 验证 /metrics 暴露 report_export_total
6. [ ] 验证导出端点返回 xlsx（Content-Type + Content-Disposition）

---

## 3. 验证步骤（部署后）

1. GET /api/openapi.json 含 `/api/bundles`、`/api/reports/bi`、`/api/reports/bi/layout`、`/api/reports/{report_type}/export`
2. 未登录调上述端点 → 401
3. 跟单 POST bundle（items=[A×1, B×1]）→ 201；重复 bundle_code → 409
4. bundle item 含跨租户 sku → 422；quantity=0 → 422
5. split_quantities(bundle, 3) → 各 sku 按数量拆分
6. 运营 GET /api/reports/bi → cards + charts(line/bar/pie)
7. PUT /api/reports/bi/layout → user_preference 落库；GET → 回显（无则默认布局）
8. 有 report.export:read 权限 GET production/export → xlsx 流（openpyxl 可解析）；无权限 → 403
9. 非法 report_type → 400
10. 多租户：tenant A bundle/偏好不影响 tenant B；RLS 隔离
11. migration 回滚演练（staging）：down 021 → 升回 021，无数据破坏

---

## 4. 监控

| 指标 | 关注 |
|---|---|
| report_export_total{report_type,result} | success/forbidden/invalid 分布（导出健康度） |
| report_query_duration_seconds | BI 聚合复用 report service 耗时（U14 复用） |
| 审计日志 | bundle 创建留痕 |

---

## 5. 回滚

- 代码回滚：撤销 bundle_router/bi_router/export_router 挂载。
- DB 回滚：migration 021 down（drop 3 表 + 删 4 scope），无回填、无外键被引用，安全幂等。

---

## 6. 一致性

- 与 infrastructure-design.md 一致（无新服务 + migration 021 + 流式响应）。
- 与 U01/U02/U14 部署架构一致（复用 Zeabur 6 服务 + RLS + 审计 + openpyxl）。
- **V2 收官单元**：部署后 V2 全部 2/2 交付完成；项目仅剩 P3（U18 AI 决策建议）。
