# U18 代码生成计划（Code Generation Plan）

> 单元：U18 — AI 决策建议（EP11-S01、S02、S03）（P3 项目收官单元）
> 分批：**2 批** + Build & Test
> Build & Test：Docker PG16:5561 + Redis7:6416 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 新建 modules/ai（client/enums/exceptions/models/schemas/permissions/repository/service/deps/api）；migration 022 = ai_advice_log 1 表 + ai.advice scope seed。
- [Answer] DeepSeekClient httpx /chat/completions + 优雅降级；AiAdvisoryService 3 方法 + _run 统一编排 + 留痕 + 降级。
- [Answer] 配置 DEEPSEEK_*；指标 ai_advice_total/latency；测试全程 monkeypatch DeepSeekClient。

---

## 1. 步骤（2 批）

### Batch 1 — 模型 + Schema + 枚举 + 异常 + 权限 + 配置 + 指标 + client + repository
- [x] 1.1 enums.py（AdviceType/AdviceStatus）+ exceptions.py（503/422）
- [x] 1.2 models.py（AiAdviceLog）+ schemas.py（5 schema）
- [x] 1.3 permissions.py（ai.advice:read）+ core/config DEEPSEEK_*
- [x] 1.4 core/metrics +ai_advice_total/ai_advice_latency_seconds
- [x] 1.5 client.py（DeepSeekClient 降级）+ repository.py（AiAdviceLogRepository + AiDataRepository）

### Batch 2 — Service + Deps + API + main + migration + conftest + 测试
- [x] 2.1 service.py（AiAdvisoryService _run + strategy/anomaly/blogger + 留痕 + 降级）
- [x] 2.2 deps.py（AiAdvisoryServiceDep）+ api.py（3 端点）
- [x] 2.3 main.py 挂 ai_router + migration 022 + conftest import
- [x] 2.4 测试 3 文件（unit/integration/api）

### Build & Test
- [x] B.1 Docker PG16:5561 + Redis7:6416；alembic upgrade head（含 022）；U18 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 2 批 + Build & Test。**
