"""录 7 天数据的门槛与只录一次（流程线 PR-6 S10；设计 3.7、7.3 metrics 行、9.3「7 天门槛」行）。

- 门槛：推广单结款状态 = 已结款，且 ``get_today() ≥ 发布日 + 7``（北京时间）；不满足 → 422
  ``METRICS_NOT_AVAILABLE_YET``（``blocked_by`` ∈ settlement / seven_days，带 ``available_from``），状态不变、不传对象
- 顺序：未录过 → 已结款 → 满 7 天 → 矩阵（阶段 + 权限）→ 三指标 + 截图；没有发布日不拦 7 天（D8）
- 只录一次：``record_metrics_once`` 条件 UPDATE（``metrics_recorded_at IS NULL``）；录过再录 → 422，
  条件 UPDATE 落空 → 409 且删掉已传的对象；两条独立连接同时录 → 恰 1 个成功
- 过渡期：录完复盘 未开始 → 待复盘（PR-7 改）；路由挂 ``promotion.metrics:write``（PR 成功，财务、运营 403）
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from freezegun import freeze_time
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionMetricsRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.concurrency import committed, default_tenant_id, run_concurrently
from tests.conftest import purge_promotions
from tests.flow_event_helpers import events_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_SHOT = ("m.png", "image/png", _PNG)
_METRICS = PromotionMetricsRequest(like_count=120, collect_count=30, comment_count=8)
# UTC 5-26 16:30 = 北京 5-27 00:30：容器的 date.today() 还是 5-26
_FROZEN_UTC = "2026-05-26 16:30:00"
_BEIJING_TODAY = date(2026, 5, 27)


class _FakeS3:
    """只替掉要联网的三个调用，记下传了 / 删了哪些 key。"""

    def __init__(self) -> None:
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def put_object(self, **kw: Any) -> dict[str, Any]:
        self.puts.append(kw["Key"])
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture
def fake_r2(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _call(
    session: AsyncSession,
    user: Any,
    method: str,
    path: str,
    *,
    data: Any = None,
    files: Any = None,
) -> Any:
    """真实路由 + 用户的有效权限（同 ``test_settlement_routing._call``，多一个表单 ``data``）。"""
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, data=data, files=files)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def _published(
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    **kw: Any,
) -> Any:
    """已发布的推广单；默认已结款、发布日在 10 天前（满 7 天）。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    kw.setdefault("actual_publish_date", get_today() - timedelta(days=10))
    kw.setdefault("settlement_status", "已结款")
    return await promotion_factory.promotion(
        style=style, blogger=blogger, pr=flow_users.pr, publish_status="已发布", **kw
    )


async def _metrics_state(session: AsyncSession, promotion_id: UUID) -> tuple[Any, ...]:
    row = (
        await session.execute(
            text(
                "SELECT like_count, collect_count, comment_count, metrics_attachment_id, "
                "metrics_recorded_at, retro_status FROM promotion WHERE id = CAST(:p AS uuid)"
            ),
            {"p": promotion_id},
        )
    ).one()
    return tuple(row)


def _untouched(retro: str = "未开始") -> tuple[Any, ...]:
    return (None, None, None, None, None, retro)


@pytest.mark.usefixtures("tenant_ctx")
class TestGate:
    @pytest.mark.parametrize("status", ["待核查", "待付款", "结款驳回", "已付款"])
    async def test_not_settled_blocked(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
        status: str,
    ) -> None:
        """满 7 天但还没到已结款（含 已付款 = 待传结款截图）→ 422 ``blocked_by = settlement``。"""
        publish = get_today() - timedelta(days=10)
        promo = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            actual_publish_date=publish,
            settlement_status=status,
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).record_metrics(
                promo.id, _METRICS, flow_users.pr, screenshot=_SHOT
            )
        assert (exc.value.status_code, exc.value.code) == (422, "METRICS_NOT_AVAILABLE_YET")
        assert exc.value.details["blocked_by"] == "settlement"
        assert exc.value.details["available_from"] == (publish + timedelta(days=7)).isoformat()
        assert await _metrics_state(session, promo.id) == _untouched()
        assert fake_r2.puts == []

    @freeze_time(_FROZEN_UTC)
    async def test_day_six_blocked_beijing_midnight(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """已结款、发布后第 6 天（北京 5-27，发布日 5-21）→ 422 ``seven_days``，提示 5-28 起可录。"""
        assert get_today() == _BEIJING_TODAY and date.today() != _BEIJING_TODAY, "场景有效性"
        promo = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            actual_publish_date=_BEIJING_TODAY - timedelta(days=6),
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).record_metrics(
                promo.id, _METRICS, flow_users.pr, screenshot=_SHOT
            )
        assert (exc.value.status_code, exc.value.code) == (422, "METRICS_NOT_AVAILABLE_YET")
        assert exc.value.details["blocked_by"] == "seven_days"
        assert exc.value.details["available_from"] == "2026-05-28"
        assert "2026-05-28 起可以录" in exc.value.message
        assert await _metrics_state(session, promo.id) == _untouched()
        assert fake_r2.puts == []

    @freeze_time(_FROZEN_UTC)
    async def test_day_seven_succeeds_beijing_midnight(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """发布后第 7 天按北京时间算：容器 UTC 还在第 6 天也能录（用 ``date.today()`` 会红）。"""
        assert get_today() == _BEIJING_TODAY and date.today() != _BEIJING_TODAY, "场景有效性"
        promo = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            actual_publish_date=_BEIJING_TODAY - timedelta(days=7),
        )
        resp = await PromotionService(session).record_metrics(
            promo.id, _METRICS, flow_users.pr, screenshot=_SHOT
        )
        assert resp.metrics_recorded_at is not None
        assert (resp.like_count, resp.collect_count, resp.comment_count) == (120, 30, 8)
        assert len(fake_r2.puts) == 1

    async def test_no_publish_date_not_blocked(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """存量没有发布日的已结款单：7 天门槛不拦（D8，与阶段 #14 同口径）。"""
        promo = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            actual_publish_date=None,
        )
        resp = await PromotionService(session).record_metrics(
            promo.id, _METRICS, flow_users.pr, screenshot=_SHOT
        )
        assert resp.metrics_recorded_at is not None

    async def test_success_writes_once_and_event(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """录成功：三指标、截图、``metrics_recorded_at``；过渡期复盘 未开始 → 待复盘；
        时间线一条 ``promotion.metrics_recorded``（附件 = 截图）；可录角标与录数按钮跟着消失。"""
        promo = await _published(flow_users, product_factory, blogger_factory, promotion_factory)
        pid, line_id = promo.id, promo.line_id
        svc = PromotionService(session)
        before = await svc.get_promotion(pid, flow_users.pr)
        assert "metrics_due" in before.badges, "场景有效：已结款、满 7 天、未录"
        assert before.ui is not None and before.ui["actions"]["metrics"]["state"] == "enabled"

        resp = await svc.record_metrics(pid, _METRICS, flow_users.pr, screenshot=_SHOT)
        like, collect, comment, att_id, recorded_at, retro = await _metrics_state(session, pid)
        assert (like, collect, comment, retro) == (120, 30, 8, "待复盘")
        assert att_id is not None and recorded_at is not None
        assert resp.retro_status == "待复盘"
        assert "metrics_due" not in resp.badges
        assert resp.ui is not None and "metrics" not in resp.ui["actions"]
        recorded = await events_of(
            session, line_id=line_id, event_type=LineEventType.PROMOTION_METRICS_RECORDED
        )
        assert [(e.from_state, e.to_state, e.attachment_id) for e in recorded] == [
            ("未开始", "待复盘", att_id)
        ]

    async def test_recalling_stage_illegal(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """门槛都满足、但阶段是「召回中」（G 列，矩阵里录数是隐）→ ``require`` 按状态机拒绝 422。"""
        promo = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            recall_status="召回中",
        )
        with pytest.raises(AppException) as exc:
            await PromotionService(session).record_metrics(
                promo.id, _METRICS, flow_users.pr, screenshot=_SHOT
            )
        assert (exc.value.status_code, exc.value.code) == (422, "ILLEGAL_STATE_TRANSITION")
        assert exc.value.details["from_state"] == "召回中"
        assert await _metrics_state(session, promo.id) == _untouched()
        assert fake_r2.puts == []

    async def test_second_time_rejected(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """录过再录（管理员也一样）→ 422 ``ILLEGAL_STATE_TRANSITION``，数字与截图不变、不再传对象。"""
        promo = await _published(flow_users, product_factory, blogger_factory, promotion_factory)
        pid = promo.id
        svc = PromotionService(session)
        await svc.record_metrics(pid, _METRICS, flow_users.pr, screenshot=_SHOT)
        first = await _metrics_state(session, pid)
        for who in ("pr", "admin"):
            with pytest.raises(AppException) as exc:
                await svc.record_metrics(
                    pid,
                    PromotionMetricsRequest(like_count=999, collect_count=1, comment_count=1),
                    getattr(flow_users, who),
                    screenshot=_SHOT,
                )
            assert (exc.value.status_code, exc.value.code) == (422, "ILLEGAL_STATE_TRANSITION"), who
        assert await _metrics_state(session, pid) == first
        assert len(fake_r2.puts) == 1

    async def test_conditional_update_lost_409_deletes_object(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """条件 UPDATE 落空（别人刚录完）→ 409，附件行回滚、已传的对象补偿删除。"""
        promo = await _published(flow_users, product_factory, blogger_factory, promotion_factory)
        pid = promo.id
        await session.commit()

        async def _lost(self: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr(PromotionRepository, "record_metrics_once", _lost)
        with pytest.raises(AppException) as exc:
            await PromotionService(session).record_metrics(
                pid, _METRICS, flow_users.pr, screenshot=_SHOT
            )
        assert exc.value.status_code == 409
        assert len(fake_r2.puts) == 1, "场景有效：对象已经传上去了"
        assert fake_r2.deletes == fake_r2.puts
        assert await _metrics_state(session, pid) == _untouched()


@pytest.mark.usefixtures("tenant_ctx")
class TestRoute:
    async def test_scope_and_gate_over_http(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        fake_r2: _FakeS3,
    ) -> None:
        """路由挂 ``promotion.metrics:write``：财务、运营 403；PR 未满 7 天 422 带可录日期，满了成功。"""
        form = {"like_count": "120", "collect_count": "30", "comment_count": "8"}

        def _files() -> dict[str, Any]:
            return {"screenshot": ("m.png", _PNG, "image/png")}

        early = await _published(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            actual_publish_date=get_today() - timedelta(days=3),
        )
        resp = await _call(
            session,
            flow_users.pr,
            "POST",
            f"/api/promotions/{early.id}/metrics",
            data=form,
            files=_files(),
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "METRICS_NOT_AVAILABLE_YET")
        details = resp.json()["details"]
        assert details["blocked_by"] == "seven_days"
        assert details["available_from"] == (get_today() + timedelta(days=4)).isoformat()

        promo = await _published(flow_users, product_factory, blogger_factory, promotion_factory)
        path = f"/api/promotions/{promo.id}/metrics"
        for who in ("finance", "operations"):
            resp = await _call(
                session, getattr(flow_users, who), "POST", path, data=form, files=_files()
            )
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), who
        assert await _metrics_state(session, promo.id) == _untouched()
        assert fake_r2.puts == []

        resp = await _call(session, flow_users.pr, "POST", path, data=form, files=_files())
        assert resp.status_code == 200, resp.text
        assert resp.json()["like_count"] == 120


@pytest.mark.integration
@pytest.mark.asyncio
class TestRecordOnceConcurrent:
    """两条独立连接同时录同一单 → 恰 1 个成功（``metrics_recorded_at IS NULL`` 守住）。"""

    async def test_two_connections_only_one_succeeds(self, engine: Any) -> None:
        promotion_id, style_id, blogger_id, user_id = uuid4(), uuid4(), uuid4(), uuid4()
        suffix = uuid4().hex[:8]
        tenant_id = await default_tenant_id(engine)
        async with committed(engine) as seed:
            await seed.execute(
                text(
                    'INSERT INTO "user" (id, tenant_id, username, password_hash, '
                    "status, password_must_change, created_at, updated_at) "
                    "VALUES (:id, :tid, :un, 'x', 'active', false, NOW(), NOW())"
                ),
                {"id": user_id, "tid": tenant_id, "un": f"metrics_{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, "
                    "category, design_status, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :code, 'MT款', '连衣裙', '大货', true, false, NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "code": f"MTST{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, "
                    "platform, is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :xhs, 'MT博主', '小红书', false, true, false, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "xhs": f"XHS{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO promotion (id, tenant_id, style_id, blogger_id, pr_id, "
                    "internal_code, style_code_snapshot, style_short_name_snapshot, "
                    "quote_amount, platform, cooperation_date, publish_status, "
                    "recall_status, settlement_status, is_active, created_at, updated_at) "
                    "VALUES (:id, :tid, :sid, :bid, :uid, :code, 'SC', 'SN', 500.00, "
                    "'小红书', :cd, '已发布', '未召回', '已结款', true, NOW(), NOW())"
                ),
                {
                    "id": promotion_id,
                    "tid": tenant_id,
                    "sid": style_id,
                    "bid": blogger_id,
                    "uid": user_id,
                    "code": f"MTPR{suffix}",
                    "cd": date(2026, 5, 1),
                },
            )

        barrier = asyncio.Barrier(2)

        async def attempt(s: AsyncSession, i: int) -> bool:
            await s.execute(text("SELECT 1"))  # 先把连接拿到手，两边一起起跑
            await barrier.wait()
            updated = await PromotionRepository(s).record_metrics_once(
                promotion_id=promotion_id,
                tenant_id=tenant_id,
                values={"like_count": 100 + i, "collect_count": 1, "comment_count": 1},
            )
            # 先成功的那边拿着行锁多停一会儿，另一边的 UPDATE 一定排在它后面再判 WHERE
            await asyncio.sleep(0.3)
            await s.commit()
            return updated is not None

        try:
            results = await run_concurrently(engine, 2, attempt, tenant_id=tenant_id)
            assert sorted(results) == [False, True], results
            async with committed(engine) as check:
                like = (
                    await check.execute(
                        text("SELECT like_count FROM promotion WHERE id = :p"),
                        {"p": promotion_id},
                    )
                ).scalar_one()
            assert like == 100 + results.index(True)
        finally:
            async with committed(engine) as cleanup:
                await purge_promotions(cleanup, [promotion_id])
                await cleanup.execute(
                    text("DELETE FROM blogger WHERE id = :id"), {"id": blogger_id}
                )
                await cleanup.execute(text("DELETE FROM style WHERE id = :id"), {"id": style_id})
                await cleanup.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": user_id})
