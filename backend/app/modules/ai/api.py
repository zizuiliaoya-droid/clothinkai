"""U18 AI 决策建议 API（/api/ai/*）。

AiServiceUnavailableError(503) / AiDataInsufficientError(422) / ResourceNotFoundError(404)
继承 AppException → 全局 error handler 自动映射；503 降级不阻塞页面。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.ai.deps import AiAdvisoryServiceDep
from app.modules.ai.schemas import (
    AiAdviceResponse,
    AnomalyDiagnosisRequest,
    BloggerSuggestRequest,
    BloggerSuggestion,
    StrategyAdviceRequest,
)
from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.report.domain import resolve_time_range

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.post(
    "/strategy-advice",
    response_model=AiAdviceResponse,
    dependencies=[require_permission("ai.advice", "read")],
)
async def strategy_advice(
    payload: StrategyAdviceRequest,
    user: CurrentActiveUser,
    service: AiAdvisoryServiceDep,
) -> AiAdviceResponse:
    tr = resolve_time_range(payload.preset, payload.date_from, payload.date_to)
    return await service.strategy_advice(tr, user)


@router.post(
    "/anomaly-diagnosis",
    response_model=AiAdviceResponse,
    dependencies=[require_permission("ai.advice", "read")],
)
async def anomaly_diagnosis(
    payload: AnomalyDiagnosisRequest,
    user: CurrentActiveUser,
    service: AiAdvisoryServiceDep,
) -> AiAdviceResponse:
    return await service.anomaly_diagnosis(payload.alert_id, user)


@router.post(
    "/blogger-suggest",
    response_model=list[BloggerSuggestion],
    dependencies=[require_permission("ai.advice", "read")],
)
async def blogger_suggest(
    payload: BloggerSuggestRequest,
    user: CurrentActiveUser,
    service: AiAdvisoryServiceDep,
) -> list[BloggerSuggestion]:
    return await service.blogger_suggest(payload.style_id, payload.top_n, user)


__all__ = ["router"]
