# U18 NFR 需求（AI 决策建议）

> 增量式：复用 U01 NFR 基线 + U07 Client 模式（httpx/超时/错误）+ U14/U15 数据。本文仅列 U18 特异 NFR。
> 单元：EP11-S01、S02、S03（P3 实验功能）

---

## 1. 性能

| 项 | 指标 | 说明 |
|---|---|---|
| AI 调用 | 无严格在线 SLA | 受外部 DeepSeek 延迟主导；超时上限 DEEPSEEK_TIMEOUT=30s（可配） |
| 数据准备 | ≤ 1s | ProductionService/WorkProgressService 聚合（复用 U14 SLA） |
| 端点整体 | 受 AI 响应主导 | 前端异步加载，不阻塞主页面 |

- 超时即降级 503（不长时间挂起）。
- 数据不足（策略 <6 月）不调 AI（节省成本 + 快速返回）。

---

## 2. 可靠性与优雅降级（核心 AC）

- **降级全捕获**：DeepSeekClient catch（httpx.TimeoutException / HTTPError / 非 200 / JSON 解析失败）→ AiServiceUnavailableError(503)。
- **API 层统一**：捕获 AiServiceUnavailableError → 返回 503 + 提示「AI 服务暂时不可用」，不抛 500、不阻塞页面。
- **未配置即不可用**：DEEPSEEK_API_KEY 缺失 → 视为不可用 503（不报配置错误）。
- **留痕**：降级/失败均落 ai_advice_log（status=degraded/failed）+ 指标。
- **独立性**：DeepSeek 全程不可用时，系统其余模块正常（仅 AI 端点 503）；模块缺失不影响 main 启动。

---

## 3. 安全

### 3.1 威胁模型
| 威胁 | 缓解 |
|---|---|
| API key 泄露 | 仅环境变量（Zeabur Secrets）；不回显、不入日志 |
| 敏感数据外泄给 AI | prompt 仅聚合指标脱敏（无成本价/采购价/行级敏感数据） |
| 跨租户读 ai_advice_log | RLS + 显式 WHERE tenant_id |
| 越权调用 | require_permission ai.advice:read → 403 |
| 外部出站 | 仅 DeepSeek 域名 HTTPS |

### 3.2 权限
- ai.advice:read（pr / pr_manager / operations + admin 通配）。

---

## 4. 成本控制

- 数据不足不调 AI（策略 <6 月 → 422 提示）。
- 候选博主限数量（避免 prompt 过长 → token 成本）。
- 超时短（30s）；ai_advice_log 记 latency_ms + status 便于用量/成本追踪。
- 无缓存（P3 实验；可后续加 Redis 缓存相同请求）。

---

## 5. 多租户与迁移

- ai_advice_log 继承 TenantScopedModel + RLS。
- wecom_alert_log（U15）/ report（U14）数据读取显式 tenant；created_by 记发起人。
- migration 022：ai_advice_log 1 表（RLS + idx）+ ai.advice scope seed。down 安全 drop 表 + 删 scope；无回填。

---

## 6. 可观测性

| 指标 | 类型 | labels | 用途 |
|---|---|---|---|
| ai_advice_total | Counter | advice_type, status(success/degraded/failed) | AI 请求结果分布 |
| ai_advice_latency_seconds | Histogram | — | AI 调用耗时 |

- Sentry：非降级类异常（500 级，如解析 bug）capture；降级（503）不 capture（预期行为）。

---

## 7. 测试矩阵（AI mock）

| 层 | 文件 | 覆盖 |
|---|---|---|
| unit | test_ai_advisory.py | prompt 构造 + 响应解析 + 数据充足校验纯逻辑 |
| integration | test_ai_advice.py | monkeypatch DeepSeekClient.chat 成功→success log / 抛 AiServiceUnavailableError→degraded log / 数据不足 422 / alert 404 / style 404 / RLS |
| api | test_ai_api.py | 3 端点 401 + OpenAPI + 503 降级契约（monkeypatch 不可用） |

- 全程 monkeypatch DeepSeekClient，不调真实 DeepSeek。
- 覆盖率门 ≥70%（全量回归）。

---

## 8. 依赖

- 零新第三方依赖：复用 httpx（U07）调 DeepSeek /chat/completions（OpenAI 兼容）。
- 新增配置 DEEPSEEK_API_BASE / API_KEY / MODEL / TIMEOUT（环境变量）。

---

## 9. 一致性校验

- 与 functional-design business-rules BR-U18-01~96 引用一致。
- Client 降级模式与 U07 WecomClient 一致。
- 数据复用 U14/U15/U03，不重复实现。
