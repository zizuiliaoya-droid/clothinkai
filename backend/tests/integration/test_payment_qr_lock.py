"""收款码按阶段锁定与字段规则（流程线 PR-6 S9；设计 7.3 收款码接口、4.6、9.3 D 块「收款码不在发布卡、按阶段锁」）。

- 发布不卡收款码（D3，Q14 修正）：没码照样能发布
- PR / 主管按顺序判：① 没发布 → 422「发布后上传」；①' 已停用 / 已删除 / 不合作 / 状态异常 → 422「只读」；
  ② 结款单待财务付款 → 409 ``PAYMENT_QR_LOCKED``（``settlement_paying``）；③ 推广单已付款 / 已结款 → 409（``settled``）；
  D 列、结款单待付款、结款驳回、召回中放行；召回中 / R33 已驳回可跨已结款，待财务付款仍锁
- 持 ``*`` 的管理员只受 ①；结款单已生成后 DELETE 一律 409（``settlement_created``，管理员也一样）
- 四个接口（upload-init / upload / PUT / DELETE）同一套锁；每格与矩阵 ``fields.payment_qr`` 一致（改 ⇔ 放行）
- 财务能看到收款码签名（字段规则 ``promotion.payment_qr``），运营看不到
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, PermissionDeniedError
from app.modules.promotion.schemas import (
    PromotionPaymentQrBindRequest,
    PromotionPaymentQrUploadInitRequest,
    PromotionPublishRequest,
)
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.integration.test_settlement_routing import _call, _FakeS3

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_OPS = ("init", "upload", "bind", "delete")


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _promo(
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
    *,
    settlement: str | None = None,
    **kw: Any,
) -> Any:
    """默认已发布、待核查、有收款码；``settlement`` 给了就同时建一张该状态的结款单。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("publish_status", "已发布")
    kw.setdefault("settlement_status", "待核查")
    kw.setdefault("actual_publish_date", get_today() - timedelta(days=3))
    kw.setdefault("payment_qr", True)
    promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr, **kw)
    if settlement is not None:
        await settlement_factory.settlement(
            style=style,
            blogger=blogger,
            promotion=promo,
            pr=flow_users.pr,
            settlement_status=settlement,
        )
    return promo


async def _qr_of(session: AsyncSession, promotion_id: UUID) -> Any:
    return (
        await session.execute(
            text("SELECT payment_qr_attachment_id FROM promotion WHERE id = CAST(:p AS uuid)"),
            {"p": promotion_id},
        )
    ).scalar_one()


async def _do(svc: PromotionService, op: str, promo: Any, user: Any, qr_id: Any) -> None:
    if op == "init":
        await svc.init_payment_qr_upload(
            promo.id,
            PromotionPaymentQrUploadInitRequest(
                filename="qr.png", mime_type="image/png", size_bytes=len(_PNG)
            ),
            user,
        )
    elif op == "upload":
        await svc.upload_payment_qr(
            promo.id, filename="qr.png", mime_type="image/png", data=_PNG, user=user
        )
    elif op == "bind":
        await svc.bind_payment_qr(
            promo.id, PromotionPaymentQrBindRequest(payment_qr_attachment_id=qr_id), user
        )
    else:
        await svc.remove_payment_qr(promo.id, user)


async def _matrix_state(svc: PromotionService, promotion_id: UUID, user: Any) -> str | None:
    """这个用户看这张单 ``ui.fields.payment_qr`` 的状态（隐 = None）。停用 / 删除的单也取得到。"""
    promotion = await svc._repo.get_by_id(promotion_id, include_inactive=True)
    assert promotion is not None
    await svc._session.refresh(promotion)
    resp = await svc._to_response(promotion, user)
    assert resp.ui is not None
    field = resp.ui["fields"].get("payment_qr")
    return field["state"] if field is not None else None


# (id, 推广单 kwargs, 结款单状态, PR / 主管的结果)：结果 = None 放行 / (状态码, 错误码, lock 或 None)
_ILLEGAL = (422, "ILLEGAL_STATE_TRANSITION", None)
_PAYING = (409, "PAYMENT_QR_LOCKED", "settlement_paying")
_SETTLED = (409, "PAYMENT_QR_LOCKED", "settled")
_CASES: list[tuple[str, dict[str, Any], str | None, Any]] = [
    ("未发布", {"publish_status": "未发布", "settlement_status": "未核查"}, None, _ILLEGAL),
    ("异常", {"publish_status": "异常", "settlement_status": "未核查"}, None, _ILLEGAL),
    ("待主管审核", {}, None, None),
    ("推广驳回", {"settlement_status": "已驳回"}, None, None),
    ("待财务付款·结款单待付款", {"settlement_status": "待付款"}, "待付款", None),
    ("待财务付款·结款单待财务付款", {"settlement_status": "待付款"}, "待财务付款", _PAYING),
    ("结款驳回", {"settlement_status": "结款驳回"}, "已驳回", None),
    ("待传结款截图", {"settlement_status": "已付款"}, "已付款", _SETTLED),
    ("已结款", {"settlement_status": "已结款"}, "已付款", _SETTLED),
    ("召回中", {"recall_status": "召回中"}, None, None),
    (
        "召回中·结款单待财务付款",
        {"recall_status": "召回中", "settlement_status": "待付款"},
        "待财务付款",
        _PAYING,
    ),
    (
        "召回中·已结款",
        {"recall_status": "召回中", "settlement_status": "已结款"},
        "已付款",
        None,
    ),
    ("已停用", {"is_active": False}, None, _ILLEGAL),
    ("已删除", {"publish_status": "已删除"}, None, _ILLEGAL),
    (
        "已完结·不合作",
        {"publish_status": "已取消", "settlement_status": "未核查"},
        None,
        _ILLEGAL,
    ),
    (
        "已完结·召回",
        {
            "publish_status": "已取消",
            "recall_status": "召回成功",
            "settlement_status": "未核查",
        },
        None,
        _ILLEGAL,
    ),
    ("状态异常", {"settlement_status": "未核查"}, None, _ILLEGAL),
]
# 管理员只受 ①
_ADMIN_BLOCKED = {"未发布", "异常"}


def _assert_error(exc: AppException, expected: tuple[int, str, str | None], case: str) -> None:
    status_code, code, lock = expected
    assert (exc.status_code, exc.code) == (status_code, code), case
    assert exc.details.get("lock") == lock, case
    if code == "ILLEGAL_STATE_TRANSITION":
        assert exc.message in {"发布后上传", "已停用 / 已取消，只读"}, case


@pytest.mark.usefixtures("tenant_ctx")
class TestPublishNoQrGate:
    async def test_publish_without_qr(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """D3：没码照样能发布（Q14 修正），发布后才出上传入口。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            publish_status="未发布",
            settlement_status="未核查",
            actual_publish_date=None,
            payment_qr=False,
            brand_comment=True,
        )
        resp = await PromotionService(session).publish(
            promo.id,
            PromotionPublishRequest(
                publish_url="https://www.xiaohongshu.com/explore/abc",
                actual_publish_date=get_today(),
            ),
            flow_users.pr,
        )
        assert (resp.publish_status, resp.settlement_status) == ("已发布", "待核查")
        assert resp.payment_qr_attachment_id is None
        assert resp.ui is not None and resp.ui["fields"]["payment_qr"]["state"] == "edit"


@pytest.mark.usefixtures("tenant_ctx")
class TestLockByStage:
    @pytest.mark.parametrize("who", ["pr", "pr_manager"])
    @pytest.mark.parametrize(
        ("case", "promo_kw", "settlement", "expected"), _CASES, ids=[c[0] for c in _CASES]
    )
    async def test_pr_and_manager(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        who: str,
        case: str,
        promo_kw: dict[str, Any],
        settlement: str | None,
        expected: Any,
    ) -> None:
        """每格的结果与矩阵 ``fields.payment_qr`` 一致：放行 ⇔ 「改」。"""
        user = getattr(flow_users, who)
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement=settlement,
            **promo_kw,
        )
        pid, old_qr = promo.id, promo.payment_qr_attachment_id
        svc = PromotionService(session)
        if expected is None:
            resp = await svc.upload_payment_qr(
                pid, filename="qr.png", mime_type="image/png", data=_PNG, user=user
            )
            assert resp.payment_qr_attachment_id not in (None, old_qr), case
            assert len(fake_r2.puts) == 1
            assert await _matrix_state(svc, pid, user) == "edit", case
        else:
            with pytest.raises(AppException) as exc:
                await svc.upload_payment_qr(
                    pid, filename="qr.png", mime_type="image/png", data=_PNG, user=user
                )
            _assert_error(exc.value, expected, case)
            assert await _qr_of(session, pid) == old_qr
            assert fake_r2.puts == []
            assert await _matrix_state(svc, pid, user) != "edit", case

    @pytest.mark.parametrize(
        ("case", "promo_kw", "settlement", "expected"), _CASES, ids=[c[0] for c in _CASES]
    )
    async def test_admin_only_before_publish(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        case: str,
        promo_kw: dict[str, Any],
        settlement: str | None,
        expected: Any,
    ) -> None:
        """持 ``*`` 只受 ①：两种锁都能换，停用 / 删除 / 不合作的单也能换。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement=settlement,
            **promo_kw,
        )
        pid = promo.id
        svc = PromotionService(session)
        if case in _ADMIN_BLOCKED:
            with pytest.raises(AppException) as exc:
                await svc.upload_payment_qr(
                    pid, filename="qr.png", mime_type="image/png", data=_PNG, user=flow_users.admin
                )
            _assert_error(exc.value, _ILLEGAL, case)
            assert exc.value.message == "发布后上传"
            assert await _matrix_state(svc, pid, flow_users.admin) != "edit", case
        else:
            resp = await svc.upload_payment_qr(
                pid, filename="qr.png", mime_type="image/png", data=_PNG, user=flow_users.admin
            )
            assert resp.payment_qr_attachment_id is not None, case
            assert await _matrix_state(svc, pid, flow_users.admin) == "edit", case


@pytest.mark.usefixtures("tenant_ctx")
class TestFourEndpoints:
    @pytest.mark.parametrize("op", _OPS)
    async def test_paying_locks_all_four(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        op: str,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="待付款",
            settlement="待财务付款",
        )
        old_qr = promo.payment_qr_attachment_id
        with pytest.raises(AppException) as exc:
            await _do(PromotionService(session), op, promo, flow_users.pr, old_qr)
        _assert_error(exc.value, _PAYING, op)
        assert await _qr_of(session, promo.id) == old_qr
        assert fake_r2.puts == []

    @pytest.mark.parametrize("op", _OPS)
    async def test_before_publish_blocks_admin_on_all_four(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        op: str,
    ) -> None:
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            publish_status="未发布",
            settlement_status="未核查",
        )
        old_qr = promo.payment_qr_attachment_id
        with pytest.raises(AppException) as exc:
            await _do(PromotionService(session), op, promo, flow_users.admin, old_qr)
        _assert_error(exc.value, _ILLEGAL, op)
        assert await _qr_of(session, promo.id) == old_qr

    @pytest.mark.parametrize("op", _OPS)
    async def test_inactive_is_readonly_not_404(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        op: str,
    ) -> None:
        """①'：PR 给已停用的单传 / 换 / 删收款码 → 422「只读」（不是 404）。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            is_active=False,
        )
        old_qr = promo.payment_qr_attachment_id
        with pytest.raises(AppException) as exc:
            await _do(PromotionService(session), op, promo, flow_users.pr, old_qr)
        _assert_error(exc.value, _ILLEGAL, op)
        assert exc.value.message == "已停用 / 已取消，只读"
        assert await _qr_of(session, promo.id) == old_qr

    @pytest.mark.parametrize("op", ["init", "bind"])
    async def test_open_stage_init_and_bind(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
        op: str,
    ) -> None:
        """结款驳回时 PR 能重传（init / PUT 都放行）。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="结款驳回",
            settlement="已驳回",
        )
        old_qr = promo.payment_qr_attachment_id
        await _do(PromotionService(session), op, promo, flow_users.pr, old_qr)
        assert await _qr_of(session, promo.id) == old_qr


@pytest.mark.usefixtures("tenant_ctx")
class TestDelete:
    async def test_delete_without_settlement(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        promo = await _promo(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        await PromotionService(session).remove_payment_qr(promo.id, flow_users.pr)
        assert await _qr_of(session, promo.id) is None

    @pytest.mark.parametrize(
        ("who", "promo_status", "settlement"),
        [
            ("pr", "待付款", "待付款"),
            ("pr", "结款驳回", "已驳回"),
            ("admin", "待付款", "待财务付款"),
            ("admin", "已结款", "已付款"),
        ],
    )
    async def test_delete_after_settlement_created(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        who: str,
        promo_status: str,
        settlement: str,
    ) -> None:
        """结款单已生成后 DELETE 拒绝：PR 在能换码的阶段也不能删；管理员同样拒。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status=promo_status,
            settlement=settlement,
        )
        old_qr = promo.payment_qr_attachment_id
        with pytest.raises(AppException) as exc:
            await PromotionService(session).remove_payment_qr(promo.id, getattr(flow_users, who))
        assert (exc.value.status_code, exc.value.code) == (409, "PAYMENT_QR_LOCKED")
        assert exc.value.details["lock"] == "settlement_created"
        assert exc.value.details["settlement_status"] == settlement
        assert await _qr_of(session, promo.id) == old_qr


@pytest.mark.usefixtures("tenant_ctx")
class TestVisibilityAndRoute:
    async def test_finance_sees_qr_operations_do_not(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """字段规则 promotion.payment_qr 含财务（7d-3）；运营不含。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="待付款",
            settlement="待付款",
        )
        svc = PromotionService(session)
        # 财务字段规则允许签名，不等于允许进入完整推广详情。保留两层独立断言。
        with pytest.raises(PermissionDeniedError) as denied:
            await svc.get_promotion(promo.id, flow_users.finance)
        assert denied.value.details["required_scope"] == "promotion"
        for who in ("finance", "pr", "pr_manager", "admin"):
            user = getattr(flow_users, who)
            resp = (
                await svc._to_response(promo, user)
                if who == "finance"
                else await svc.get_promotion(promo.id, user)
            )
            assert resp.payment_qr_attachment_id == promo.payment_qr_attachment_id, who
            assert resp.payment_qr_signed_url is not None, who
        resp = await svc.get_promotion(promo.id, flow_users.operations)
        assert (resp.payment_qr_attachment_id, resp.payment_qr_signed_url) == (None, None)

    async def test_route_returns_lock(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """路由：PR 在结款单待财务付款时换码 → 409 PAYMENT_QR_LOCKED，details.lock = settlement_paying。"""
        promo = await _promo(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status="待付款",
            settlement="待财务付款",
        )
        resp = await _call(
            session,
            flow_users.pr,
            "POST",
            f"/api/promotions/{promo.id}/payment-qr/upload",
            files={"file": ("qr.png", _PNG, "image/png")},
        )
        assert (resp.status_code, resp.json()["code"]) == (409, "PAYMENT_QR_LOCKED"), resp.text
        assert resp.json()["details"]["lock"] == "settlement_paying"
        assert fake_r2.puts == []
