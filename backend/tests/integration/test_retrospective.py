"""复盘环节集成测试（PRD V1.4 改动 4）。

守的不变量：
- 录 7 天数据要求 ``settlement_status='已付款'``（结款没完成复盘没有意义）
- 三个指标 + 截图都必填，截图缺了整个操作回滚（不留「数字录了图没传」的中间态）
- 指标与状态推进同事务
- 禁止确认自己写的复盘 —— 这条必须在 service 层挡，权限层拦不住
- 打回不删旧记录，重写时追加新行
- 博主档案只收已确认的复盘，未确认的不进 hover 卡
- retro_status 与 settlement_status 正交：复盘推进不影响「已付款」这个过滤条件
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import PublishStatus, RetroStatus, SettlementStatus
from app.modules.promotion.exceptions import (
    MetricsScreenshotRequiredError,
    RetroSelfConfirmForbiddenError,
    SettlementNotPaidError,
)
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    RetrospectiveConfirmRequest,
    RetrospectiveSubmitRequest,
)
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 最小合法 PNG（魔数 + 一点内容），check_image_payload 只验魔数不解码
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_SHOT = ("data.png", "image/png", _PNG)
_METRICS = PromotionMetricsRequest(like_count=1200, collect_count=300, comment_count=45)


class _FakeS3:
    """只替掉真正要联网的那三个调用。

    ``create_upload_record`` 在 R2 未配置时直接抛 ``AttachmentError``（没有本地回退，
    与 ``upload_bytes`` 不同），所以测试环境必须给 ``attachment_service`` 装一个假
    client —— 这样 Attachment 行、状态机、FK 都走真实路径，只有网络调用是假的。
    """

    def put_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def delete_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        key = (kw.get("Params") or {}).get("Key", "")
        return f"https://fake-r2.local/{key}"


@pytest.fixture(autouse=True)
def _fake_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)


async def _paid_promo(
    promotion_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    pr: Any,
    *,
    code: str,
    settlement_status: str = SettlementStatus.PAID.value,
) -> Any:
    """造一张已结款、已发布的推广单 —— 复盘的起点。"""
    style = await product_factory.style(style_code=code)
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=pr,
        publish_status=PublishStatus.PUBLISHED.value,
        settlement_status=settlement_status,
    )


class TestRecordMetrics:
    async def test_record_metrics_enters_pending_retro(
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
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_OK"
            )
            resp = await PromotionService(session).record_metrics(
                promo.id, _METRICS, pr, screenshot=_SHOT
            )
            assert resp.retro_status == RetroStatus.PENDING_RETRO.value
            assert resp.like_count == 1200
            assert resp.collect_count == 300
            assert resp.comment_count == 45
            assert resp.metrics_recorded_at is not None
            # 结款状态不受影响 —— 两个状态机正交
            assert resp.settlement_status == SettlementStatus.PAID.value
        finally:
            tenant_id_ctx.reset(token)

    async def test_unpaid_promotion_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """结款没完成不许录数据 —— ROI 的分母还没定。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="RT_UNPAID",
                settlement_status=SettlementStatus.PENDING_PAYMENT.value,
            )
            with pytest.raises(SettlementNotPaidError):
                await PromotionService(session).record_metrics(
                    promo.id, _METRICS, pr, screenshot=_SHOT
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_missing_screenshot_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PRD 原文「录入点赞/收藏/评论 + 截图」，截图不是可选的。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_NOSHOT"
            )
            with pytest.raises(MetricsScreenshotRequiredError):
                await PromotionService(session).record_metrics(
                    promo.id, _METRICS, pr, screenshot=None
                )
            # 状态没动，指标也没写进去
            await session.refresh(promo)
            assert promo.retro_status == RetroStatus.NOT_STARTED.value
            assert promo.like_count is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_fake_image_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """声明 PNG 但内容不是 —— 改个扩展名就能当图片存进来是不行的。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_FAKEIMG"
            )
            with pytest.raises(MetricsScreenshotRequiredError):
                await PromotionService(session).record_metrics(
                    promo.id,
                    _METRICS,
                    pr,
                    screenshot=("x.png", "image/png", b"not a png at all"),
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_negative_metrics_rejected_by_schema(self) -> None:
        with pytest.raises(PydanticValidationError):
            PromotionMetricsRequest(like_count=-1, collect_count=0, comment_count=0)


class TestRetrospectiveFlow:
    async def test_full_flow_to_completed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """录数据 → 写复盘 → 主管确认 → 已完成，复盘进博主档案。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            manager = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_FLOW"
            )
            svc = PromotionService(session)

            await svc.record_metrics(promo.id, _METRICS, pr, screenshot=_SHOT)
            submitted = await svc.submit_retrospective(
                promo.id,
                RetrospectiveSubmitRequest(content="数据一般，博主配合度高，建议二搭"),
                pr,
            )
            assert submitted.retro_status == RetroStatus.PENDING_CONFIRM.value
            assert submitted.retro_content == "数据一般，博主配合度高，建议二搭"

            # 未确认之前不该进博主档案
            archive = await svc.blogger_retrospectives(promo.blogger_id, manager)
            assert archive == []

            confirmed = await svc.confirm_retrospective(
                promo.id, RetrospectiveConfirmRequest(approve=True), manager
            )
            assert confirmed.retro_status == RetroStatus.COMPLETED.value
            assert confirmed.retro_confirmed_by == manager.id
            assert confirmed.retro_confirmed_at is not None

            archive = await svc.blogger_retrospectives(promo.blogger_id, manager)
            assert len(archive) == 1
            assert archive[0].content == "数据一般，博主配合度高，建议二搭"
            assert archive[0].confirmed_by == manager.id
            assert archive[0].created_by == pr.id
            assert archive[0].promotion_internal_code == promo.internal_code
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_keeps_old_record_and_allows_rewrite(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """打回退回待复盘；旧版留着，重写追加新行。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            manager = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_REJECT"
            )
            svc = PromotionService(session)
            await svc.record_metrics(promo.id, _METRICS, pr, screenshot=_SHOT)
            await svc.submit_retrospective(
                promo.id, RetrospectiveSubmitRequest(content="第一版，写得太敷衍"), pr
            )
            rejected = await svc.confirm_retrospective(
                promo.id,
                RetrospectiveConfirmRequest(approve=False, opinion="补充下二搭建议"),
                manager,
            )
            assert rejected.retro_status == RetroStatus.PENDING_RETRO.value
            assert rejected.retro_confirmed_at is None

            resubmitted = await svc.submit_retrospective(
                promo.id, RetrospectiveSubmitRequest(content="第二版，补了二搭建议"), pr
            )
            assert resubmitted.retro_status == RetroStatus.PENDING_CONFIRM.value
            # 「当前生效」是最新那条
            assert resubmitted.retro_content == "第二版，补了二搭建议"

            # 旧版没被删
            count = (
                await session.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM blogger_retrospective " "WHERE promotion_id = :p"
                    ),
                    {"p": promo.id},
                )
            ).scalar_one()
            assert count == 2

            await svc.confirm_retrospective(
                promo.id, RetrospectiveConfirmRequest(approve=True), manager
            )
            # 档案里只收确认过的那一条，被打回那版不进
            archive = await svc.blogger_retrospectives(promo.blogger_id, manager)
            assert len(archive) == 1
            assert archive[0].content == "第二版，补了二搭建议"
        finally:
            tenant_id_ctx.reset(token)

    async def test_self_confirm_forbidden(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """自己写的复盘自己批，主管这道关就没有意义。

        这条只能在 service 层挡 —— promotion.retro:confirm 的一级域是 promotion，
        PR 的 promotion.*:* 会被通配命中。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _paid_promo(
                promotion_factory, product_factory, blogger_factory, pr, code="RT_SELF"
            )
            svc = PromotionService(session)
            await svc.record_metrics(promo.id, _METRICS, pr, screenshot=_SHOT)
            await svc.submit_retrospective(
                promo.id, RetrospectiveSubmitRequest(content="自己写自己批"), pr
            )
            with pytest.raises(RetroSelfConfirmForbiddenError):
                await svc.confirm_retrospective(
                    promo.id, RetrospectiveConfirmRequest(approve=True), pr
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_requires_opinion(self) -> None:
        with pytest.raises(PydanticValidationError):
            RetrospectiveConfirmRequest(approve=False)

    async def test_blogger_archive_spans_promotions(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PRD：复盘跨单据伴随这个博主。两单复盘都要出现在同一个博主档案里。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            manager = await factory.user(tenant_a, roles=[admin_role])
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)

            for i, code in enumerate(["RT_SPAN_A", "RT_SPAN_B"]):
                style = await product_factory.style(style_code=code)
                promo = await promotion_factory.promotion(
                    style=style,
                    blogger=blogger,
                    pr=pr,
                    publish_status=PublishStatus.PUBLISHED.value,
                    settlement_status=SettlementStatus.PAID.value,
                )
                await svc.record_metrics(promo.id, _METRICS, pr, screenshot=_SHOT)
                await svc.submit_retrospective(
                    promo.id, RetrospectiveSubmitRequest(content=f"第 {i + 1} 次合作的复盘"), pr
                )
                await svc.confirm_retrospective(
                    promo.id, RetrospectiveConfirmRequest(approve=True), manager
                )

            archive = await svc.blogger_retrospectives(blogger.id, manager)
            assert len(archive) == 2
            # 倒序：最近的在最前
            assert archive[0].content == "第 2 次合作的复盘"
        finally:
            tenant_id_ctx.reset(token)


# 纯规则的状态机测试在 tests/unit/test_retro_state_machine.py ——
# 本模块的 pytestmark 带 asyncio，会把同步测试一起标上然后报 warning。
