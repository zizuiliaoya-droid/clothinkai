"""品牌词评论截图必传 + 金额变更时间线。

守的不变量：
- 没有品牌词评论截图发不了单（PRD 改动 5 的硬约束，后端拦不只靠前端）
- 截图上传不限状态（PR 可以被 publish 挡住之后再来补）
- 金额时间线只记**净变更**，``change_source`` 能区分「我改的」和「系统按模式压的」
- 时间线读权限走**字段级**判定 —— 运营持 ``promotion.*:read``，靠 scope 挡不住
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import CooperationMode, PublishStatus
from app.modules.promotion.exceptions import (
    BrandCommentScreenshotRequiredError,
    FieldPermissionDenied,
)
from app.modules.promotion.schemas import PromotionPublishRequest, PromotionUpdate
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _FakeS3:
    """只替掉要联网的三个调用，Attachment 行与 FK 都走真实路径。"""

    def put_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def delete_object(self, **_kw: Any) -> dict[str, Any]:
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture(autouse=True)
def _fake_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "_client", _FakeS3(), raising=False)


async def _promo(
    promotion_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    pr: Any,
    *,
    code: str,
    **kw: Any,
) -> Any:
    style = await product_factory.style(style_code=code)
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(style=style, blogger=blogger, pr=pr, **kw)


class TestBrandCommentGate:
    async def test_publish_without_screenshot_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PRD 改动 5 的硬约束。没有截图就不能提交发布审核。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="BC_NOIMG"
            )
            with pytest.raises(BrandCommentScreenshotRequiredError):
                await PromotionService(session).publish(
                    promo.id,
                    PromotionPublishRequest(
                        publish_url="https://www.xiaohongshu.com/explore/a",
                        actual_publish_date=get_today(),
                    ),
                    pr,
                )
            # 状态没动
            await session.refresh(promo)
            assert promo.publish_status == PublishStatus.UNPUBLISHED.value
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_then_publish_succeeds(
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
                promotion_factory, product_factory, blogger_factory, pr, code="BC_OK"
            )
            svc = PromotionService(session)
            uploaded = await svc.upload_brand_comment(
                promo.id,
                filename="comment.png",
                mime_type="image/png",
                data=_PNG,
                user=pr,
            )
            assert uploaded.brand_comment_attachment_id is not None
            assert uploaded.brand_comment_signed_url is not None

            published = await svc.publish(
                promo.id,
                PromotionPublishRequest(
                    publish_url="https://www.xiaohongshu.com/explore/b",
                    actual_publish_date=get_today(),
                ),
                pr,
            )
            assert published.publish_status == PublishStatus.PUBLISHED.value
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_is_state_agnostic(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """被 publish 挡住之后再来补图要能补上，不然流程走不通。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="BC_RETRY"
            )
            svc = PromotionService(session)
            with pytest.raises(BrandCommentScreenshotRequiredError):
                await svc.publish(
                    promo.id,
                    PromotionPublishRequest(
                        publish_url="https://www.xiaohongshu.com/explore/c",
                        actual_publish_date=get_today(),
                    ),
                    pr,
                )
            await svc.upload_brand_comment(
                promo.id, filename="c.png", mime_type="image/png", data=_PNG, user=pr
            )
            published = await svc.publish(
                promo.id,
                PromotionPublishRequest(
                    publish_url="https://www.xiaohongshu.com/explore/c",
                    actual_publish_date=get_today(),
                ),
                pr,
            )
            assert published.publish_status == PublishStatus.PUBLISHED.value
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
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="BC_FAKE"
            )
            with pytest.raises(BrandCommentScreenshotRequiredError):
                await PromotionService(session).upload_brand_comment(
                    promo.id,
                    filename="x.png",
                    mime_type="image/png",
                    data=b"not a png",
                    user=pr,
                )
        finally:
            tenant_id_ctx.reset(token)


class TestAmountLog:
    async def test_manual_edit_logged(
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
                code="AL_MANUAL",
                quote_amount=Decimal("500.00"),
            )
            svc = PromotionService(session)
            await svc.update_promotion(
                promo.id, PromotionUpdate(quote_amount=Decimal("800.00")), pr
            )

            logs = await svc.amount_log(promo.id, pr)
            assert len(logs) == 1
            assert logs[0].field_name == "quote_amount"
            assert logs[0].before_value == Decimal("500.00")
            assert logs[0].after_value == Decimal("800.00")
            assert logs[0].change_source == "手动编辑"
            assert logs[0].changed_by == pr.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_mode_enforce_is_distinguished_from_manual(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """这条是金额时间线存在的理由。

        置换模式下 ``_enforce_mode_costs`` 会把服务费静默压成 0。PR 传了 800，
        落库是 0 —— 没有 ``change_source`` 的话 PR 会以为是自己填错了。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="AL_ENFORCE",
                quote_amount=Decimal("500.00"),
                cooperation_mode=None,
            )
            # 先把模式定成置换（历史数据补一次），同时试图改服务费
            svc = PromotionService(session)
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(
                    cooperation_mode=CooperationMode.BARTER,
                    quote_amount=Decimal("800.00"),
                ),
                pr,
            )
            await session.refresh(promo)
            # 置换服务费恒为 0，前端传什么都被覆盖
            assert promo.quote_amount == Decimal("0.00")

            logs = await svc.amount_log(promo.id, pr)
            quote_logs = [c for c in logs if c.field_name == "quote_amount"]
            assert len(quote_logs) == 1
            assert quote_logs[0].before_value == Decimal("500.00")
            assert quote_logs[0].after_value == Decimal("0.00")
            # 传了 800 但落了 0 → 被规则改写，不是手动编辑
            assert quote_logs[0].change_source == "模式兜底"
        finally:
            tenant_id_ctx.reset(token)

    async def test_no_change_no_log(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """传了相同的值不该留一行 —— 时间线里全是噪音就没人看了。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="AL_NOOP",
                quote_amount=Decimal("500.00"),
            )
            svc = PromotionService(session)
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(quote_amount=Decimal("500.00"), remark="只改备注"),
                pr,
            )
            assert await svc.amount_log(promo.id, pr) == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_multiple_fields_each_get_a_row(
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
                code="AL_MULTI",
                quote_amount=Decimal("500.00"),
                cooperation_mode=CooperationMode.GIFT.value,
            )
            svc = PromotionService(session)
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(
                    quote_amount=Decimal("600.00"),
                    return_shipping_fee=Decimal("15.00"),
                ),
                pr,
            )
            logs = await svc.amount_log(promo.id, pr)
            fields = {c.field_name for c in logs}
            assert fields == {"quote_amount", "return_shipping_fee"}
            assert all(c.change_source == "手动编辑" for c in logs)
        finally:
            tenant_id_ctx.reset(token)

    async def test_read_gated_by_field_permission_not_scope(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        operations_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """核心断言：运营有 promotion.*:read，但看不到金额，所以也读不到金额时间线。

        这正是不给这张表新建 ``promotion.amount_log:read`` 的理由 —— ``has()`` 的
        前缀通配只看第一段，新 scope 会被 ``promotion.*:read`` 直接命中。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            ops = await factory.user(tenant_a, roles=[operations_role])
            promo = await _promo(
                promotion_factory,
                product_factory,
                blogger_factory,
                pr,
                code="AL_PERM",
                quote_amount=Decimal("500.00"),
            )
            svc = PromotionService(session)
            await svc.update_promotion(
                promo.id, PromotionUpdate(quote_amount=Decimal("900.00")), pr
            )
            # PR 能看
            assert len(await svc.amount_log(promo.id, pr)) == 1
            # 运营读不到
            with pytest.raises(FieldPermissionDenied):
                await svc.amount_log(promo.id, ops)
        finally:
            tenant_id_ctx.reset(token)

    async def test_log_row_requires_actual_change_at_db_level(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """CHECK 约束兜底：绕过 service 直接插一条「没变化」的记录应该被拒。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[admin_role])
            promo = await _promo(
                promotion_factory, product_factory, blogger_factory, pr, code="AL_CHECK"
            )
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError):
                await session.execute(
                    sa_text(
                        """
                        INSERT INTO promotion_amount_log
                          (id, tenant_id, promotion_id, field_name, before_value,
                           after_value, change_source, created_at, updated_at)
                        VALUES
                          (gen_random_uuid(), :t, :p, 'quote_amount', 500.00,
                           500.00, '手动编辑', NOW(), NOW())
                        """
                    ),
                    {"t": tenant_a.id, "p": promo.id},
                )
            await session.rollback()
        finally:
            tenant_id_ctx.reset(token)
