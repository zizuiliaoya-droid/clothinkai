# U18 业务逻辑模型（AI 决策建议）

> 单元：U18（EP11-S01、S02、S03）（P3 实验功能，项目收官单元）
> 3 个核心用例 + 优雅降级流 + 跨单元契约（U14/U15/U03）

---

## UC-1：AI 推广策略建议（EP11-S01）

```
[PR 主管]
  POST /api/ai/strategy-advice  (require ai.advice:read)
    payload: preset/date_from/date_to
    AiAdvisoryService.strategy_advice(time_range)：
      1. 校验历史数据充足（推广 ≥6 个月）；不足 → AiDataInsufficientError(422)  （BR-U18-01）
      2. 数据准备：ProductionService.get_report(tr) + WorkProgressService 摘要（脱敏聚合）
      3. prompt = 系统角色 + 聚合数据 + "给出策略建议含数据依据和置信度"
      4. try: result = DeepSeekClient.chat(messages)            （BR-U18-03）
              advice = parse(result.content)  # text + confidence + basis
              log(status=success, latency, model)
              return AiAdviceResponse(advice_text, confidence, data_basis)
         except AiServiceUnavailableError:                      （BR-U18-60/61）
              log(status=degraded)
              raise → API 返回 503 + 提示（不阻塞页面）
```

---

## UC-2：AI 异常原因分析（EP11-S02）

```
[运营点击"AI 分析"]
  POST /api/ai/anomaly-diagnosis  (require ai.advice:read)
    payload: alert_id
    AiAdvisoryService.anomaly_diagnosis(alert_id)：
      1. alert = wecom_alert_log[alert_id]（U15）；不存在/跨租户 → 404  （BR-U18-21）
      2. 数据准备：alert.detail(类型/值/阈值) + 关联款式投产快照
      3. prompt = 多维度归因（款式/投放/退货/转化）
      4. DeepSeekClient.chat → 归因分析文本 → log + 返回
         AI 不可用 → 503 降级（log degraded）
```

---

## UC-3：AI 博主选择建议（EP11-S03）

```
[PR 选款后]
  POST /api/ai/blogger-suggest  (require ai.advice:read)
    payload: style_id / top_n=5
    AiAdvisoryService.blogger_suggest(style_id, top_n)：
      1. style = StyleRepo[style_id]；不存在 → 404           （BR-U18-40）
      2. 候选博主：blogger 库按 category/quality_tags 匹配预筛（限候选数）  （BR-U18-41）
         无候选 → 返回空列表（不报错）                        （BR-U18-43）
      3. prompt = 款式信息 + 候选博主摘要 + "Top N 排序 + 理由"
      4. DeepSeekClient.chat → 解析 → [BloggerSuggestion(blogger_id, match_score, reason)]
         → log + 返回；AI 不可用 → 503 降级
```

---

## 优雅降级流（所有端点核心）

```
DeepSeekClient.chat(messages):
  t0 = now()
  try:
    resp = httpx.post(DEEPSEEK_API_BASE/chat/completions,
                      headers={Authorization: Bearer ***}, json={...},
                      timeout=DEEPSEEK_TIMEOUT)  # 默认 30s
    if resp.status_code != 200: raise AiServiceUnavailableError
    return ChatResult(content, model, latency_ms=now()-t0)
  except (httpx.TimeoutException, httpx.HTTPError, 非200):
    raise AiServiceUnavailableError(503)          # BR-U18-60

API 层：
  except AiServiceUnavailableError:
    ai_advice_total.labels(type, "degraded").inc()
    → 503 {"detail": "AI 服务暂时不可用，请稍后重试"}   # 不阻塞页面 BR-U18-61
```

---

## 4. 跨单元契约

| 来源单元 | 契约 | U18 用法 |
|---|---|---|
| U14 report | ProductionService.get_report / WorkProgressService | 策略建议数据准备 |
| U15 wecom | wecom_alert_log（异常预警留痕） | 异常归因数据源 |
| U03/U11 blogger | Blogger 库 + category/quality_tags | 博主候选筛选 |
| U02 product | Style 模型 | 博主建议 style 校验 |
| U01 core | TenantScopedModel / AuditService / require_permission / httpx | RLS + 留痕 + 权限 + 外部调用 |
| 外部 | DeepSeek /chat/completions（OpenAI 兼容） | DeepSeekClient |

---

## 5. 故事覆盖

| 故事 | 覆盖 |
|---|---|
| EP11-S01 AI 推广策略建议 | UC-1（数据充足校验 + 聚合 + DeepSeek + 503 降级） |
| EP11-S02 AI 异常原因分析 | UC-2（wecom_alert_log + 多维度归因） |
| EP11-S03 AI 博主选择建议 | UC-3（候选筛选 + Top N 排序 + 理由） |

---

## 6. 一致性

- DeepSeek 集成走 Client 层封装（与企微 WecomClient 同模式）。
- 优雅降级是 P3 核心验收（AI 不可用不阻塞）；ai_advice_log 全程留痕。
- 数据复用 U14/U15/U03 既有 service，不重复实现聚合。
- 独立实验功能，不阻塞 MVP/V1/V2（DeepSeek 缺失全程 503 降级）。
