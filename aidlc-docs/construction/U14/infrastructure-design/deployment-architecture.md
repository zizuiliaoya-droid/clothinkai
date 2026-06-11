# U14 部署架构（Deployment Architecture）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表

---

## 1. 拓扑变更

**无变更**——U14 报表聚合运行在既有 backend；precompute 占位由既有 celery-worker/beat 承载（V1 不强制启用）。

```
[既有拓扑不变]
frontend → backend ── PostgreSQL（+target_planning + store_daily）
                   └─ 聚合 qianniu_daily/ad_daily/promotion/settlement
celery-worker/beat（precompute_report_cache 占位，V1 默认不启用）
```

---

## 2. 部署 Checklist

- [ ] 合并代码（modules/report 追加 9 文件 + services/metric 3 子模块 + 横切改动）到 main
- [ ] migrate job 执行 alembic upgrade head（含 018）
- [ ] backend 重启加载 advanced_api 路由（/api/reports/work-progress|targets|store-daily|production）
- [ ]（可选）启用 precompute：celery-worker `-Q ...,report` + 取消 Beat 注释
- [ ] 验证 /api/openapi.json 暴露 4 报表端点

---

## 3. 验证步骤（部署后）

| # | 验证项 | 期望 |
|---|---|---|
| 1 | 2 表存在 | target_planning / store_daily（含 UNIQUE + idx） |
| 2 | scope seed | report.target/store_daily/work_progress/production scope；pr_manager/operations 绑定 |
| 3 | RLS 生效 | 2 表 rowsecurity=true |
| 4 | 工作进度 | GET work-progress?month= 返回 per-PR KPI |
| 5 | 爆款约篇 | set_target + list 返回达标/缺口 |
| 6 | 店铺数据 | qianniu_daily 聚合 + store_daily 手动字段 |
| 7 | 投产报表 | 5 公式正确 + 周环比 previous 期 |
| 8 | 除零 | 分母 0 → null（前端 "—"） |
| 9 | 手动 upsert | PUT store-daily/{date} 更新手动字段 |
| 10 | 多租户隔离 | A 租户报表不含 B；target/store UNIQUE 跨租户独立 |
| 11 | exclude_brushing | 参数存在 V1 不影响结果 |

---

## 4. 监控

| 项 | 说明 |
|---|---|
| report_query_duration_seconds{report_type} | 4 类报表聚合耗时（监控慢查询，投产最重 ≤800ms） |
| Sentry | 聚合异常 capture |

---

## 5. 回滚

| 步骤 | 命令 |
|---|---|
| 1. DB 回滚 | alembic downgrade 017（DROP target_planning + store_daily + DELETE scope） |
| 2. 代码回滚 | Zeabur 切回上一版本 |
| 3.（如启用 precompute） | celery-worker -Q 移除 report + Beat 注释 |
| 风险 | 低——新表无下游依赖（U16/U17 尚未实施） |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| 拓扑无变更 | ✅ |
| Checklist + 11 验证步骤 | ✅ |
| 监控 report_query_duration + Sentry | ✅ |
| 回滚安全 | ✅ |
| 本地 Docker 5557/6412 | ✅ |
