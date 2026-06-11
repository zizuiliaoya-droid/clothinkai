# U18 NFR 需求计划（NFR Requirements Plan）

> 单元：U18 — AI 决策建议（EP11-S01、S02、S03）（P3 实验功能）
> 增量式：复用 U01 NFR 基线 + U07 Client 模式（httpx/Redis 缓存可选）+ U14/U15 数据
> 依赖：U14（report）、U15（wecom_alert_log）、U03（blogger）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：AI API 性能 SLA？
[Answer] AI 调用受外部 DeepSeek 延迟主导，无严格在线 SLA；超时上限 DEEPSEEK_TIMEOUT=30s（可配）；超时即降级 503。数据准备（报表聚合）≤1s（复用 U14 SLA）。整体端点 P95 受 AI 响应主导，前端异步加载不阻塞主页面。

### Q2：依赖与 Client 选型？
[Answer] 复用 httpx（U07 已装）调 DeepSeek /chat/completions（OpenAI 兼容）；无需新 SDK。零新第三方依赖。配置 DEEPSEEK_API_BASE/API_KEY/MODEL/TIMEOUT（环境变量）。

### Q3：优雅降级可靠性（核心）？
[Answer] DeepSeekClient catch（httpx.TimeoutException/HTTPError/非 200/JSON 解析失败）→ AiServiceUnavailableError(503)；API 层统一捕获返回 503 + 提示，不抛 500 不阻塞页面。降级/失败均落 ai_advice_log + 指标。DeepSeek 未配置（无 API_KEY）→ 视为不可用 503。

### Q4：安全 / 威胁模型？
[Answer] API key 仅环境变量（Zeabur Secrets），不回显不入日志；prompt 脱敏（聚合指标，无成本价/采购价）；ai_advice_log RLS + 显式 tenant；外部出站仅 DeepSeek 域名 HTTPS；不发送原始敏感行级数据。

### Q5：成本控制？
[Answer] 数据不足（策略 <6 月）不调 AI；候选博主限数量避免 prompt 过长；超时短；ai_advice_log 记 latency + status 便于成本/用量追踪。无缓存（P3 实验；可后续加 Redis 缓存相同请求）。

### Q6：可观测指标？
[Answer] ai_advice_total{advice_type, status}（success/degraded/failed）；ai_advice_latency_seconds Histogram（AI 调用耗时）。Sentry capture 非降级类异常（500）。

### Q7：迁移与回滚？
[Answer] migration 022：ai_advice_log 1 表（RLS + idx）+ ai.advice scope seed。down 安全 drop 表 + 删 scope。无回填。

### Q8：多租户？
[Answer] ai_advice_log 继承 TenantScopedModel + RLS；wecom_alert_log/report 数据读取显式 tenant；created_by 记发起人。

### Q9：测试矩阵（含 AI mock）？
[Answer] 测试 3 文件：unit（prompt 构造 + 响应解析 + 数据充足校验纯逻辑）+ integration（monkeypatch DeepSeekClient.chat 成功路径落 success log / 抛 AiServiceUnavailableError 降级落 degraded log / 数据不足 422 / alert 404 / RLS）+ api（3 端点 401 + OpenAPI + 503 降级契约 monkeypatch）。不调真实 DeepSeek。

### Q10：独立性（P3）？
[Answer] U18 独立实验功能，不阻塞前序；DeepSeek 全程不可用时系统其余模块正常（仅 AI 端点 503）。模块缺失/未注册不影响 main 启动。

---

## 1. 步骤

- [x] 1.1 阅读 U18 functional-design 3 文档 + U07 WecomClient 模式（httpx/超时/错误）+ U14/U15 数据接口 + U01 NFR 基线
- [x] 1.2 编写 nfr-requirements.md（性能：AI 受外部主导无 SLA+超时 30s 降级；可靠性：降级全捕获不阻塞+未配置视为不可用；安全：key 环境变量+prompt 脱敏+威胁模型；成本；2 指标+Sentry；migration 022；测试矩阵 mock）
- [x] 1.3 编写 tech-stack-decisions.md（零新依赖复用 httpx/report/blogger；modules/ai 11 新建 + 5 横切落点；DeepSeekClient 降级片段；DEEPSEEK_* 配置；2 指标；migration 022 片段；测试 3 文件 mock 模式）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。nfr-requirements.md 的 spec-format 假阳性 IGNORE。**
