"""流程线 PR-2：推广单收件 / 发货（设计 3.3、4.6、7.3）。

本文件按条目逐步长：S5 收件三项（字段规则投影、写权限、电话规范化、``source_extra`` 退役键拒收）。
角色一律用 ``flow_users``（迁移 seed 的真实角色）。断言落库值时直接 SELECT，不看响应。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import (
    FieldPermissionDenied,
    InvalidReceiverPhoneError,
    PromotionNotFoundError,
    SourceExtraKeyRetiredError,
)
from app.modules.promotion.schemas import (
    PromotionCreate,
    PromotionListFilters,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13812345678",
    "receiver_address": "浙江省杭州市西湖区某路 1 号",
}


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _override(session: AsyncSession, tenant: Any, user: Any, scope: str, effect: str) -> None:
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=tenant.id, user_id=user.id, permission_id=perm.id, effect=effect
        )
    )
    await session.flush()


async def _db_receiver(session: AsyncSession, promotion_id: UUID) -> dict[str, Any]:
    row = (
        await session.execute(
            sa_text(
                "SELECT receiver_name, receiver_phone, receiver_address, source_extra "
                "FROM promotion WHERE id = :pid"
            ),
            {"pid": promotion_id},
        )
    ).one()
    return {
        "receiver_name": row[0],
        "receiver_phone": row[1],
        "receiver_address": row[2],
        "source_extra": dict(row[3]),
    }


async def _promotion(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any, **kw: Any
) -> Any:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr, **kw)


def _receiver_of(resp: Any) -> dict[str, Any]:
    return {k: getattr(resp, k) for k in _RECEIVER}


# ---------------------------------------------------------------------------
# 读：字段规则 promotion.receiver_*（可见 admin / pr / pr_manager / warehouse）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverRead:
    async def test_detail_projection_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)
        for who in ("pr", "pr_manager", "admin", "warehouse"):
            resp = await svc.get_promotion(promo.id, getattr(flow_users, who))
            assert _receiver_of(resp) == _RECEIVER, who
        # 财务看不到收货地址（Q4），运营看不到收件（4.6）
        for who in ("finance", "operations"):
            resp = await svc.get_promotion(promo.id, getattr(flow_users, who))
            assert _receiver_of(resp) == dict.fromkeys(_RECEIVER), who

    async def test_list_projection_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)

        async def _row(user: Any) -> Any:
            page = await svc.list_promotions(
                filters=PromotionListFilters(), page=1, page_size=100, user=user
            )
            return next(p for p in page.items if p.id == promo.id)

        assert _receiver_of(await _row(flow_users.pr_manager)) == _RECEIVER
        assert _receiver_of(await _row(flow_users.operations)) == dict.fromkeys(_RECEIVER)

    async def test_read_revoke_hides_only_that_field(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        await _override(
            session,
            flow_users.tenant,
            flow_users.pr,
            "field.promotion.receiver_phone:read",
            "revoke",
        )
        await _override(
            session,
            flow_users.tenant,
            flow_users.operations,
            "field.promotion.receiver_address:read",
            "grant",
        )
        svc = PromotionService(session)
        pr_view = await svc.get_promotion(promo.id, flow_users.pr)
        assert pr_view.receiver_phone is None
        assert pr_view.receiver_name == _RECEIVER["receiver_name"]
        ops_view = await svc.get_promotion(promo.id, flow_users.operations)
        assert ops_view.receiver_address == _RECEIVER["receiver_address"]
        assert ops_view.receiver_name is None


# ---------------------------------------------------------------------------
# 写：PATCH / POST 的收件三项
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestReceiverWrite:
    async def test_pr_and_manager_can_patch_and_phone_is_normalized(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        svc = PromotionService(session)

        resp = await svc.update_promotion(
            promo.id,
            PromotionUpdate(
                receiver_name="  李四 ",
                receiver_phone="+86 138-1234-5678",
                receiver_address=" 上海市某路 2 号 ",
            ),
            flow_users.pr,
        )
        expected = {
            "receiver_name": "李四",
            "receiver_phone": "13812345678",
            "receiver_address": "上海市某路 2 号",
        }
        assert _receiver_of(resp) == expected
        db = await _db_receiver(session, promo.id)
        assert {k: db[k] for k in _RECEIVER} == expected

        # 主管只改地址：没传的两项不动
        await svc.update_promotion(
            promo.id, PromotionUpdate(receiver_address="北京市某路 3 号"), flow_users.pr_manager
        )
        db = await _db_receiver(session, promo.id)
        assert db["receiver_address"] == "北京市某路 3 号"
        assert db["receiver_name"] == "李四"
        assert db["receiver_phone"] == "13812345678"

    async def test_blank_or_null_clears(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        svc = PromotionService(session)
        await svc.update_promotion(
            promo.id,
            PromotionUpdate(receiver_phone="   ", receiver_name=None),
            flow_users.pr,
        )
        db = await _db_receiver(session, promo.id)
        assert db["receiver_phone"] is None
        assert db["receiver_name"] is None
        assert db["receiver_address"] == _RECEIVER["receiver_address"]

    async def test_invalid_phone_rejected_and_nothing_written(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, **_RECEIVER
        )
        pid = promo.id
        with pytest.raises(InvalidReceiverPhoneError) as exc_info:
            await PromotionService(session).update_promotion(
                pid,
                PromotionUpdate(receiver_phone="1381234567", receiver_name="王五"),
                flow_users.pr,
            )
        assert exc_info.value.code == "INVALID_RECEIVER_PHONE"
        # 不 rollback（会连造的数一起回滚）：SELECT 前的 autoflush 会把改了一半的属性写下去
        db = await _db_receiver(session, pid)
        assert {k: db[k] for k in _RECEIVER} == _RECEIVER

    async def test_write_revoke_is_denied_with_all_fields(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        for field in ("receiver_name", "receiver_address"):
            await _override(
                session,
                flow_users.tenant,
                flow_users.pr,
                f"field.promotion.{field}:write",
                "revoke",
            )
        with pytest.raises(FieldPermissionDenied) as exc_info:
            await PromotionService(session).update_promotion(
                promo.id,
                PromotionUpdate(**_RECEIVER),
                flow_users.pr,
            )
        assert exc_info.value.fields == ("receiver_name", "receiver_address")

    async def test_create_with_receiver(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        svc = PromotionService(session)
        resp = await svc.create_promotion(
            PromotionCreate(
                cooperation_mode=CooperationMode.GIFT,
                style_id=style.id,
                blogger_id=blogger.id,
                platform="小红书",
                receiver_name="赵六",
                receiver_phone="0571-8888888",
                receiver_address="杭州市某路 4 号",
            ),
            flow_users.pr_manager,
        )
        db = await _db_receiver(session, resp.id)
        assert db["receiver_name"] == "赵六"
        assert db["receiver_phone"] == "05718888888"
        assert db["receiver_address"] == "杭州市某路 4 号"

        with pytest.raises(InvalidReceiverPhoneError):
            await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    receiver_phone="12345",
                ),
                flow_users.pr_manager,
            )


# ---------------------------------------------------------------------------
# source_extra 退役键：「打单地址」「发货单号」M1 后是 typed 列（7.1 SOURCE_EXTRA_KEY_RETIRED）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestRetiredSourceExtraKeys:
    @pytest.mark.parametrize(
        ("patch", "keys"),
        [
            ({"打单地址": "杭州"}, ["打单地址"]),
            ({"发货单号": "SF1", "订单号": "TB1"}, ["发货单号"]),
            # 删键也不行：键本身退役了
            ({"打单地址": None, "发货单号": ""}, ["打单地址", "发货单号"]),
        ],
    )
    async def test_patch_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        patch: dict[str, Any],
        keys: list[str],
    ) -> None:
        promo = await _promotion(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            source_extra={"订单号": "TB0"},
        )
        pid = promo.id
        with pytest.raises(SourceExtraKeyRetiredError) as exc_info:
            await PromotionService(session).update_promotion(
                pid, PromotionUpdate(source_extra=patch), flow_users.pr
            )
        err = exc_info.value
        assert (err.code, err.status_code, err.details) == (
            "SOURCE_EXTRA_KEY_RETIRED",
            422,
            {"keys": keys},
        )
        assert (await _db_receiver(session, pid))["source_extra"] == {"订单号": "TB0"}

    async def test_patch_checks_retired_before_field_permission_and_phone(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """顺序（细化 §7）：404 → 退役键 → 写权限 → 其余校验。"""
        promo = await _promotion(flow_users, product_factory, blogger_factory, promotion_factory)
        await _override(
            session,
            flow_users.tenant,
            flow_users.pr,
            "field.promotion.receiver_name:write",
            "revoke",
        )
        svc = PromotionService(session)
        payload = PromotionUpdate(
            source_extra={"打单地址": "杭州"}, receiver_name="甲", receiver_phone="x"
        )
        with pytest.raises(SourceExtraKeyRetiredError):
            await svc.update_promotion(promo.id, payload, flow_users.pr)
        with pytest.raises(PromotionNotFoundError):
            await svc.update_promotion(uuid4(), payload, flow_users.pr)
        # 权限先于电话格式
        with pytest.raises(FieldPermissionDenied):
            await svc.update_promotion(
                promo.id,
                PromotionUpdate(receiver_name="甲", receiver_phone="x"),
                flow_users.pr,
            )

    async def test_create_rejected_first(
        self,
        session: AsyncSession,
        flow_users: Any,
        blogger_factory: Any,
    ) -> None:
        """POST 最先判：款式不存在也先报退役键。"""
        blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
        with pytest.raises(SourceExtraKeyRetiredError) as exc_info:
            await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=uuid4(),
                    blogger_id=blogger.id,
                    platform="小红书",
                    source_extra={"订单号": "TB1", "发货单号": "SF1"},
                ),
                flow_users.pr_manager,
            )
        assert exc_info.value.details == {"keys": ["发货单号"]}
