# U18 部署架构（AI 决策建议）

> 拓扑无变更；部署动作 = migration 022 + DEEPSEEK_* 配置 + 代码发布。
> 单元：EP11-S01、S02、S03（P3 项目收官单元）。

---

## 1. 部署拓扑（无变更）

```
[PR 主管] ──POST /api/ai/strategy-advice────> backend → AiAdvisoryService → ProductionService(U14)
[运营]   ──POST /api/ai/anomaly-diagnosis──> backend → wecom_alert_log(U15)
[PR]    ──POST /api/ai/blogger-suggest────> backend → blogger 库(U03)
                                              └─ DeepSeekClient ──HTTPS──> api.deepseek.com
                                              └─ ai_advice_log（留痕）
AI 不可用/未配置 → 503 降级（不阻塞页面）
```

无 Celery / Beat；唯一外部出站 = DeepSeek HTTPS。

---

## 2. 部署 checklist

1. [ ] 合并 U18 代码（modules/ai 11 新建 + 横切 5 + migration 022 + 3 测试）
2. [ ] CI 通过（lint + 单测 + 集成 + 覆盖率门 ≥70%；DeepSeek monkeypatch）
3. [ ] 运行 migration 022（migrate.yml job，prod/staging）→ head=022
4. [ ] 配置 DEEPSEEK_API_KEY（Zeabur Secrets）+ DEEPSEEK_API_BASE/MODEL/TIMEOUT（可选默认）
5. [ ] 部署 backend（ai_router）
6. [ ] 验证 /metrics 暴露 ai_advice_total + ai_advice_latency_seconds
7. [ ] 验证未配置 API_KEY 时 AI 端点返回 503（降级，不报 500）

---

## 3. 验证步骤（部署后）

1. GET /api/openapi.json 含 `/api/ai/strategy-advice`、`/api/ai/anomaly-diagnosis`、`/api/ai/blogger-suggest`
2. 未登录调上述端点 → 401
3. 无 ai.advice 权限 → 403
4. 配置有效 API_KEY：PR 主管 POST strategy-advice（数据 ≥6 月）→ 返回建议文本 + 置信度；ai_advice_log success
5. 历史数据不足 → 422 提示（不调 AI）
6. anomaly-diagnosis 不存在 alert_id → 404；存在 → 归因文本
7. blogger-suggest 不存在 style_id → 404；存在 → Top N 建议
8. **降级**：移除/置空 DEEPSEEK_API_KEY 或模拟超时 → 503 + 提示，不阻塞页面；ai_advice_log degraded
9. 多租户：tenant A ai_advice_log 不含 tenant B；RLS 隔离
10. migration 回滚演练（staging）：down 022 → 升回 022，无数据破坏
11. 其余模块在 DeepSeek 不可用时正常（仅 AI 端点 503）

---

## 4. 监控

| 指标 | 关注 |
|---|---|
| ai_advice_total{advice_type,status} | degraded/failed 占比（AI 可用性）；success 用量 |
| ai_advice_latency_seconds | AI 调用耗时 P95 |
| ai_advice_log（latency_ms/status） | 成本/用量追踪 |
| Sentry | 非降级类异常（500 级 bug）capture；降级 503 不 capture |

---

## 5. 回滚 + 密钥安全

- 代码回滚：撤销 ai_router 挂载。
- DB 回滚：migration 022 down（drop ai_advice_log + 删 scope），安全幂等。
- 配置：DEEPSEEK_API_KEY 留空即全 503 降级（快速停用 AI 而不下线代码）。
- 密钥：API_KEY 存 Zeabur Secrets，不入仓库/日志；轮换经 Secrets 更新。

---

## 6. 一致性

- 与 infrastructure-design.md 一致（无新服务 + migration 022 + DeepSeek 出站）。
- 与 U01/U07 部署架构一致（复用 Zeabur 6 服务 + Secrets + Sentry + HTTPS 出站）。
- **P3 项目收官单元**：部署后全部 23 个 sub-unit（MVP 12 + V1 8 + V2 2 + P3 1）交付完成。
