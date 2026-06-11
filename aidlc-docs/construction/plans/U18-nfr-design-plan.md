# U18 NFR 设计计划（NFR Design Plan）

> 单元：U18 — AI 决策建议（EP11-S01、S02、S03）（P3 实验功能）
> 产出：nfr-design-patterns.md（伪代码模式）+ logical-components.md（组件清单 + 依赖图）

---

## 0. 澄清问题（[Answer] 预填）

### Q1：DeepSeekClient 降级模式？
[Answer] P-U18-01：chat(messages, model) → 未配置 key 即 raise AiServiceUnavailableError；try httpx.post（timeout）catch（TimeoutException/HTTPError/非200/JSON 解析失败）→ AiServiceUnavailableError；成功返回 {content, model, latency_ms}。完整伪代码 + build_ai_http_client。

### Q2：AiAdvisoryService 三方法 + 留痕 + 降级模式？
[Answer] P-U18-02：统一 _run(advice_type, payload, build_messages, parse) → 数据准备 → DeepSeekClient.chat → parse → 落 ai_advice_log(success) → 返回；except AiServiceUnavailableError → 落 log(degraded) + 指标 + 重新抛（API 转 503）。strategy_advice（数据充足校验）/anomaly_diagnosis（alert 404）/blogger_suggest（style 404 + 候选筛选）各自 build_messages + parse。完整伪代码。

### Q3：API 层降级契约？
[Answer] P-U18-03：api 3 端点 require_permission ai.advice:read；service 抛 AiServiceUnavailableError(503，AppException 自动映射)；AiDataInsufficientError(422)；404 由 service。无需 api 额外 try（AppException 全局 error handler 映射）。指标在 service 落。

### Q4：prompt 构造 + 解析？
[Answer] P-U18-02 续：build_messages 系统角色 + 脱敏聚合数据 JSON；parse 提取 content 文本 + confidence（启发式或默认 medium）；blogger parse 解析 Top N（JSON 或文本兜底）。prompt 不含敏感字段。

### Q5：logical-components 组件与依赖？
[Answer] modules/ai 11 新建 + 横切 5；依赖图：ai_api→AiAdvisoryService→DeepSeekClient(httpx) + ProductionService/WorkProgressService(U14) + wecom_alert_log(U15) + blogger repo(U03) + AiAdviceLogRepository。无循环（U18→U14/U15/U03→U13/U05→U01）。

### Q6：留痕落库时机？
[Answer] AiAdviceLog 在 service 内每次调用后落（success/degraded/failed）+ commit；降级时先 log 再抛。created_by 从 user。

### Q7：测试设计映射？
[Answer] logical-components 末尾列 3 测试文件 → 组件/规则映射：unit(parse/build_messages/数据充足)+integration(monkeypatch chat 成功/降级/数据不足/404/RLS)+api(401/OpenAPI/503)。

---

## 1. 步骤

- [x] 1.1 阅读 U18 functional-design + nfr-requirements + U07 WecomClient 降级模式 + U14/U15 数据接口
- [x] 1.2 编写 nfr-design-patterns.md（P-U18-01 DeepSeekClient 降级 / P-U18-02 AiAdvisoryService _run + 3 方法 + 留痕 + 降级 / P-U18-03 API 降级契约 完整伪代码）
- [x] 1.3 编写 logical-components.md（11 新建 + 5 横切 + repository + 依赖图无循环 + migration 022 DDL 概要 + 3 测试文件映射）
- [x] 1.4 一致性校验 + Plan 勾选 + state/audit 更新

---

**本轮执行 Step 1.1~1.4（Plan + 2 文档，同一回合）。**
