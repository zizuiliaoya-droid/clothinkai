# U18 功能设计计划（Functional Design Plan）

> 单元：U18 — AI 决策建议（EP11-S01、S02、S03）（P3 实验功能，项目收官单元）
> 依赖：U14（report 历史数据）+ U03/U11（blogger 选择）
> 新建：modules/ai（DeepSeekClient + AiAdvisoryService）；独立实验功能，不阻塞前序阶段

---

## 0. 澄清问题（[Answer] 预填）

### Q1：AI 模块落点与组件？
[Answer] 新建 modules/ai：DeepSeekClient（底层 HTTPS API 封装 + 超时 + 降级）+ AiAdvisoryService（数据准备 + prompt 构造 + 调用 + 留痕 + 降级）+ ai_models（AiAdviceLog）+ schemas/service/api/deps/enums/exceptions/permissions/config。migration 022：ai_advice_log 1 表 + ai.advice scope seed。

### Q2：DeepSeek 集成方式？
[Answer] DeepSeekClient 用 httpx.AsyncClient 调 DeepSeek Chat Completions API（OpenAI 兼容 /chat/completions）；API key/base/model/timeout 从 settings（环境变量）；返回内容文本 + usage。连接失败/超时/非 200 → AiServiceUnavailableError（503）。

### Q3：EP11-S01 策略建议数据来源？
[Answer] strategy_advice(time_range)：检查历史数据充足（≥6 个月推广数据，不足返回提示）；聚合 ProductionService(投产) + WorkProgressService(工作进度) 摘要 → 构造 prompt → DeepSeek → 返回 advice 文本 + 数据依据 + 置信度（从响应解析或默认 medium）。

### Q4：EP11-S02 异常归因数据来源？
[Answer] anomaly_diagnosis(alert_id)：读 wecom_alert_log（U15 异常预警）detail + 关联款式投产数据 → 构造多维度归因 prompt → DeepSeek → 返回归因分析文本。alert 不存在 404。

### Q5：EP11-S03 博主选择数据来源？
[Answer] blogger_suggest(style_id, top_n=5)：读 style + 候选 blogger（U03 库，按 quality_tags/category 匹配）→ 构造 prompt → DeepSeek → 返回 Top N 博主建议（match_score 排序 + 理由）。style 不存在 404。

### Q6：优雅降级（核心 AC）？
[Answer] AI 服务不可用（连接/超时/限流/非 200）→ DeepSeekClient 抛 AiServiceUnavailableError(503)；API 返回 503 + 提示信息「AI 服务暂时不可用」，**不阻塞页面**；ai_advice_log 记 status=degraded/failed 留痕。超时短（默认 30s，可配）。

### Q7：ai_advice_log 留痕？
[Answer] AiAdviceLog：advice_type(strategy/anomaly/blogger)/request_payload JSONB/response_text Text/confidence/status(success/degraded/failed)/model/latency_ms/created_by。每次请求落一条（成功/降级/失败均留痕）。

### Q8：同步还是异步？
[Answer] V1 同步 API（POST 返回建议 + 短超时 + 503 降级）；services.md 的 ai_advisory_request Celery 任务作为可选占位（长文本/批量场景留扩展点），P3 不强制启用。

### Q9：权限 scope？
[Answer] ai.advice:read（策略→pr_manager / 异常→operations / 博主→pr；统一 scope 绑 pr/pr_manager/operations + admin 通配）。migration 022 seed。

### Q10：数据充足性与安全？
[Answer] strategy 数据不足（<6 个月）→ 返回提示不调 AI（节省成本）；prompt 不含敏感字段（成本价等脱敏，仅聚合指标）；API key 仅环境变量不回显不入日志；多租户 ai_advice_log RLS。

---

## 1. 步骤

- [x] 1.1 阅读 EP11-S01/S02/S03 GWT + 需求 2.9 + 已有 report/blogger service + wecom_alert_log（U15）+ DeepSeek API 形态
- [x] 1.2 编写 domain-entities.md（AiAdviceLog 1 表 + AdviceType/AdviceStatus 枚举 + DeepSeekClient I/O + 3 advice 数据准备口径 + ER）
- [x] 1.3 编写 business-rules.md（BR-U18-01~ 策略数据充足/异常归因/博主匹配/优雅降级 503/留痕/prompt 脱敏/权限/错误码）
- [x] 1.4 编写 business-logic-model.md（3 UC：策略建议 / 异常归因 / 博主选择 + 降级流 + 跨单元契约 U14/U15/U03）
- [x] 1.5 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.5（Plan + 3 文档，同一回合）。**
