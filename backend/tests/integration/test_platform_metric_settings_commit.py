"""质量重算投递时必须能从独立连接读到已提交的配置（7e R7 收尾 L3）。

不用 ``session`` 的外层事务 / 保存点：直接由 engine 创建 session，真实提交。
Celery ``delay`` 是同步方法，mock 在另一个线程 / 事件循环内开独立连接读取，
读完才返回，不能把读取推迟到 service 返回之后。engine fixture 使用 NullPool。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Tenant
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.repository import PromotionPlatformSettingRepository
from app.modules.metric_config.schemas import PlatformSettingsUpdate
from app.modules.metric_config.service import PlatformMetricSettingService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("existing_row", [False, True], ids=["first_save", "update"])
async def test_delay_sees_committed_setting(
    engine: Any, monkeypatch: pytest.MonkeyPatch, existing_row: bool
) -> None:
    """把投递挪到 commit 前，应读到缺行 / 旧值，并使本用例失败。"""
    import app.tasks.blogger_tasks as tasks

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = uuid4()
    expected = (Decimal("0.750"), 5432)
    observed: list[tuple[Decimal, int] | None] = []
    payload = PlatformSettingsUpdate.model_validate(
        [
            {"platform": "小红书", "like_coefficient": "1", "hit_threshold": 1000},
            {"platform": "抖音", "like_coefficient": "0.750", "hit_threshold": 5432},
            {"platform": "快手", "like_coefficient": "0.1", "hit_threshold": 1000},
            {"platform": "B站", "like_coefficient": "1", "hit_threshold": 1000},
            {"platform": "得物", "like_coefficient": "1", "hit_threshold": 1000},
        ]
    )

    async def read_committed() -> tuple[Decimal, int] | None:
        async with maker() as reader:
            row = (
                await reader.execute(
                    select(
                        PromotionPlatformSetting.like_coefficient,
                        PromotionPlatformSetting.hit_threshold,
                    ).where(
                        PromotionPlatformSetting.tenant_id == tid,
                        PromotionPlatformSetting.platform == "抖音",
                    )
                )
            ).one_or_none()
            return None if row is None else (row[0], row[1])

    token = tenant_id_ctx.set(tid)
    try:
        async with maker() as seed:
            seed.add(Tenant(id=tid, code=f"metric_commit_{uuid4().hex}", name="配置提交测试"))
            await seed.flush()
            if existing_row:
                await PromotionPlatformSettingRepository(seed).upsert(
                    tid, [("抖音", Decimal("0.200"), 2000)], updated_by=None
                )
            await seed.commit()
        assert await read_committed() == (
            (Decimal("0.200"), 2000) if existing_row else None
        ), "场景有效性：保存前是缺行或不同的旧值"

        with ThreadPoolExecutor(max_workers=1) as executor:

            def delay(tenant_id: str) -> None:
                assert tenant_id == str(tid)
                value = executor.submit(asyncio.run, read_committed()).result(timeout=5)
                observed.append(value)
                assert value == expected, "投递时独立连接必须已经能读到新配置"

            monkeypatch.setattr(tasks.recompute_blogger_quality_tags, "delay", delay)
            async with maker() as writer:
                result = await PlatformMetricSettingService(writer).update_settings(
                    payload, cast(Any, SimpleNamespace(id=None, tenant_id=tid)), can_edit=True
                )

        # 投递 helper 会捕获 AssertionError，必须在外面再次断言实际读取结果。
        assert observed == [expected], "投递发生在配置真实提交之前"
        assert result.quality_recompute_queued is True
        assert result.changed == ["抖音"]
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
