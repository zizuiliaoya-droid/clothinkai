# U18 业务规则（AI 决策建议）

> 单元：U18（EP11-S01、S02、S03）（P3 实验功能）
> 核心：优雅降级（AI 不可用不阻塞页面）+ 留痕 + prompt 脱敏

---

## 1. AI 推广策略建议（EP11-S01）

- **BR-U18-01** strategy_advice(time_range)：先校验历史数据充足（推广数据 ≥ 6 个月）；不足 → AiDataInsufficientError（返回提示「历史数据不足，无法生成策略建议」，不调 AI 节省成本）。
- **BR-U18-02** 数据准备：聚合 ProductionService（投产 ROI 摘要）+ WorkProgressService（工作进度摘要）为脱敏聚合指标（不含成本价等敏感字段）。
- **BR-U18-03** prompt 构造：系统角色 + 聚合数据 + 请求"给出推广策略建议，含数据依据和置信度"。
- **BR-U18-04** 返回 advice_text + confidence（从响应解析或默认 medium）+ data_basis（数据依据摘要）。

---

## 2. AI 异常原因分析（EP11-S02）

- **BR-U18-20** anomaly_diagnosis(alert_id)：读 wecom_alert_log（U15）detail（异常类型/当前值/阈值）+ 关联款式投产快照。
- **BR-U18-21** alert_id 不存在或跨租户 → 404（RLS + 显式校验）。
- **BR-U18-22** prompt 构造多维度归因（款式/投放/退货/转化角度）→ DeepSeek → 返回归因分析文本。

---

## 3. AI 博主选择建议（EP11-S03）

- **BR-U18-40** blogger_suggest(style_id, top_n=5)：style_id 不存在 → 404。
- **BR-U18-41** 候选博主：从 blogger 库（U03）按 category_tags / quality_tags 与款式匹配筛选候选（规则预筛，限候选数避免 prompt 过长）。
- **BR-U18-42** prompt 构造：款式信息 + 候选博主摘要 → DeepSeek → 返回 Top N 建议（match_score 排序 + reason）。
- **BR-U18-43** 无候选博主 → 返回空列表（不报错）。

---

## 4. 优雅降级（核心 AC，所有端点）

- **BR-U18-60** AI 不可用（连接失败/超时/限流/非 200）→ DeepSeekClient 抛 AiServiceUnavailableError(503)。
- **BR-U18-61** API 捕获 → 返回 503 + 提示「AI 服务暂时不可用，请稍后重试」，**不阻塞页面**（前端可正常展示其余内容）。
- **BR-U18-62** 超时短（settings.DEEPSEEK_TIMEOUT 默认 30s），避免长时间挂起。
- **BR-U18-63** 降级/失败均落 ai_advice_log（status=degraded/failed），便于审计 + 成本追踪。

---

## 5. 留痕与安全

- **BR-U18-80** 每次请求落 ai_advice_log（成功 success / AI 不可用 degraded / 其他错误 failed）。
- **BR-U18-81** request_payload 仅存脱敏聚合摘要（不含成本价/采购价等敏感字段）。
- **BR-U18-82** DEEPSEEK_API_KEY 仅环境变量；不回显、不入日志（log 仅记 advice_type/status/latency）。
- **BR-U18-83** ai_advice_log 多租户 RLS + 显式 tenant。

---

## 6. 权限

- **BR-U18-90** ai.advice:read → pr（博主建议）+ pr_manager（策略）+ operations（异常）+ admin 通配。migration 022 seed。
- **BR-U18-91** AI 调用以当前用户上下文（created_by 记录发起人）。

---

## 7. 可观测

- **BR-U18-95** 指标 ai_advice_total{advice_type, status}（success/degraded/failed）。
- **BR-U18-96** Celery 任务占位（ai_advisory_request）P3 不强制；同步 API 为主。

---

## 8. 错误码矩阵

| 场景 | 异常 | HTTP |
|---|---|---|
| 历史数据不足（策略） | AiDataInsufficientError | 422（含提示） |
| alert_id 不存在（异常） | ResourceNotFoundError | 404 |
| style_id 不存在（博主） | ResourceNotFoundError | 404 |
| AI 服务不可用 | AiServiceUnavailableError | 503（不阻塞页面） |
| 无权限 | require_permission | 403 |
