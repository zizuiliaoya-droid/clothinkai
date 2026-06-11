# U18 NFR 设计模式（AI 决策建议）

> 3 个设计模式伪代码：DeepSeekClient 降级 / AiAdvisoryService 统一编排 + 3 方法 / API 降级契约。
> 复用 U07 WecomClient 的 httpx + 超时 + 错误封装模式。

---

## P-U18-01：DeepSeekClient 降级（外部 API 封装）

```python
import time
import httpx
from app.core.config import settings
from app.modules.ai.exceptions import AiServiceUnavailableError


def build_ai_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.DEEPSEEK_TIMEOUT)


class DeepSeekClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def chat(self, messages: list[dict], *, model: str | None = None) -> dict:
        if not settings.DEEPSEEK_API_KEY:           # 未配置即不可用（BR-U18-60）
            raise AiServiceUnavailableError()
        t0 = time.monotonic()
        try:
            resp = await self._http.post(
                f"{settings.DEEPSEEK_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={"model": model or settings.DEEPSEEK_MODEL,
                      "messages": messages, "stream": False},
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise AiServiceUnavailableError() from exc
        if resp.status_code != 200:
            raise AiServiceUnavailableError()
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise AiServiceUnavailableError() from exc
        return {"content": content,
                "model": data.get("model", model or settings.DEEPSEEK_MODEL),
                "latency_ms": int((time.monotonic() - t0) * 1000)}
```
> API key 不入日志；超时/错误/非 200/解析失败全转 AiServiceUnavailableError（503）。

---

## P-U18-02：AiAdvisoryService 统一编排 + 3 方法 + 留痕 + 降级

```python
class AiAdvisoryService:
    def __init__(self, session) -> None:
        self._s = session
        self._repo = AiAdviceLogRepository(session)

    async def _run(self, advice_type: str, payload: dict, messages: list[dict],
                   user) -> dict:
        """统一：chat → 留痕 → 返回；降级 → 留痕 degraded → 抛（API 转 503）。"""
        http = build_ai_http_client()
        try:
            result = await DeepSeekClient(http).chat(messages)
        except AiServiceUnavailableError:
            await self._log(advice_type, payload, None, "degraded", None, None, user)
            ai_advice_total.labels(advice_type=advice_type, status="degraded").inc()
            raise                                       # API 转 503（BR-U18-61）
        finally:
            await http.aclose()
        ai_advice_latency_seconds.observe(result["latency_ms"] / 1000)
        await self._log(advice_type, payload, result["content"], "success",
                        result["model"], result["latency_ms"], user)
        ai_advice_total.labels(advice_type=advice_type, status="success").inc()
        return result

    async def _log(self, advice_type, payload, text, status, model, latency, user):
        self._repo.add(AiAdviceLog(
            tenant_id=user.tenant_id, advice_type=advice_type,
            request_payload=payload, response_text=text, status=status,
            model=model, latency_ms=latency, created_by=user.id))
        await self._s.commit()

    # ---- EP11-S01 策略建议 ----
    async def strategy_advice(self, time_range, user) -> AiAdviceResponse:
        prod = await ProductionService(self._s).get_report(user.tenant_id, time_range)
        if not self._has_enough_history(user.tenant_id, time_range):   # ≥6 月
            raise AiDataInsufficientError("历史数据不足，无法生成策略建议")  # 422
        summary = self._summarize(prod)                  # 脱敏聚合
        messages = [{"role": "system", "content": "你是服装电商运营策略顾问..."},
                    {"role": "user", "content": f"基于以下数据给出策略建议含数据依据和置信度：{summary}"}]
        r = await self._run("strategy", {"summary": summary}, messages, user)
        return self._parse_advice(r["content"])

    # ---- EP11-S02 异常归因 ----
    async def anomaly_diagnosis(self, alert_id, user) -> AiAdviceResponse:
        alert = await self._get_alert(alert_id, user.tenant_id)  # 不存在 → 404
        messages = [{"role": "system", "content": "你是数据归因分析专家..."},
                    {"role": "user", "content": f"分析异常可能原因（多维度）：{alert.detail}"}]
        r = await self._run("anomaly", {"alert_id": str(alert_id)}, messages, user)
        return self._parse_advice(r["content"])

    # ---- EP11-S03 博主选择 ----
    async def blogger_suggest(self, style_id, top_n, user) -> list[BloggerSuggestion]:
        style = await self._get_style(style_id, user.tenant_id)   # 不存在 → 404
        candidates = await self._candidate_bloggers(style, user.tenant_id)  # 规则预筛
        if not candidates:
            return []                                    # 无候选返回空（BR-U18-43）
        messages = [{"role": "system", "content": "你是博主匹配顾问..."},
                    {"role": "user", "content": f"为款式 {style.style_name} 从候选中选 Top{top_n} 并排序+理由：{self._summarize_bloggers(candidates)}"}]
        r = await self._run("blogger", {"style_id": str(style_id)}, messages, user)
        return self._parse_suggestions(r["content"], candidates, top_n)

    @staticmethod
    def _parse_advice(content: str) -> AiAdviceResponse:
        # 启发式提取 confidence（high/medium/low），默认 medium
        conf = "high" if "高置信" in content else ("low" if "低置信" in content else "medium")
        return AiAdviceResponse(advice_text=content, confidence=conf, data_basis="基于聚合历史数据")
```

---

## P-U18-03：API 降级契约（3 端点）

```python
router = APIRouter(prefix="/api/ai", tags=["ai"])

@router.post("/strategy-advice",
             dependencies=[require_permission("ai.advice", "read")])
async def strategy_advice(payload: StrategyAdviceRequest, user: CurrentActiveUser,
                          service: AiAdvisoryServiceDep) -> AiAdviceResponse:
    tr = resolve_time_range(payload.preset, payload.date_from, payload.date_to)
    return await service.strategy_advice(tr, user)
    # AiServiceUnavailableError → 503 / AiDataInsufficientError → 422
    # （AppException 全局 error handler 自动映射，无需 api try）

@router.post("/anomaly-diagnosis",
             dependencies=[require_permission("ai.advice", "read")])
async def anomaly_diagnosis(payload: AnomalyDiagnosisRequest, ...) -> AiAdviceResponse:
    return await service.anomaly_diagnosis(payload.alert_id, user)

@router.post("/blogger-suggest",
             dependencies=[require_permission("ai.advice", "read")])
async def blogger_suggest(payload: BloggerSuggestRequest, ...) -> list[BloggerSuggestion]:
    return await service.blogger_suggest(payload.style_id, payload.top_n, user)
```
> AiServiceUnavailableError(status_code=503) + AiDataInsufficientError(422) 继承 AppException → 全局 error handler 映射；不阻塞页面（前端 catch 503 展示提示）。

---

## 故事 / NFR 映射

| 模式 | 故事 | 规则 |
|---|---|---|
| P-U18-01 | 全部（降级核心） | BR-U18-60~63 |
| P-U18-02 | EP11-S01/S02/S03 | BR-U18-01~43 + 80~82 |
| P-U18-03 | 全部 | BR-U18-61 + 90 |
