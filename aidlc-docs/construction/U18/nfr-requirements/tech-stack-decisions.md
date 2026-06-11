# U18 技术栈决策（AI 决策建议）

> 零新依赖：复用 httpx（U07）+ U14/U15/U03 数据 + U01 核心。

---

## 1. 依赖

**无新增第三方依赖。** 复用：
- `httpx`（DeepSeekClient，U07 已装）
- U14 ProductionService/WorkProgressService + U15 wecom_alert_log + U03 blogger
- prometheus Counter/Histogram + AuditService（U01）

新增配置（core/config settings）：
- `DEEPSEEK_API_BASE`（默认 https://api.deepseek.com）
- `DEEPSEEK_API_KEY`（环境变量，默认空 → 视为不可用）
- `DEEPSEEK_MODEL`（默认 deepseek-chat）
- `DEEPSEEK_TIMEOUT`（默认 30 秒）

---

## 2. 文件落点（modules/ai 新建 11）

| 文件 | 内容 |
|---|---|
| `config.py` | DEEPSEEK 配置常量（或直接用 core/config settings） |
| `client.py` | DeepSeekClient（httpx /chat/completions + 降级） |
| `enums.py` | AdviceType（strategy/anomaly/blogger）/ AdviceStatus（success/degraded/failed） |
| `exceptions.py` | AiServiceUnavailableError(503) + AiDataInsufficientError(422) |
| `models.py` | AiAdviceLog ORM |
| `schemas.py` | StrategyAdviceRequest / AnomalyDiagnosisRequest / BloggerSuggestRequest / AiAdviceResponse / BloggerSuggestion |
| `permissions.py` | ai.advice:read |
| `repository.py` | AiAdviceLogRepository（add） |
| `service.py` | AiAdvisoryService（strategy_advice / anomaly_diagnosis / blogger_suggest + 留痕 + 降级） |
| `deps.py` | AiAdvisoryServiceDep |
| `api.py` | 3 端点 /api/ai/* |

### 横切修改（5）
| 文件 | 改动 |
|---|---|
| `core/config.py` | +DEEPSEEK_API_BASE/API_KEY/MODEL/TIMEOUT |
| `core/metrics.py` | +ai_advice_total{advice_type,status} + ai_advice_latency_seconds |
| `main.py` | 挂 ai_router |
| `tests/conftest.py` | 追加 ai.models import |
| `alembic/versions/022_*.py` | ai_advice_log 表 + ai.advice scope seed |

---

## 3. DeepSeekClient 降级实现

```python
import time
import httpx
from app.core.config import settings
from app.modules.ai.exceptions import AiServiceUnavailableError

class DeepSeekClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def chat(self, messages: list[dict], *, model: str | None = None) -> dict:
        if not settings.DEEPSEEK_API_KEY:
            raise AiServiceUnavailableError()              # 未配置视为不可用
        t0 = time.monotonic()
        try:
            resp = await self._http.post(
                f"{settings.DEEPSEEK_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
                json={"model": model or settings.DEEPSEEK_MODEL,
                      "messages": messages, "stream": False},
                timeout=settings.DEEPSEEK_TIMEOUT,
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

---

## 4. 指标 + migration 022

```python
ai_advice_total = Counter(
    "ai_advice_total", "Total AI advice requests (U18)",
    labelnames=("advice_type", "status"),  # success/degraded/failed
)
ai_advice_latency_seconds = Histogram(
    "ai_advice_latency_seconds", "AI advice call latency (U18)",
)
```

```python
revision = "022_u18_ai_advice_log"
down_revision = "021_u17_bundle_bi_export"
# ai_advice_log（base + advice_type String(16) + request_payload JSONB +
#   response_text Text null + confidence String(8) null + status String(8) +
#   model String(32) null + latency_ms Integer null + created_by UUID FK user SET NULL）
#   INDEX(tenant_id, advice_type, created_at)
# enable_rls；seed ai.advice:read（pr/pr_manager/operations + admin 通配）
```
- revision id `"022_u18_ai_advice_log"`（20 字符 ≤ 32）。

---

## 5. 测试落点（AI mock）

| 文件 | 重点 |
|---|---|
| `tests/unit/test_ai_advisory.py` | prompt 构造 + 响应解析 + 数据充足校验纯逻辑 |
| `tests/integration/test_ai_advice.py` | monkeypatch DeepSeekClient.chat：成功 success log / 抛 AiServiceUnavailableError degraded log / 数据不足 422 / alert 404 / style 404 / RLS |
| `tests/api/test_ai_api.py` | 3 端点 401 + OpenAPI + 503 降级（monkeypatch 不可用） |

- 全程 monkeypatch DeepSeekClient，不调真实 DeepSeek API。
- 本地 Docker PG16:5561 + Redis7:6416 + Py3.12（U18 唯一端口）。
