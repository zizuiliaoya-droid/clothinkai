"""平台配置保存必须在同一刷新锁之后读现值（7e R7 收尾 L5）。

两条保存连接真实提交，首个请求写入后暂停；确认第二条确实在 PostgreSQL 等锁
才允许第一个提交。覆盖首次插入和已有行更新，不能用单连接 / 保存点 fixture。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Tenant
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.repository import PromotionPlatformSettingRepository
from app.modules.metric_config.schemas import PlatformSettingsUpdate
from app.modules.metric_config.service import PlatformMetricSettingService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _payload(threshold: int) -> PlatformSettingsUpdate:
    return PlatformSettingsUpdate.model_validate(
        [
            {"platform": "小红书", "like_coefficient": "1", "hit_threshold": 1000},
            {"platform": "抖音", "like_coefficient": "0.750", "hit_threshold": threshold},
            {"platform": "快手", "like_coefficient": "0.1", "hit_threshold": 1000},
            {"platform": "B站", "like_coefficient": "1", "hit_threshold": 1000},
            {"platform": "得物", "like_coefficient": "1", "hit_threshold": 1000},
        ]
    )


async def _wait_for_advisory_lock(engine: Any, pid: int) -> None:
    """只观察本用例的第二条连接，确保它已进入实际锁等待。"""
    async with engine.connect() as observer:
        async with asyncio.timeout(5):
            while True:
                waiting = (
                    await observer.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE pid = CAST(:pid AS integer) "
                            "AND locktype = 'advisory' AND NOT granted)"
                        ),
                        {"pid": pid},
                    )
                ).scalar_one()
                if waiting:
                    return
                await asyncio.sleep(0.02)


@pytest.mark.parametrize("existing_row", [False, True], ids=["first_save", "update"])
@pytest.mark.parametrize("second_threshold", [7000, 5432], ids=["changed", "unchanged"])
async def test_concurrent_save_reads_committed_before_after_lock(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    existing_row: bool,
    second_threshold: int,
) -> None:
    import app.tasks.blogger_tasks as tasks

    monkeypatch.setattr(tasks.recompute_blogger_quality_tags, "delay", lambda _tid: None)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = uuid4()
    user = cast(Any, SimpleNamespace(id=None, tenant_id=tid))
    token = tenant_id_ctx.set(tid)
    try:
        async with maker() as seed:
            seed.add(Tenant(id=tid, code=f"metric_conc_{uuid4().hex}", name="配置并发保存测试"))
            await seed.flush()
            if existing_row:
                await PromotionPlatformSettingRepository(seed).upsert(
                    tid, [("抖音", Decimal("0.200"), 2000)], updated_by=None
                )
            await seed.commit()

        first_written = asyncio.Event()
        release_first = asyncio.Event()
        real_upsert = PromotionPlatformSettingRepository.upsert
        async with maker() as first, maker() as second:

            async def pause_first(
                repo: PromotionPlatformSettingRepository, *args: Any, **kwargs: Any
            ) -> None:
                await real_upsert(repo, *args, **kwargs)
                if repo._session is first:
                    first_written.set()
                    await release_first.wait()

            monkeypatch.setattr(PromotionPlatformSettingRepository, "upsert", pause_first)
            second_pid = int((await second.execute(text("SELECT pg_backend_pid()"))).scalar_one())
            running = []
            try:
                first_task = asyncio.create_task(
                    PlatformMetricSettingService(first).update_settings(
                        _payload(5432), user, can_edit=True
                    )
                )
                running.append(first_task)
                await asyncio.wait_for(first_written.wait(), timeout=5)
                second_task = asyncio.create_task(
                    PlatformMetricSettingService(second).update_settings(
                        _payload(second_threshold), user, can_edit=True
                    )
                )
                running.append(second_task)
                await _wait_for_advisory_lock(engine, second_pid)
                assert not second_task.done(), "第二次保存必须等待第一条提交"
                release_first.set()
                first_result, second_result = await asyncio.wait_for(
                    asyncio.gather(first_task, second_task), timeout=5
                )
            finally:
                release_first.set()
                await asyncio.gather(*running, return_exceptions=True)

        async with maker() as reader:
            audits = (
                (
                    await reader.execute(
                        select(AuditLog)
                        .where(AuditLog.tenant_id == tid, AuditLog.action == "metric_config.update")
                        .order_by(AuditLog.id)
                    )
                )
                .scalars()
                .all()
            )
            saved = (
                await reader.execute(
                    select(
                        PromotionPlatformSetting.like_coefficient,
                        PromotionPlatformSetting.hit_threshold,
                    ).where(
                        PromotionPlatformSetting.tenant_id == tid,
                        PromotionPlatformSetting.platform == "抖音",
                    )
                )
            ).one()

        assert first_result.changed == ["抖音"]
        assert tuple(saved) == (Decimal("0.750"), second_threshold)
        assert audits[0].before == {
            "抖音": {
                "like_coefficient": "0.2" if existing_row else "0.1",
                "hit_threshold": 2000 if existing_row else 10000,
            }
        }
        assert audits[0].after == {
            "抖音": {"like_coefficient": "0.75", "hit_threshold": 5432},
            "coverage_invalidated": 0,
        }
        if second_threshold == 5432:
            assert second_result.changed == [], "已被先到请求保存的相同值不应再写 / 审计 / 投递"
            assert second_result.quality_recompute_queued is False
            assert len(audits) == 1
        else:
            assert len(audits) == 2, "两次有效改动的审计都必须保留"
            assert second_result.changed == ["抖音"]
            assert audits[1].before == {"抖音": audits[0].after["抖音"]}
            assert audits[1].after == {
                "抖音": {"like_coefficient": "0.75", "hit_threshold": 7000},
                "coverage_invalidated": 0,
            }
    finally:
        tenant_id_ctx.reset(token)
        async with maker() as cleanup:
            for table in ("audit_log", "promotion_platform_setting"):
                await cleanup.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id = CAST(:t AS uuid)"), {"t": tid}
                )
            await cleanup.execute(
                text("DELETE FROM tenant WHERE id = CAST(:t AS uuid)"), {"t": tid}
            )
            await cleanup.commit()
