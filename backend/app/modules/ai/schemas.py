"""U18 AI 决策建议 Schema。"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class StrategyAdviceRequest(BaseModel):
    preset: str = "last_30d"
    date_from: date | None = None
    date_to: date | None = None


class AnomalyDiagnosisRequest(BaseModel):
    alert_id: UUID


class BloggerSuggestRequest(BaseModel):
    style_id: UUID
    top_n: int = Field(5, ge=1, le=20)


class AiAdviceResponse(BaseModel):
    advice_text: str
    confidence: str | None = None
    data_basis: str | None = None


class BloggerSuggestion(BaseModel):
    blogger_id: UUID
    nickname: str
    match_score: float
    reason: str


__all__ = [
    "AiAdviceResponse",
    "AnomalyDiagnosisRequest",
    "BloggerSuggestRequest",
    "BloggerSuggestion",
    "StrategyAdviceRequest",
]
