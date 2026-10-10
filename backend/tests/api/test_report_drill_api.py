"""7e R1 下钻接口 ``GET /api/reports/drill/promotions``（报表视图设计 2.1、4、5.1）。

真实路由 + 迁移 seed 的真实角色（``flow_users``）：

- 判权：工作进度 read / read_own 或 ``report.bi:read`` 任一；只持 ``read_own`` 的 PR 服务端强制本人
  （传别人的 ``pr_id`` / ``unassigned`` 也忽略，不报错）
- 响应字段 = ``DrillPromotionRow`` 白名单（不返回收件信息、备注、``source_extra``），``detail_url`` 恒 null
- F2 逐单金额：服务费要 ``quote_amount`` 可读，站外推广成本要 ``quote_amount`` 与 ``cost_snapshot`` 都可读
- 商品归属 = ``COALESCE(p.goods_main_id, 主商品推定)``（``_PROMO_GOODS_LATERAL``，与投产同一片段）
- 校验：跨度 > 366 天 ``REPORT_RANGE_TOO_LONG``；``from > to`` ``REPORT_INVALID_TIME_RANGE``；其余 422
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

# 场景放在上个月：整月都在过去，与「今天」无关
_TODAY = get_today()
_M_HI = _TODAY.replace(day=1) - timedelta(days=1)
_M_LO = _M_HI.replace(day=1)
_URL = "/api/reports/drill/promotions"
_RANGE = f"date_from={_M_LO}&date_to={_M_HI}"

_WHITELIST = {
    "id",
    "internal_code",
    "cooperation_date",
    "scheduled_publish_date",
    "actual_publish_date",
    "pr_id",
    "pr_name",
    "blogger_nickname",
    "platform",
    "display_short_name",
    "publish_status",
    "recall_status",
    "quote_amount",
    "total_promo_cost",
    "like_count",
    "detail_url",
}

_SECRETS = ("SECRET-REMARK", "SECRET-EXTRA", "SECRET-RECEIVER", "13900000000", "SECRET-ADDR")


async def _call(session: AsyncSession, user: Any, path: str) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限。"""
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
            return await c.get(path)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def _override(session: AsyncSession, user: Any, scope: str, effect: str) -> None:
    """给用户造一条个人授予 / 撤销（scope 须已入册）。"""
    perm = (await session.execute(select(Permission).where(Permission.scope == scope))).scalar_one()
    session.add(
        UserPermissionOverride(
            tenant_id=user.tenant_id, user_id=user.id, permission_id=perm.id, effect=effect
        )
    )
    await session.flush()


async def _goods(
    session: AsyncSession, tenant: Any, style: Any, *, short_name: str | None, season: str
) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=f"DG{uuid4().hex[:8]}",
        goods_title=f"商品{uuid4().hex[:6]}",
        short_name=short_name,
        season=season,
    )
    session.add(goods)
    await session.flush()
    session.add(
        GoodsStyleItem(tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=0)
    )
    await session.flush()
    return goods


@dataclass
class _Scene:
    users: Any
    g1: GoodsMain
    g2: GoodsMain
    p1: UUID  # pr，无归属 → 推定 G1，已发布，带收件 / 备注 / source_extra
    p2: UUID  # pr，显式归属 G2（款式在 G1）
    p3: UUID  # pr2，款式在 G2，已发布
    p4: UUID  # 未分配，款式在 G1


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
async def scene(
    session: AsyncSession,
    tenant_a: Any,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> _Scene:
    s1 = await product_factory.style(style_code=f"DS{uuid4().hex[:8]}")
    s2 = await product_factory.style(style_code=f"DT{uuid4().hex[:8]}")
    g1 = await _goods(session, tenant_a, s1, short_name="简称一", season="春季")
    g2 = await _goods(session, tenant_a, s2, short_name=None, season="夏季")
    blogger = await blogger_factory.blogger(nickname="下钻博主")

    async def promo(style: Any, day: int, **kw: Any) -> UUID:
        p = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            internal_code=f"DR{uuid4().hex[:10].upper()}",
            cooperation_date=_M_LO + timedelta(days=day),
            **kw,
        )
        return p.id

    pr, pr2 = flow_users.pr, flow_users.pr2
    p1 = await promo(
        s1,
        2,
        pr=pr,
        quote_amount=Decimal("300.00"),
        cost_snapshot=Decimal("20.00"),
        publish_status="已发布",
        like_count=120,
        remark="SECRET-REMARK",
        source_extra={"打单地址": "SECRET-EXTRA"},
        receiver_name="SECRET-RECEIVER",
        receiver_phone="13900000000",
        receiver_address="SECRET-ADDR",
    )
    p2 = await promo(s1, 5, pr=pr, quote_amount=Decimal("150.00"), goods_main_id=g2.id)
    p3 = await promo(
        s2,
        3,
        pr=pr2,
        quote_amount=Decimal("500.00"),
        publish_status="已发布",
        style_short_name_snapshot="快照名",
    )
    p4 = await promo(s1, 4, quote_amount=Decimal("80.00"))
    # 不进结果：已删除、区间外
    await promo(s1, 6, pr=pr, is_active=False)
    await promo(s1, -1, pr=pr)
    return _Scene(flow_users, g1, g2, p1, p2, p3, p4)


def _ids(resp: Any) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [r["id"] for r in resp.json()["items"]]


@pytest.mark.usefixtures("tenant_ctx")
class TestDrillPromotions:
    async def test_whitelist_and_order(self, session: AsyncSession, scene: _Scene) -> None:
        resp = await _call(session, scene.users.pr_manager, f"{_URL}?{_RANGE}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 4
        assert (body["page"], body["page_size"]) == (1, 20)
        # 合作日期倒序
        assert [r["id"] for r in body["items"]] == [
            str(x) for x in (scene.p2, scene.p4, scene.p3, scene.p1)
        ]
        for row in body["items"]:
            assert set(row) == _WHITELIST
            assert row["detail_url"] is None
        for secret in _SECRETS:
            assert secret not in resp.text
        rows = {r["id"]: r for r in body["items"]}
        r1 = rows[str(scene.p1)]
        assert r1["blogger_nickname"] == "下钻博主"
        assert r1["pr_id"] == str(scene.users.pr.id)
        assert r1["like_count"] == 120
        assert r1["publish_status"] == "已发布"
        # 品名：按归属商品（推定 G1）的简称
        assert r1["display_short_name"] == "简称一"
        # G2 没有简称 → 回落快照
        assert rows[str(scene.p3)]["display_short_name"] == "快照名"
        assert rows[str(scene.p4)]["pr_id"] is None
        assert rows[str(scene.p4)]["pr_name"] == "未分配"

    async def test_pr_forced_to_self(self, session: AsyncSession, scene: _Scene) -> None:
        mine = [str(scene.p2), str(scene.p1)]
        pr = scene.users.pr
        assert _ids(await _call(session, pr, f"{_URL}?{_RANGE}")) == mine
        # 传别人的 id / 未分配都忽略，不报错
        other = f"{_URL}?{_RANGE}&pr_id={scene.users.pr2.id}"
        assert _ids(await _call(session, pr, other)) == mine
        assert _ids(await _call(session, pr, f"{_URL}?{_RANGE}&pr_id=unassigned")) == mine

    async def test_full_scope_honours_pr_param(self, session: AsyncSession, scene: _Scene) -> None:
        mgr = scene.users.pr_manager
        assert _ids(await _call(session, mgr, f"{_URL}?{_RANGE}&pr_id=unassigned")) == [
            str(scene.p4)
        ]
        pr2 = f"{_URL}?{_RANGE}&pr_id={scene.users.pr2.id}"
        assert _ids(await _call(session, mgr, pr2)) == [str(scene.p3)]

    async def test_bi_read_alone_sees_all(self, session: AsyncSession, scene: _Scene) -> None:
        """只持 ``report.bi:read``（个人授予）也算看全部，按请求的 ``pr_id``。"""
        wh = scene.users.warehouse
        path = f"{_URL}?{_RANGE}&pr_id={scene.users.pr2.id}"
        before = await _call(session, wh, path)
        assert (before.status_code, before.json()["code"]) == (403, "PERMISSION_DENIED")
        await _override(session, wh, "report.bi:read", "grant")
        assert _ids(await _call(session, wh, path)) == [str(scene.p3)]

    @pytest.mark.parametrize("role", ["pr_manager", "finance", "admin"])
    async def test_amounts_visible(self, session: AsyncSession, scene: _Scene, role: str) -> None:
        resp = await _call(session, getattr(scene.users, role), f"{_URL}?{_RANGE}")
        assert resp.status_code == 200, resp.text
        r1 = {r["id"]: r for r in resp.json()["items"]}[str(scene.p1)]
        assert (r1["quote_amount"], r1["total_promo_cost"]) == ("300.00", "320.00")

    async def test_operations_amounts_null(self, session: AsyncSession, scene: _Scene) -> None:
        """运营：能下钻（report.*:read），逐单金额两项都 null。"""
        resp = await _call(session, scene.users.operations, f"{_URL}?{_RANGE}")
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 4
        assert all(r["quote_amount"] is None and r["total_promo_cost"] is None for r in items)

    async def test_cost_snapshot_revoked(self, session: AsyncSession, scene: _Scene) -> None:
        """只撤 ``cost_snapshot`` 读：服务费照给，站外推广成本 null。"""
        fin = scene.users.finance
        await _override(session, fin, "field.promotion.cost_snapshot:read", "revoke")
        resp = await _call(session, fin, f"{_URL}?{_RANGE}")
        assert resp.status_code == 200, resp.text
        r1 = {r["id"]: r for r in resp.json()["items"]}[str(scene.p1)]
        assert (r1["quote_amount"], r1["total_promo_cost"]) == ("300.00", None)

    async def test_goods_attribution(self, session: AsyncSession, scene: _Scene) -> None:
        """无归属的单按主商品推定；显式归属的单不再推定（同投产口径）。"""
        mgr = scene.users.pr_manager
        g1 = await _call(session, mgr, f"{_URL}?{_RANGE}&goods_id={scene.g1.id}")
        assert _ids(g1) == [str(scene.p4), str(scene.p1)]
        g2 = await _call(session, mgr, f"{_URL}?{_RANGE}&goods_id={scene.g2.id}")
        assert _ids(g2) == [str(scene.p2), str(scene.p3)]
        none = await _call(session, mgr, f"{_URL}?{_RANGE}&goods_id={uuid4()}")
        assert none.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}

    async def test_season_and_status_filters(self, session: AsyncSession, scene: _Scene) -> None:
        mgr = scene.users.pr_manager
        spring = await _call(session, mgr, f"{_URL}?{_RANGE}&season={quote('春季')}")
        assert _ids(spring) == [str(scene.p4), str(scene.p1)]
        both = f"{_URL}?{_RANGE}&season={quote('春季')}&season={quote('夏季')}"
        assert len(_ids(await _call(session, mgr, both))) == 4
        published = await _call(session, mgr, f"{_URL}?{_RANGE}&publish_status={quote('已发布')}")
        assert _ids(published) == [str(scene.p3), str(scene.p1)]

    async def test_pagination(self, session: AsyncSession, scene: _Scene) -> None:
        resp = await _call(session, scene.users.pr_manager, f"{_URL}?{_RANGE}&page=2&page_size=3")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["total"], body["page"], body["page_size"]) == (4, 2, 3)
        assert [r["id"] for r in body["items"]] == [str(scene.p1)]

    async def test_warehouse_forbidden(self, session: AsyncSession, scene: _Scene) -> None:
        resp = await _call(session, scene.users.warehouse, f"{_URL}?{_RANGE}")
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")

    async def test_span_366_ok(self, session: AsyncSession, flow_users: Any) -> None:
        path = f"{_URL}?date_from={_M_LO}&date_to={_M_LO + timedelta(days=366)}"
        resp = await _call(session, flow_users.pr_manager, path)
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            (
                f"date_from={_M_LO}&date_to={_M_LO + timedelta(days=367)}",
                "REPORT_RANGE_TOO_LONG",
            ),
            (f"date_from={_M_HI}&date_to={_M_LO}", "REPORT_INVALID_TIME_RANGE"),
            (f"date_to={_M_HI}", None),
            (f"{_RANGE}&pr_id=abc", "VALIDATION_ERROR"),
            (f"{_RANGE}&publish_status=foo", None),
            (_RANGE + "".join(f"&season=s{i}" for i in range(21)), "VALIDATION_ERROR"),
            (f"{_RANGE}&season={'x' * 65}", "VALIDATION_ERROR"),
            (f"{_RANGE}&page_size=101", None),
            (f"{_RANGE}&page=0", None),
            (f"{_RANGE}&goods_id=abc", None),
        ],
        ids=[
            "span_367",
            "from_after_to",
            "missing_from",
            "bad_pr_id",
            "bad_status",
            "season_21",
            "season_65_chars",
            "page_size_101",
            "page_0",
            "bad_goods_id",
        ],
    )
    async def test_invalid_422(
        self, session: AsyncSession, flow_users: Any, query: str, code: str | None
    ) -> None:
        resp = await _call(session, flow_users.pr_manager, f"{_URL}?{query}")
        assert resp.status_code == 422, resp.text
        if code is not None:
            assert resp.json()["code"] == code

    async def test_twenty_seasons_ok(self, session: AsyncSession, flow_users: Any) -> None:
        """边界：20 个季节、其中一个 64 字，照常 200。"""
        query = _RANGE + "".join(f"&season=s{i}" for i in range(19)) + f"&season={'x' * 64}"
        resp = await _call(session, flow_users.pr_manager, f"{_URL}?{query}")
        assert resp.status_code == 200, resp.text
