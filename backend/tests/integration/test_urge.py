"""催发任务集成测试（PRD V1.4 改动 2）。

守的不变量：
- 一个推广单只有一个催发任务（并发/重复调用都不该建出第二条）
- 自动扫描当天只计一次；手动催不受当日限制
- 自动扫描有超时下限 —— 不设的话生产 5134 条半年前的陈旧排期会被全量建任务
- 博主发布 / 单据取消 → 任务自动关闭，关闭后不能再催
- 已发布 / 已取消的单据不允许新建催发
- 看板数字按阈值算，阈值改了立刻生效
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import PublishStatus
from app.modules.promotion.schemas import PromotionCancelRequest, PromotionPublishRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.enums import UrgeCloseReason, UrgeTaskStatus
from app.modules.urge.exceptions import (
    UrgeBatchEmptyError,
    UrgeNotApplicableError,
    UrgeTaskClosedError,
)
from app.modules.urge.schemas import UrgeConfigUpdate, UrgeTaskListFilters
from app.modules.urge.service import UrgeService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _promo(
    promotion_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    pr: Any,
    *,
    code: str,
    scheduled: date | None = None,
    publish_status: str = PublishStatus.UNPUBLISHED.value,
) -> Any:
    style = await product_factory.style(style_code=code)
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=pr,
        scheduled_publish_date=scheduled,
        publish_status=publish_status,
    )


class TestManualUrge:
    async def test_first_urge_creates_task_and_record(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_FIRST"
            )
            detail = await UrgeService(session).urge_once(promo.id, pr, note="微信催了一次")

            assert detail.status == UrgeTaskStatus.OPEN.value
            assert detail.urge_count == 1
            assert detail.last_urged_at is not None
            assert len(detail.records) == 1
            assert detail.records[0].trigger_type == "手动"
            assert detail.records[0].note == "微信催了一次"
            assert detail.records[0].screenshot_url is None
            assert detail.promotion_id == promo.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_repeated_urge_reuses_one_task(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """一单一任务。多次催发累加计次，不是建多个任务。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_REPEAT"
            )
            svc = UrgeService(session)
            first = await svc.urge_once(promo.id, pr, note="第一次")
            second = await svc.urge_once(promo.id, pr, note="第二次")

            assert first.id == second.id
            assert second.urge_count == 2
            assert len(second.records) == 2
            # 时间线倒序：最新的在最前
            assert second.records[0].note == "第二次"

            count = (
                await session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM urge_task "
                        "WHERE tenant_id = :t AND promotion_id = :p"
                    ),
                    {"t": tenant_a.id, "p": promo.id},
                )
            ).scalar_one()
            assert count == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_over_limit_flag_follows_config(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """「超过 N 次提示主管」由服务端算，阈值改了立刻生效。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_LIMIT"
            )
            svc = UrgeService(session)
            await svc.update_config(UrgeConfigUpdate(max_urge_times=2), pr)

            for _ in range(2):
                detail = await svc.urge_once(promo.id, pr)
            assert detail.urge_count == 2
            assert detail.over_limit is False  # 恰好等于阈值不算超

            detail = await svc.urge_once(promo.id, pr)
            assert detail.urge_count == 3
            assert detail.over_limit is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_published_promotion_cannot_be_urged(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_PUBLISHED",
                publish_status=PublishStatus.PUBLISHED.value,
            )
            with pytest.raises(UrgeNotApplicableError):
                await UrgeService(session).urge_once(promo.id, pr)
        finally:
            tenant_id_ctx.reset(token)


class TestAutoClose:
    async def test_publish_closes_task(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PRD：博主确认发布 → 任务自动关闭。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_PUB_CLOSE"
            )
            svc = UrgeService(session)
            task = await svc.urge_once(promo.id, pr)

            await PromotionService(session).publish(
                promo.id,
                PromotionPublishRequest(
                    publish_url="https://www.xiaohongshu.com/explore/abc",
                    actual_publish_date=get_today(),
                ),
                pr,
            )

            reloaded = await svc.get_task_detail(task.id, pr)
            assert reloaded.status == UrgeTaskStatus.CLOSED.value
            assert reloaded.close_reason == UrgeCloseReason.PUBLISHED.value
            assert reloaded.closed_at is not None
        finally:
            tenant_id_ctx.reset(token)

    async def test_cancel_closes_task_and_blocks_further_urge(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_CANCEL"
            )
            svc = UrgeService(session)
            await svc.urge_once(promo.id, pr)

            await PromotionService(session).cancel(
                promo.id, PromotionCancelRequest(cancel_reason="博主档期冲突"), pr
            )

            # 单据已取消 → 连「可催发」这道门都过不去
            with pytest.raises(UrgeNotApplicableError):
                await svc.urge_once(promo.id, pr)
        finally:
            tenant_id_ctx.reset(token)

    async def test_closed_task_rejects_urge(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """手动关闭后单据还是未发布，但任务关了就不该再催。

        这条和上一条的区别：上一条被 publish_status 挡住，这条推广单仍可催，
        是任务状态挡住的 —— 两道门要分别守住。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="UG_MANUAL_CLOSE"
            )
            svc = UrgeService(session)
            task = await svc.urge_once(promo.id, pr)
            closed = await svc.close_task(task.id, pr, reason="改走召回")

            assert closed.status == UrgeTaskStatus.CLOSED.value
            assert closed.close_reason == UrgeCloseReason.MANUAL.value
            # 关闭动作本身也进时间线，否则详情页看不出为什么不催了
            assert any("[手动关闭]" in (r.note or "") for r in closed.records)

            with pytest.raises(UrgeTaskClosedError):
                await svc.urge_once(promo.id, pr)
        finally:
            tenant_id_ctx.reset(token)


class TestAutoScan:
    async def test_scan_creates_task_once_per_day(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """同一天重复扫描不重复计次（Beat 重跑 / 多实例都不该把次数刷上去）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_SCAN",
                scheduled=today + timedelta(days=2),
            )
            svc = UrgeService(session)
            first = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
            assert first.tasks_created == 1
            assert first.records_created == 1

            second = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
            assert second.tasks_created == 0
            assert second.records_created == 0
            assert second.skipped_same_day == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_scan_skips_outside_window(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """窗口两端都要守。

        上界：还没到临期的不催（PRD「≤ N 天」）。
        下界：超期太久的陈旧单不自动催 —— 生产有 5134 条排期在半年前，
        不设下界的话首次扫描就建 5134 个任务。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            svc = UrgeService(session)
            cfg = await svc.get_effective_config(tenant_a.id)

            # 远未到期
            await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_FUTURE",
                scheduled=today + timedelta(days=cfg["no_publish_days"] + 10),
            )
            # 超期太久（模拟生产那批半年前的历史单）
            await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_ANCIENT",
                scheduled=today - timedelta(days=cfg["max_overdue_days"] + 100),
            )
            result = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
            assert result.tasks_created == 0
            assert result.records_created == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_scan_respects_disable_switch(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_OFF",
                scheduled=today + timedelta(days=1),
            )
            svc = UrgeService(session)
            await svc.update_config(UrgeConfigUpdate(auto_scan_enabled=False), pr)
            result = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
            assert result.tasks_created == 0
            assert result.records_created == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_scan_closes_stale_task(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """兜底收口：单据已发布但任务还开着（历史数据或别的路径漏了调用）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            promo = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_STALE",
                scheduled=today + timedelta(days=1),
            )
            svc = UrgeService(session)
            task = await svc.urge_once(promo.id, pr)

            # 绕过 publish()，直接改状态模拟「别的路径改了状态没关任务」
            await session.execute(
                sa_text("UPDATE promotion SET publish_status = '已发布' WHERE id = :p"),
                {"p": promo.id},
            )
            await session.commit()

            result = await svc.scan_tenant(tenant_id=tenant_a.id, today=today)
            assert result.tasks_closed == 1
            reloaded = await svc.get_task_detail(task.id, pr)
            assert reloaded.status == UrgeTaskStatus.CLOSED.value
            assert reloaded.close_reason == UrgeCloseReason.PUBLISHED.value
        finally:
            tenant_id_ctx.reset(token)


class TestBatchUrge:
    async def test_batch_urges_all_unpublished_of_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="UG_BATCH")
            for _ in range(3):
                blogger = await blogger_factory.blogger()
                await promotion_factory.promotion(style=style, blogger=blogger, pr=pr)
            # 已发布的那条不该被catch进来
            published_blogger = await blogger_factory.blogger()
            await promotion_factory.promotion(
                style=style,
                blogger=published_blogger,
                pr=pr,
                publish_status=PublishStatus.PUBLISHED.value,
            )

            resp = await UrgeService(session).urge_batch(style.id, pr, note="批量催一轮")
            assert resp.urged_count == 3
            assert len(resp.task_ids) == 3
            assert resp.skipped_closed == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_batch_with_nothing_to_urge_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """报错而不是静默返回 0 —— PR 点了按钮什么都没发生，得知道为什么。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="UG_BATCH_EMPTY")
            with pytest.raises(UrgeBatchEmptyError):
                await UrgeService(session).urge_batch(style.id, pr)
        finally:
            tenant_id_ctx.reset(token)


class TestDashboardAndList:
    async def test_dashboard_counts(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            svc = UrgeService(session)
            await svc.update_config(UrgeConfigUpdate(max_urge_times=1), pr)

            overdue = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_DASH_OVERDUE",
                scheduled=today - timedelta(days=3),
            )
            upcoming = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_DASH_SOON",
                scheduled=today + timedelta(days=2),
            )
            # 超期这条催两次 → 超过阈值 1
            await svc.urge_once(overdue.id, pr)
            await svc.urge_once(overdue.id, pr)
            await svc.urge_once(upcoming.id, pr)

            board = await svc.dashboard(pr)
            assert board.pending == 2
            assert board.overdue == 1
            assert board.over_limit == 1
            assert board.urged_this_week == 3  # 按次数算，不是按任务数
            assert board.max_urge_times == 1
            assert board.week_start == today - timedelta(days=today.weekday())
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_filters(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            today = get_today()
            svc = UrgeService(session)
            overdue = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_LIST_OVERDUE",
                scheduled=today - timedelta(days=5),
            )
            await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="UG_LIST_SOON",
                scheduled=today + timedelta(days=5),
            )
            await svc.urge_once(overdue.id, pr)

            items, total = await svc.list_tasks(UrgeTaskListFilters(), pr)
            assert total == 1
            assert items[0].overdue_days == 5

            items, total = await svc.list_tasks(UrgeTaskListFilters(overdue_only=True), pr)
            assert total == 1

            items, total = await svc.list_tasks(
                UrgeTaskListFilters(status=UrgeTaskStatus.CLOSED), pr
            )
            assert total == 0
        finally:
            tenant_id_ctx.reset(token)


class TestConfig:
    async def test_defaults_without_row(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        """没有配置行也要能工作 —— 新租户还没写配置的空窗期不该让催发挂掉。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await session.execute(
                sa_text("DELETE FROM urge_config WHERE tenant_id = :t"), {"t": tenant_a.id}
            )
            await session.commit()
            cfg = await UrgeService(session).get_effective_config(tenant_a.id)
            assert cfg["no_publish_days"] == 5
            assert cfg["max_urge_times"] == 3
            assert cfg["max_overdue_days"] == 30
            assert cfg["auto_scan_enabled"] is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_upsert_is_idempotent(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        """单行 upsert：改两次还是一行，不是两行。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = UrgeService(session)
            await svc.update_config(UrgeConfigUpdate(no_publish_days=7), user)
            resp = await svc.update_config(UrgeConfigUpdate(no_publish_days=9), user)
            assert resp.no_publish_days == 9

            count = (
                await session.execute(
                    sa_text("SELECT COUNT(*) FROM urge_config WHERE tenant_id = :t"),
                    {"t": tenant_a.id},
                )
            ).scalar_one()
            assert count == 1
        finally:
            tenant_id_ctx.reset(token)
