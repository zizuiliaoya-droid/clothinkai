"""U14 报表预聚合 Celery 任务（占位，V1 不强制启用）。

V1 报表以实时聚合为主（数据量可控，SLA ≤800ms）。precompute_report_cache 留作
V2+ 大数据量优化扩展位；Beat 调度默认不注册（需要时在 celery_app.beat_schedule 启用）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.report_tasks.precompute_report_cache",
    queue="report",
)
def precompute_report_cache() -> dict[str, Any]:
    """占位：V1 不预聚合，直接返回 noop。V2+ 实现按租户预聚合报表缓存。"""
    log.info("precompute_report_cache_noop")
    return {"status": "noop", "note": "V1 实时聚合，预聚合留 V2+"}


__all__ = ["precompute_report_cache"]
