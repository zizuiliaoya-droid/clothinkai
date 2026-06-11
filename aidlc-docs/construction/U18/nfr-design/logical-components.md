# U18 逻辑组件（AI 决策建议）

> 单元：U18（EP11-S01、S02、S03）（P3 项目收官单元）
> 新建 modules/ai 11 文件 + 横切 5；无循环依赖。

---

## 1. 新建组件（modules/ai，11）

| 组件 | 类型 | 职责 |
|---|---|---|
| `config.py` | 配置 | DEEPSEEK 配置访问（或直接用 core/config settings） |
| `client.py` | Client | DeepSeekClient（httpx /chat/completions + 降级）+ build_ai_http_client |
| `enums.py` | Enum | AdviceType（strategy/anomaly/blogger）/ AdviceStatus（success/degraded/failed） |
| `exceptions.py` | 异常 | AiServiceUnavailableError(503) + AiDataInsufficientError(422) |
| `models.py` | ORM | AiAdviceLog（TenantScopedModel + RLS） |
| `schemas.py` | Pydantic | StrategyAdviceRequest/AnomalyDiagnosisRequest/BloggerSuggestRequest/AiAdviceResponse/BloggerSuggestion |
| `permissions.py` | 权限 | ai.advice:read |
| `repository.py` | Repository | AiAdviceLogRepository（add） |
| `service.py` | Service | AiAdvisoryService（_run + 3 方法 + 留痕 + 降级 + parse） |
| `deps.py` | DI | AiAdvisoryServiceDep |
| `api.py` | Router | /api/ai/{strategy-advice,anomaly-diagnosis,blogger-suggest} |

---

## 2. 横切修改（5）

| 文件 | 改动 |
|---|---|
| `core/config.py` | +DEEPSEEK_API_BASE/API_KEY/MODEL/TIMEOUT |
| `core/metrics.py` | +ai_advice_total{advice_type,status} + ai_advice_latency_seconds（+ __all__） |
| `main.py` | 挂 ai_router |
| `tests/conftest.py` | 追加 `import app.modules.ai.models` |
| `alembic/versions/022_*.py` | ai_advice_log 表 + ai.advice scope seed |

---

## 3. 依赖图（无循环）

```
ai_api → AiAdvisoryService → DeepSeekClient(httpx) → DeepSeek API（HTTPS）
                           → ProductionService/WorkProgressService(U14)
                           → wecom_alert_log(U15)
                           → blogger repository(U03) / style(U02)
                           → AiAdviceLogRepository → ai_models
```

依赖层级：U18 → U14（report）/ U15（wecom）/ U03（blogger）/ U02（product）→ U13/U05 → U01。无环（被依赖单元仅被读，不反向依赖 U18）。

---

## 4. migration 022 DDL 概要

```
revision = "022_u18_ai_advice_log"
down_revision = "021_u17_bundle_bi_export"

ai_advice_log（base + ）:
  advice_type String(16) NOT NULL
  request_payload JSONB NOT NULL DEFAULT '{}'
  response_text Text NULL
  confidence String(8) NULL
  status String(8) NOT NULL
  model String(32) NULL
  latency_ms Integer NULL
  created_by UUID FK user SET NULL NULL
  INDEX(tenant_id, advice_type, created_at)  [idx_ai_advice_log_tenant_type]

enable_rls("ai_advice_log")
seed: ai.advice:read（pr / pr_manager / operations + admin 通配）
```

down：drop ai_advice_log + 删 ai.advice:read scope。

---

## 5. 启动序列影响

- `main` 挂 ai_router（/api/ai）。
- 无新 Celery 任务/Beat（同步 API；ai_advisory_request 占位 P3 不启用）。
- DeepSeek 未配置（无 API_KEY）→ AI 端点全 503 降级，不影响 main 启动与其余模块。

---

## 6. 测试组件映射（3 文件）

| 测试文件 | 目标组件 | 用例要点 |
|---|---|---|
| `tests/unit/test_ai_advisory.py` | AiAdvisoryService._parse_advice + build_messages 等纯逻辑 | confidence 启发式解析 / parse_suggestions / 数据充足判定 |
| `tests/integration/test_ai_advice.py` | AiAdvisoryService（monkeypatch DeepSeekClient.chat） | 成功→ai_advice_log success / 抛 AiServiceUnavailableError→degraded log + 重抛 / 数据不足 422 / alert 404 / style 404 / RLS |
| `tests/api/test_ai_api.py` | ai_api | 3 端点 401 + OpenAPI 路径 + 503 降级（monkeypatch chat 抛 unavailable） |

- 全程 monkeypatch DeepSeekClient.chat，不调真实 DeepSeek API。
- 复用 conftest fixtures：session/tenant_a/factory/pr_role/pr_manager_role/operations_role/admin_role/product_factory/blogger_factory；异常归因需 wecom_alert_log 行（U15）。

---

## 7. 一致性校验

- 与 nfr-design-patterns P-U18-01~03 伪代码组件一致。
- 与 functional-design domain-entities 组件清单（11 新建 + 5 横切）一致。
- DeepSeekClient 降级模式与 U07 WecomClient 一致；数据复用 U14/U15/U03，无重复实现。
- 依赖图无循环（拓扑：U01 → U02/U05/U13 → U14/U15/U03 → U18）。
