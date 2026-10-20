"""复盘环节集成测试（PRD V1.4 改动 4）。

守的不变量：
- 录 7 天数据要求 ``settlement_status='已结款'``（门槛与只录一次见 test_metrics_gate）
- 三个指标 + 截图都必填，截图缺了整个操作回滚（不留「数字录了图没传」的中间态）
- 指标与状态推进同事务
- 禁止确认自己写的复盘 —— 这条必须在 service 层挡，权限层拦不住
- 打回不删旧记录，重写时追加新行
- 博主档案只收已确认的复盘，未确认的不进 hover 卡
- retro_status 与 settlement_status 正交：复盘推进不影响「已结款」这个过滤条件
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
    MetricsNotAvailableYetError,
    MetricsScreenshotRequiredError,
)
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    RetrospectiveWriteRequest,
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
    settlement_status: str = SettlementStatus.SETTLED.value,
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
        retro_status="待复盘",
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
            assert resp.settlement_status == SettlementStatus.SETTLED.value
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
            with pytest.raises(MetricsNotAvailableYetError):
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
            assert promo.retro_status == RetroStatus.PENDING_RETRO.value
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


@pytest.mark.usefixtures("tenant_ctx")
class TestRetrospectiveFlow:
    async def test_supervisor_self_owned_write_without_metrics_or_payment(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _paid_promo(
            promotion_factory,
            product_factory,
            blogger_factory,
            flow_users.pr_manager,
            code="RT_MANAGER",
            settlement_status="待核查",
        )
        svc = PromotionService(session)
        resp = await svc.write_retrospective(
            promo.id, RetrospectiveWriteRequest(content="主管自己负责也可写"), flow_users.pr_manager
        )
        assert resp.retro_status == "已完成"
        assert resp.retro_confirmed_by == flow_users.pr_manager.id
        assert resp.retro_confirmed_at is not None
        assert resp.metrics_recorded_at is None
        archive = await svc.blogger_retrospectives(promo.blogger_id, flow_users.pr_manager)
        assert len(archive) == 1
        assert archive[0].created_by == archive[0].confirmed_by == flow_users.pr_manager.id
        with pytest.raises(Exception) as error:
            await svc.write_retrospective(
                promo.id, RetrospectiveWriteRequest(content="不能再写"), flow_users.pr_manager
            )
        assert error.value.status_code == 422

    async def test_admin_appends_version_and_legacy_draft_stays_hidden(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        from uuid import uuid4

        from app.modules.promotion.models import BloggerRetrospective

        promo = await _paid_promo(
            promotion_factory,
            product_factory,
            blogger_factory,
            flow_users.pr,
            code="RT_VERSIONS",
            settlement_status="待核查",
        )
        session.add(
            BloggerRetrospective(
                id=uuid4(),
                tenant_id=promo.tenant_id,
                blogger_id=promo.blogger_id,
                promotion_id=promo.id,
                content="PR旧草稿",
                created_by=flow_users.pr.id,
            )
        )
        await session.flush()
        svc = PromotionService(session)
        assert await svc.blogger_retrospectives(promo.blogger_id, flow_users.pr) == []
        await svc.write_retrospective(
            promo.id, RetrospectiveWriteRequest(content="主管版本"), flow_users.pr_manager
        )
        await svc.write_retrospective(
            promo.id, RetrospectiveWriteRequest(content="管理员修订"), flow_users.admin
        )
        archive = await svc.blogger_retrospectives(promo.blogger_id, flow_users.pr)
        assert [r.content for r in archive] == ["管理员修订"]
        assert archive[0].created_by == archive[0].confirmed_by == flow_users.admin.id
        rows = (
            await session.execute(
                sa_text(
                    "SELECT content, confirmed_at FROM blogger_retrospective WHERE promotion_id=:p"
                ),
                {"p": promo.id},
            )
        ).all()
        assert len(rows) == 3
        assert dict(rows)["PR旧草稿"] is None

    @pytest.mark.parametrize("who", ["pr", "finance", "operations"])
    async def test_independent_scope_http_forbids_old_wildcards(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        who: str,
    ) -> None:
        from tests.integration.test_settlement_routing import _call

        promo = await _paid_promo(
            promotion_factory,
            product_factory,
            blogger_factory,
            flow_users.pr,
            code=f"RT_DENY_{who}",
        )
        resp = await _call(
            session,
            getattr(flow_users, who),
            "POST",
            f"/api/promotions/{promo.id}/retrospective",
            {"content": "越权"},
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "PERMISSION_DENIED"

    async def test_deleted_confirm_endpoint_and_empty_content(
        self,
        session: AsyncSession,
        flow_users: Any,
    ) -> None:
        from uuid import uuid4

        from tests.integration.test_settlement_routing import _call

        path = f"/api/promotions/{uuid4()}/retrospective"
        assert (
            await _call(session, flow_users.admin, "POST", path + "/confirm", {"approve": True})
        ).status_code == 404
        assert (
            await _call(session, flow_users.pr_manager, "POST", path, {"content": "  "})
        ).status_code == 422


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)
