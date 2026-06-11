"""U18 AiAdvisoryService（数据准备 + prompt + DeepSeek + 留痕 + 优雅降级）。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import ai_advice_latency_seconds, ai_advice_total
from app.modules.ai.client import DeepSeekClient, build_ai_http_client
from app.modules.ai.enums import AdviceStatus, AdviceType
from app.modules.ai.exceptions import (
    AiDataInsufficientError,
    AiServiceUnavailableError,
)
from app.modules.ai.models import AiAdviceLog
from app.modules.ai.repository import AiAdviceLogRepository, AiDataRepository
from app.modules.ai.schemas import AiAdviceResponse, BloggerSuggestion
from app.core.exceptions import ResourceNotFoundError
from app.modules.report.production_service import ProductionService

_MIN_HISTORY_MONTHS = 6
_MAX_CANDIDATES = 20


class AiAdvisoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._log_repo = AiAdviceLogRepository(session)
        self._data = AiDataRepository(session)

    # ----------------------------- 统一编排 ----------------------------- #

    async def _run(
        self, advice_type: str, payload: dict, messages: list[dict], user: Any
    ) -> dict:
        http = build_ai_http_client()
        try:
            result = await DeepSeekClient(http).chat(messages)
        except AiServiceUnavailableError:
            await self._log(advice_type, payload, None, AdviceStatus.DEGRADED.value,
                            None, None, user)
            ai_advice_total.labels(
                advice_type=advice_type, status="degraded"
            ).inc()
            raise
        finally:
            await http.aclose()
        ai_advice_latency_seconds.observe(result["latency_ms"] / 1000)
        await self._log(advice_type, payload, result["content"],
                        AdviceStatus.SUCCESS.value, result["model"],
                        result["latency_ms"], user)
        ai_advice_total.labels(advice_type=advice_type, status="success").inc()
        return result

    async def _log(self, advice_type, payload, text, status, model, latency, user):
        self._log_repo.add(AiAdviceLog(
            tenant_id=user.tenant_id, advice_type=advice_type,
            request_payload=payload, response_text=text, status=status,
            confidence=None, model=model, latency_ms=latency, created_by=user.id,
        ))
        await self._s.commit()

    # ----------------------------- EP11-S01 策略 ----------------------------- #

    async def strategy_advice(
        self, time_range: tuple[date, date], user: Any
    ) -> AiAdviceResponse:
        months = await self._data.promotion_months(user.tenant_id)
        if months < _MIN_HISTORY_MONTHS:
            raise AiDataInsufficientError(
                f"历史数据不足（{months} 个月，需 ≥ {_MIN_HISTORY_MONTHS}），无法生成策略建议"
            )
        report = await ProductionService(self._s).get_report(
            user.tenant_id, time_range
        )
        summary = self._summarize_production(report)
        messages = [
            {"role": "system",
             "content": "你是服装电商运营策略顾问，基于聚合数据给出可执行建议。"},
            {"role": "user",
             "content": "基于以下投产聚合数据，给出推广策略建议，"
                        f"并说明数据依据和置信度：{json.dumps(summary, ensure_ascii=False)}"},
        ]
        result = await self._run(
            AdviceType.STRATEGY.value, {"summary": summary}, messages, user
        )
        return self._parse_advice(result["content"], summary)

    # ----------------------------- EP11-S02 异常归因 ----------------------------- #

    async def anomaly_diagnosis(
        self, alert_id: UUID, user: Any
    ) -> AiAdviceResponse:
        alert = await self._data.get_alert(alert_id, user.tenant_id)
        if alert is None:
            raise ResourceNotFoundError("预警记录不存在")
        messages = [
            {"role": "system",
             "content": "你是数据归因分析专家，从款式/投放/退货/转化多维度分析异常原因。"},
            {"role": "user",
             "content": f"异常类型={alert.alert_type}，详情="
                        f"{json.dumps(alert.detail, ensure_ascii=False)}，请给出多维度归因分析。"},
        ]
        result = await self._run(
            AdviceType.ANOMALY.value, {"alert_id": str(alert_id)}, messages, user
        )
        return self._parse_advice(result["content"], None)

    # ----------------------------- EP11-S03 博主选择 ----------------------------- #

    async def blogger_suggest(
        self, style_id: UUID, top_n: int, user: Any
    ) -> list[BloggerSuggestion]:
        style = await self._data.get_style(style_id, user.tenant_id)
        if style is None:
            raise ResourceNotFoundError("款式不存在")
        candidates = await self._data.candidate_bloggers(
            user.tenant_id, limit=_MAX_CANDIDATES
        )
        if not candidates:
            return []
        cand_summary = [
            {"blogger_id": str(b.id), "nickname": b.nickname,
             "category_tags": b.category_tags, "quality_tags": b.quality_tags,
             "follower_count": b.follower_count}
            for b in candidates
        ]
        messages = [
            {"role": "system",
             "content": "你是博主匹配顾问，为款式从候选博主中选出最匹配的并排序。"},
            {"role": "user",
             "content": f"款式：{style.style_name}（{style.category}）。"
                        f"候选博主：{json.dumps(cand_summary, ensure_ascii=False)}。"
                        f"请选 Top{top_n} 并给出 match_score(0-1) 和理由。"},
        ]
        await self._run(
            AdviceType.BLOGGER.value, {"style_id": str(style_id)}, messages, user
        )
        # AI 文本不强制结构化解析；V1 返回候选 Top N（规则排序）+ AI 已留痕
        return [
            BloggerSuggestion(
                blogger_id=b.id, nickname=b.nickname,
                match_score=round(1.0 - i * 0.05, 4),
                reason="基于品类/质量标签与粉丝量综合匹配",
            )
            for i, b in enumerate(candidates[:top_n])
        ]

    # ----------------------------- helpers ----------------------------- #

    @staticmethod
    def _summarize_production(report: Any) -> dict:
        items = report.items
        pay_total = sum((r.pay_amount for r in items), Decimal("0"))
        return {
            "style_count": len(items),
            "pay_amount_total": str(pay_total),
            "top_styles": [
                {"style_code": r.style_code, "pay_amount": str(r.pay_amount),
                 "net_roi": str(r.net_roi) if r.net_roi is not None else None}
                for r in items[:5]
            ],
        }

    @staticmethod
    def _parse_advice(content: str, basis: dict | None) -> AiAdviceResponse:
        conf = "high" if "高置信" in content else (
            "low" if "低置信" in content else "medium"
        )
        return AiAdviceResponse(
            advice_text=content,
            confidence=conf,
            data_basis=(json.dumps(basis, ensure_ascii=False) if basis else None),
        )


__all__ = ["AiAdvisoryService"]
