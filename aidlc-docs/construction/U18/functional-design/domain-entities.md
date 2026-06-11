# U18 领域实体（AI 决策建议）

> 单元：U18（EP11-S01、S02、S03）（P3 实验功能，项目收官单元）
> 模块归属：新建 `modules/ai`（DeepSeekClient + AiAdvisoryService）
> 依赖：U14（report 历史数据）、U15（wecom_alert_log 异常）、U03/U11（blogger 选择）

---

## 1. 实体总览

| 实体 | 表名 | 用途 | 关键约束 |
|---|---|---|---|
| AiAdviceLog | `ai_advice_log` | AI 请求/响应留痕（成功/降级/失败） | TenantScopedModel + RLS |

仅 1 张新表（AI 调用留痕）；其余数据从 U14/U15/U03 既有表读取。

---

## 2. AiAdviceLog（AI 调用留痕）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| advice_type | String(16) | NOT NULL | strategy / anomaly / blogger（AdviceType） |
| request_payload | JSONB | NOT NULL DEFAULT '{}' | 请求参数（脱敏摘要） |
| response_text | Text | NULL | AI 返回建议文本（降级/失败时空） |
| confidence | String(8) | NULL | 置信度 high/medium/low（可空） |
| status | String(8) | NOT NULL | success / degraded / failed（AdviceStatus） |
| model | String(32) | NULL | 调用的模型名（如 deepseek-chat） |
| latency_ms | Integer | NULL | 调用耗时毫秒 |
| created_by | UUID FK user SET NULL | NULL | 发起人 |

索引：`idx_ai_advice_log_tenant_type` (tenant_id, advice_type, created_at)。

### 设计要点
- 每次 AI 请求落一条（成功/降级/失败均留痕，便于审计 + 成本追踪）。
- request_payload 仅存脱敏聚合摘要（不含成本价等敏感字段）。
- 降级/失败时 response_text 空，status 标记。

---

## 3. 枚举

| 枚举 | 值 |
|---|---|
| AdviceType | strategy（策略建议）/ anomaly（异常归因）/ blogger（博主选择） |
| AdviceStatus | success / degraded（AI 不可用）/ failed（其他错误） |

---

## 4. DeepSeekClient（外部 API 封装）

| 方法 | 签名（语义） | 说明 |
|---|---|---|
| chat | `(messages: list[dict], *, model: str \| None) -> ChatResult` | 调 DeepSeek /chat/completions（OpenAI 兼容） |

- ChatResult：`{content: str, model: str, latency_ms: int}`。
- 用 httpx.AsyncClient + Authorization Bearer（settings.DEEPSEEK_API_KEY）+ timeout（settings.DEEPSEEK_TIMEOUT 默认 30s）。
- 连接失败/超时/限流/非 200 → AiServiceUnavailableError（503）。
- API key 仅环境变量；不回显、不入日志。

---

## 5. AiAdvisoryService（3 方法 I/O）

| 方法 | 输入 | 输出 | 数据来源 |
|---|---|---|---|
| strategy_advice | time_range | AiAdvice(text, confidence, basis) | ProductionService + WorkProgressService 摘要 |
| anomaly_diagnosis | alert_id | AiAdvice(text) | wecom_alert_log(U15) + 关联款式投产 |
| blogger_suggest | style_id, top_n=5 | list[BloggerSuggestion(blogger_id, match_score, reason)] | style + blogger 库(U03) 候选 |

- AiAdvice：`{advice_text, confidence, data_basis}`。
- 各方法：数据准备 → prompt 构造 → DeepSeekClient.chat → 解析 → 落 ai_advice_log → 降级时 503。

---

## 6. 三类 advice 数据准备口径

| advice_type | 数据准备 | 充足性/前置 |
|---|---|---|
| strategy | ProductionService.get_report(6 个月) + WorkProgressService 聚合摘要 | 历史推广数据 ≥6 个月，不足返回提示不调 AI |
| anomaly | wecom_alert_log.detail（异常类型/值/阈值）+ 款式投产快照 | alert_id 存在（不存在 404） |
| blogger | style 信息 + blogger 候选（category/quality_tags 匹配）Top 候选 | style_id 存在（不存在 404） |

---

## 7. 组件清单（新建）

### 新建（modules/ai）
| 文件 | 职责 |
|---|---|
| `config.py` | DEEPSEEK_* 配置读取（或复用 core/config settings） |
| `client.py` | DeepSeekClient（httpx + 降级） |
| `enums.py` | AdviceType / AdviceStatus |
| `exceptions.py` | AiServiceUnavailableError(503) + AiDataInsufficientError |
| `models.py` | AiAdviceLog ORM |
| `schemas.py` | StrategyAdviceRequest / AnomalyDiagnosisRequest / BloggerSuggestRequest / AiAdviceResponse / BloggerSuggestion |
| `permissions.py` | ai.advice:read |
| `repository.py` | AiAdviceLogRepository（add） |
| `service.py` | AiAdvisoryService（3 方法 + 留痕 + 降级） |
| `deps.py` | AiAdvisoryServiceDep |
| `api.py` | 3 端点 /api/ai/* |

### 横切修改
| 文件 | 改动 |
|---|---|
| `core/config.py` | +DEEPSEEK_API_BASE/API_KEY/MODEL/TIMEOUT |
| `core/metrics.py` | +ai_advice_total{advice_type,status} |
| `main.py` | 挂 ai_router |
| `tests/conftest.py` | 追加 ai.models import |
| `alembic/versions/022_*.py` | ai_advice_log 表 + ai.advice scope seed |

---

## 8. ER 关系

```
tenant 1──* ai_advice_log（每次 AI 调用一条）
AiAdvisoryService ──read──> ProductionService(U14) / wecom_alert_log(U15) / blogger(U03)
                  ──call──> DeepSeekClient ──HTTPS──> DeepSeek API
                  ──log──> ai_advice_log
```

---

## 9. 演化说明
- P3 实验功能，独立交付不阻塞前序；DeepSeek 不可用全程降级 503 不影响其他模块。
- blogger_suggest 匹配 V1 用规则候选 + AI 排序；后续可增强向量检索。
- ai_advisory_request Celery 任务占位（长文本/批量），P3 同步为主。
