"""流程线 PR-2 仓库接口（``promotion/shipping_api.py`` 的 ``/api/warehouse`` 与回填，设计 7.3、7.4、9.3 A）。

仓库从任何接口都拿不到整张推广单：列表、回填只回 ``WarehouseShipmentRow`` 投影（没有博主、平台、
发布链接、金额、``source_extra``）。scope：列表与回填 ``promotion_ship:fill``、导出 ``promotion_ship:export``，
挂在路由层（``promotion_ship`` 独立一级域，PR 的 ``promotion.*:*``、运营的 ``promotion.*:read`` 捞不到）。
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Permission, UserPermissionOverride
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.enums import ShipCourier
from app.modules.promotion.models import Promotion

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

_LIST = "/api/warehouse/shipments"
_EXPORT = "/api/warehouse/shipments/export"

ROW_KEYS = {
    "id",
    "internal_code",
    "style_code",
    "display_short_name",
    "goods_title",
    "items",
    "legacy_color_spec",
    "receiver_name",
    "receiver_phone",
    "receiver_address",
    "receiver_updated_after_push",
    "items_updated_after_push",
    "ship_status",
    "ship_pushed_at",
    "ship_pushed_by_name",
    "ship_courier",
    "ship_waybill",
    "shipped_at",
    "ui",
}

_RECEIVER = {
    "receiver_name": "张三",
    "receiver_phone": "13812345678",
    "receiver_address": "浙江省杭州市西湖区某路 1 号",
}


async def _call(
    session: AsyncSession,
    user: Any,
    method: str,
    path: str,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
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
            return await c.request(method, path, json=json, params=params)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


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


async def _goods(session: AsyncSession, tenant: Any, *styles: Any, is_suit: bool) -> GoodsMain:
    code = f"G{uuid4().hex[:8]}"
    goods = GoodsMain(
        tenant_id=tenant.id, goods_code=code, goods_title=f"{code} 全称", is_suit=is_suit
    )
    session.add(goods)
    await session.flush()
    for order, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=order
            )
        )
    await session.flush()
    return goods


class _Seed:
    def __init__(self, flow_users: Any, product_factory: Any, blogger_factory: Any, pf: Any):
        self.flow_users = flow_users
        self.product_factory = product_factory
        self.blogger_factory = blogger_factory
        self.pf = pf

    async def promotion(self, **kw: Any) -> Promotion:
        style = kw.pop("style", None) or await self.product_factory.style()
        blogger = await self.blogger_factory.blogger()
        # 工厂默认编号只有 3 位随机（4,096 种），一条用例建 9 张时约 1% 撞唯一键：给足 8 位
        kw.setdefault("internal_code", f"DEWH{uuid4().hex[:8].upper()}")
        return await self.pf.promotion(style=style, blogger=blogger, pr=self.flow_users.pr, **kw)


@pytest.fixture
def seed(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any
) -> _Seed:
    return _Seed(flow_users, product_factory, blogger_factory, promotion_factory)


def _ids(resp: Any, wanted: set[UUID]) -> list[UUID]:
    """响应里属于本用例造的那几张（按响应顺序）。"""
    return [UUID(r["id"]) for r in resp.json()["items"] if UUID(r["id"]) in wanted]


async def _db_ship(session: AsyncSession, promotion_id: UUID) -> tuple[Any, ...]:
    row = (
        await session.execute(
            sa_text(
                "SELECT ship_status, ship_courier, ship_waybill, shipped_at "
                "FROM promotion WHERE id = :p"
            ),
            {"p": promotion_id},
        )
    ).one()
    return tuple(row)


# ---------------------------------------------------------------------------
# 契约
# ---------------------------------------------------------------------------


class TestContract:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", _LIST),
            ("GET", _EXPORT),
            ("PATCH", f"/api/promotions/{uuid4()}/warehouse-waybill"),
        ],
    )
    async def test_requires_auth(self, method: str, path: str) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.request(method, path, json={"courier": "顺丰", "waybill": "SF1"})
        assert resp.status_code == 401

    async def test_openapi_exposes_warehouse_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            paths = (await ac.get("/api/openapi.json")).json()["paths"]
        assert _LIST in paths
        assert _EXPORT in paths
        assert "patch" in paths["/api/promotions/{promotion_id}/warehouse-waybill"]


# ---------------------------------------------------------------------------
# 权限通配（9.3 A）
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestScopes:
    @pytest.mark.parametrize("who", ["pr", "pr_manager", "operations", "finance"])
    async def test_list_and_export_403_without_scope(
        self, session: AsyncSession, flow_users: Any, who: str
    ) -> None:
        """PR（``promotion.*:*``）、主管（只有 push）、运营（``promotion.*:read``）、财务：列表与导出都 403。"""
        user = getattr(flow_users, who)
        for path in (_LIST, _EXPORT):
            resp = await _call(session, user, "GET", path)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), (
                path,
                resp.text,
            )

    @pytest.mark.parametrize("who", ["pr", "pr_manager", "operations", "finance"])
    async def test_fill_403_without_scope(
        self, session: AsyncSession, seed: _Seed, flow_users: Any, who: str
    ) -> None:
        promo = await seed.promotion(ship_status="待打单", **_RECEIVER)
        resp = await _call(
            session,
            getattr(flow_users, who),
            "PATCH",
            f"/api/promotions/{promo.id}/warehouse-waybill",
            {"courier": "顺丰", "waybill": "SF123"},
        )
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), resp.text
        assert (await _db_ship(session, promo.id))[0] == "待打单"

    async def test_warehouse_list_200_promotions_403(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        ok = await _call(session, flow_users.warehouse, "GET", _LIST)
        assert ok.status_code == 200, ok.text
        denied = await _call(session, flow_users.warehouse, "GET", "/api/promotions/")
        assert (denied.status_code, denied.json()["code"]) == (403, "PERMISSION_DENIED")

    async def test_page_ui_export_follows_scope(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        """页级 ``ui.actions.export`` 只在持 ``promotion_ship:export`` 时出现；管理员（``*``）也有。"""
        for user in (flow_users.warehouse, flow_users.admin):
            resp = await _call(session, user, "GET", _LIST)
            assert resp.json()["ui"] == {"actions": {"export": {"state": "enabled"}}}
        await _override(
            session, flow_users.tenant, flow_users.warehouse, "promotion_ship:export", "revoke"
        )
        resp = await _call(session, flow_users.warehouse, "GET", _LIST)
        assert resp.json()["ui"] == {"actions": {}}
        export = await _call(session, flow_users.warehouse, "GET", _EXPORT)
        assert (export.status_code, export.json()["code"]) == (403, "PERMISSION_DENIED")


# ---------------------------------------------------------------------------
# 列表：分桶、排序、关键字、投影
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("tenant_ctx")
class TestList:
    async def test_buckets_and_order(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        now = datetime.now(UTC)
        p_old = await seed.promotion(ship_status="待打单", ship_pushed_at=now - timedelta(hours=2))
        p_null = await seed.promotion(ship_status="待打单")
        p_new = await seed.promotion(ship_status="待打单", ship_pushed_at=now - timedelta(hours=1))
        s_new = await seed.promotion(ship_status="已发货", shipped_at=now - timedelta(days=1))
        s_old = await seed.promotion(ship_status="已发货", shipped_at=now - timedelta(days=2))
        s_null = await seed.promotion(ship_status="已发货")
        pending = await seed.promotion(ship_status="待发货")
        history = await seed.promotion()
        inactive = await seed.promotion(ship_status="待打单", is_active=False)
        mine = {
            p.id for p in (p_old, p_null, p_new, s_new, s_old, s_null, pending, history, inactive)
        }
        wh = flow_users.warehouse

        default = await _call(session, wh, "GET", _LIST, params={"page_size": 100})
        assert default.status_code == 200, default.text
        assert _ids(default, mine) == [p_null.id, p_old.id, p_new.id]

        shipped = await _call(session, wh, "GET", _LIST, params={"bucket": "已发货"})
        assert _ids(shipped, mine) == [s_new.id, s_old.id, s_null.id]

        every = await _call(session, wh, "GET", _LIST, params={"bucket": "全部"})
        assert _ids(every, mine) == [p_null.id, p_old.id, p_new.id, s_new.id, s_old.id, s_null.id]
        assert every.json()["total"] == 6

    @pytest.mark.parametrize(
        "params",
        [{"bucket": "待发货"}, {"bucket": "已打单"}, {"page_size": 101}, {"page": 0}],
    )
    async def test_bad_params_422(
        self, session: AsyncSession, flow_users: Any, params: dict[str, Any]
    ) -> None:
        resp = await _call(session, flow_users.warehouse, "GET", _LIST, params=params)
        assert resp.status_code == 422, resp.text

    async def test_paging(self, session: AsyncSession, seed: _Seed, flow_users: Any) -> None:
        now = datetime.now(UTC)
        made = [
            await seed.promotion(
                ship_status="待打单", ship_pushed_at=now - timedelta(minutes=10 - i)
            )
            for i in range(3)
        ]
        page2 = await _call(
            session, flow_users.warehouse, "GET", _LIST, params={"page": 2, "page_size": 2}
        )
        body = page2.json()
        assert (body["total"], body["page"], body["page_size"]) == (3, 2, 2)
        assert [UUID(r["id"]) for r in body["items"]] == [made[2].id]

    async def test_keyword(
        self,
        session: AsyncSession,
        seed: _Seed,
        flow_users: Any,
        product_factory: Any,
        tenant_a: Any,
    ) -> None:
        """关键字：内部编码 / 款式编码 / 商品简称 / 全称 / 编码 / SKU 编码 / 收件人 / 快递单号。"""
        style = await product_factory.style(style_code="KWSTYLE01")
        sku = await product_factory.sku(style, sku_code="KWSKU001")
        goods = await _goods(session, tenant_a, style, is_suit=False)
        by_style = await seed.promotion(
            style=style,
            ship_status="待打单",
            goods_main_id=goods.id,
            items=[(style, sku)],
            style_short_name_snapshot="快照简称甲",
        )
        by_code = await seed.promotion(ship_status="待打单", internal_code="DEKW00000001")
        by_receiver = await seed.promotion(ship_status="待打单", receiver_name="王小明")
        by_waybill = await seed.promotion(ship_status="已发货", ship_waybill="YT998877")
        other = await seed.promotion(ship_status="待打单")
        mine = {p.id for p in (by_style, by_code, by_receiver, by_waybill, other)}

        async def hits(kw: str) -> list[UUID]:
            resp = await _call(
                session,
                flow_users.warehouse,
                "GET",
                _LIST,
                params={"bucket": "全部", "keyword": kw},
            )
            assert resp.status_code == 200, resp.text
            return _ids(resp, mine)

        assert await hits("dekw0000") == [by_code.id]
        assert await hits("KWSTYLE") == [by_style.id]
        assert await hits("快照简称") == [by_style.id]
        assert await hits(goods.goods_code) == [by_style.id]
        assert await hits("全称") == [by_style.id]
        assert await hits("KWSKU0") == [by_style.id]
        assert await hits("王小明") == [by_receiver.id]
        assert await hits("YT9988") == [by_waybill.id]
        assert set(await hits("   ")) == mine  # 全空白 = 不过滤

    async def test_row_projection(
        self,
        session: AsyncSession,
        seed: _Seed,
        flow_users: Any,
        product_factory: Any,
        tenant_a: Any,
    ) -> None:
        """仓库行只有 ``WarehouseShipmentRow`` 的键：没有博主、平台、发布链接、服务费、``source_extra``（A5）。"""
        top = await product_factory.style(short_name="上衣", style_name="条纹上衣全称")
        pants = await product_factory.style(short_name=None, style_name="阔腿裤")
        top_sku = await product_factory.sku(top, color="黑色", size="M")
        pants_sku = await product_factory.sku(pants, color="白色", size="L")
        goods = await _goods(session, tenant_a, top, pants, is_suit=True)
        pushed_at = datetime.now(UTC) - timedelta(hours=1)
        suit = await seed.promotion(
            style=top,
            goods_main_id=goods.id,
            items=[(top, top_sku), (pants, pants_sku)],
            ship_status="待打单",
            ship_pushed_at=pushed_at,
            ship_pushed_by=flow_users.pr_manager.id,
            quote_amount=888,
            publish_url="https://example.com/note/1",
            source_extra={"颜色及规格": "旧写法", "订单号": "TB1"},
            **_RECEIVER,
        )
        legacy = await seed.promotion(
            ship_status="待打单", source_extra={"颜色及规格": "黑色 M 码"}
        )

        resp = await _call(session, flow_users.warehouse, "GET", _LIST, params={"page_size": 100})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == {"items", "total", "page", "page_size", "couriers", "ui"}
        assert body["couriers"] == [c.value for c in ShipCourier]
        rows = {r["id"]: r for r in body["items"]}
        row = rows[str(suit.id)]
        assert set(row) == ROW_KEYS
        assert row["items"] == [
            {"display_short_name": "上衣", "color": "黑色", "size": "M"},
            {"display_short_name": "阔腿裤", "color": "白色", "size": "L"},
        ]
        assert row["legacy_color_spec"] is None  # 有明细就不回落原文
        assert {k: row[k] for k in _RECEIVER} == _RECEIVER  # 仓库可读收件
        assert row["goods_title"] == goods.goods_title
        assert row["style_code"] == top.style_code
        assert row["ship_pushed_by_name"]
        assert (row["receiver_updated_after_push"], row["items_updated_after_push"]) == (
            False,
            False,
        )
        assert row["ui"] == {"actions": {"ship_fill": {"state": "enabled"}}}
        assert rows[str(legacy.id)]["items"] == []
        assert rows[str(legacy.id)]["legacy_color_spec"] == "黑色 M 码"

    async def test_receiver_follows_field_rule(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        """仓库行也过字段规则：撤销电话读 → 只有电话是 null，按收件人也搜不到（撤了姓名读的话）。"""
        promo = await seed.promotion(ship_status="待打单", **_RECEIVER)
        for field in ("receiver_phone", "receiver_name"):
            await _override(
                session,
                flow_users.tenant,
                flow_users.warehouse,
                f"field.promotion.{field}:read",
                "revoke",
            )
        resp = await _call(session, flow_users.warehouse, "GET", _LIST, params={"page_size": 100})
        row = next(r for r in resp.json()["items"] if r["id"] == str(promo.id))
        assert (row["receiver_name"], row["receiver_phone"]) == (None, None)
        assert row["receiver_address"] == _RECEIVER["receiver_address"]
        hit = await _call(session, flow_users.warehouse, "GET", _LIST, params={"keyword": "张三"})
        assert str(promo.id) not in {r["id"] for r in hit.json()["items"]}

    async def test_stage_h_row_has_no_fill_for_warehouse(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        """已发货且推广已取消、召回成功（阶段「已完结 · 召回」，H 列）：仓库只读、管理员可改。"""
        promo = await seed.promotion(
            ship_status="已发货", publish_status="已取消", recall_status="召回成功"
        )
        params = {"bucket": "已发货", "page_size": 100}
        wh = await _call(session, flow_users.warehouse, "GET", _LIST, params=params)
        row = next(r for r in wh.json()["items"] if r["id"] == str(promo.id))
        assert row["ui"] == {"actions": {}}
        admin = await _call(session, flow_users.admin, "GET", _LIST, params=params)
        row = next(r for r in admin.json()["items"] if r["id"] == str(promo.id))
        assert row["ui"] == {"actions": {"ship_fill": {"state": "enabled"}}}


# ---------------------------------------------------------------------------
# 回填（S5 / S6）
# ---------------------------------------------------------------------------


def _fill_path(promotion_id: UUID) -> str:
    return f"/api/promotions/{promotion_id}/warehouse-waybill"


@pytest.mark.usefixtures("tenant_ctx")
class TestFill:
    async def test_printing_to_shipped(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        promo = await seed.promotion(
            ship_status="待打单", quote_amount=888, publish_url="https://e.com/1", **_RECEIVER
        )
        before = datetime.now(UTC)
        resp = await _call(
            session,
            flow_users.warehouse,
            "PATCH",
            _fill_path(promo.id),
            {"courier": "顺丰", "waybill": "  SF123  "},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) == ROW_KEYS  # A7：不是整张推广单
        assert (body["ship_status"], body["ship_courier"], body["ship_waybill"]) == (
            "已发货",
            "顺丰",
            "SF123",
        )
        status, courier, waybill, shipped_at = await _db_ship(session, promo.id)
        assert (status, courier, waybill) == ("已发货", "顺丰", "SF123")
        assert before - timedelta(seconds=5) <= shipped_at <= datetime.now(UTC)

    async def test_shipped_change_waybill_audit(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        """S6：已发货改单号，audit_log 记前后值；发货时间可显式给（不晚于现在）。"""
        old_at = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
        promo = await seed.promotion(
            ship_status="已发货", ship_courier="中通", ship_waybill="ZT1", shipped_at=old_at
        )
        new_at = datetime.now(UTC) - timedelta(hours=3)
        resp = await _call(
            session,
            flow_users.warehouse,
            "PATCH",
            _fill_path(promo.id),
            {"courier": "圆通", "waybill": "YT2", "shipped_at": new_at.isoformat()},
        )
        assert resp.status_code == 200, resp.text
        assert await _db_ship(session, promo.id) == ("已发货", "圆通", "YT2", new_at)
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.resource_id == str(promo.id),
                    AuditLog.action == "promotion.warehouse_waybill.update",
                )
            )
        ).scalar_one()
        assert audit.before == {
            "ship_status": "已发货",
            "ship_courier": "中通",
            "ship_waybill": "ZT1",
            "shipped_at": old_at.isoformat(),
        }
        assert audit.after == {
            "ship_status": "已发货",
            "ship_courier": "圆通",
            "ship_waybill": "YT2",
            "shipped_at": new_at.isoformat(),
        }

    @pytest.mark.parametrize("ship_status", ["待发货", None])
    async def test_wrong_state_422(
        self, session: AsyncSession, seed: _Seed, flow_users: Any, ship_status: str | None
    ) -> None:
        """只接受待打单 / 已发货（A4）。"""
        promo = await seed.promotion(ship_status=ship_status)
        resp = await _call(
            session,
            flow_users.warehouse,
            "PATCH",
            _fill_path(promo.id),
            {"courier": "顺丰", "waybill": "SF1"},
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "ILLEGAL_STATE_TRANSITION")
        assert await _db_ship(session, promo.id) == (ship_status, None, None, None)

    @pytest.mark.parametrize(
        "body",
        [
            {"courier": "顺丰", "waybill": ""},
            {"courier": "顺丰", "waybill": "   "},
            {"courier": "顺丰", "waybill": "x" * 129},
            {"courier": "顺风", "waybill": "SF1"},
            {"waybill": "SF1"},
            {"courier": "顺丰", "waybill": "SF1", "shipped_at": "2026-10-01T08:00:00"},
        ],
    )
    async def test_body_validation_422(
        self, session: AsyncSession, seed: _Seed, flow_users: Any, body: dict[str, Any]
    ) -> None:
        """单号去空白后 1 ~ 128、快递公司枚举外、旧 body 只传单号、发货时间不带时区 → 422。"""
        promo = await seed.promotion(ship_status="待打单")
        resp = await _call(session, flow_users.warehouse, "PATCH", _fill_path(promo.id), body)
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR"), resp.text
        assert (await _db_ship(session, promo.id))[0] == "待打单"

    async def test_future_shipped_at_422(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        promo = await seed.promotion(ship_status="待打单")
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        resp = await _call(
            session,
            flow_users.warehouse,
            "PATCH",
            _fill_path(promo.id),
            {"courier": "顺丰", "waybill": "SF1", "shipped_at": future},
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "SHIPPED_AT_IN_FUTURE")
        assert (await _db_ship(session, promo.id))[0] == "待打单"

    async def test_not_found_and_inactive_404(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        inactive = await seed.promotion(ship_status="待打单", is_active=False)
        for pid in (uuid4(), inactive.id):
            resp = await _call(
                session,
                flow_users.warehouse,
                "PATCH",
                _fill_path(pid),
                {"courier": "顺丰", "waybill": "SF1"},
            )
            assert resp.status_code == 404, resp.text

    async def test_stage_h_only_admin(
        self, session: AsyncSession, seed: _Seed, flow_users: Any
    ) -> None:
        """H 列（已完结 · 召回）：仓库 403，管理员可改。"""
        promo = await seed.promotion(
            ship_status="已发货",
            publish_status="已取消",
            recall_status="召回成功",
            ship_courier="顺丰",
            ship_waybill="SF1",
        )
        body = {"courier": "顺丰", "waybill": "SF2"}
        denied = await _call(session, flow_users.warehouse, "PATCH", _fill_path(promo.id), body)
        assert (denied.status_code, denied.json()["code"]) == (403, "PERMISSION_DENIED")
        ok = await _call(session, flow_users.admin, "PATCH", _fill_path(promo.id), body)
        assert ok.status_code == 200, ok.text
        assert (await _db_ship(session, promo.id))[2] == "SF2"


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def _xlsx_rows(content: bytes) -> list[tuple[Any, ...]]:
    ws = load_workbook(io.BytesIO(content), read_only=True).worksheets[0]
    return [tuple(r) for r in ws.iter_rows(values_only=True)]


@pytest.mark.usefixtures("tenant_ctx")
class TestExport:
    async def test_suit_two_lines_legacy_one_line_and_audit(
        self,
        session: AsyncSession,
        seed: _Seed,
        flow_users: Any,
        product_factory: Any,
        tenant_a: Any,
    ) -> None:
        top = await product_factory.style(short_name="上衣", style_code="EXTOP01")
        pants = await product_factory.style(short_name="裤子", style_code="EXPANTS01")
        top_sku = await product_factory.sku(top, color="黑色", size="M", sku_code="EXSKUTOP")
        pants_sku = await product_factory.sku(pants, color="白色", size="L", sku_code="EXSKUPAN")
        goods = await _goods(session, tenant_a, top, pants, is_suit=True)
        pushed = datetime(2026, 10, 9, 2, 5, tzinfo=UTC)
        await seed.promotion(
            style=top,
            internal_code="DEEX00000001",
            goods_main_id=goods.id,
            items=[(top, top_sku), (pants, pants_sku)],
            ship_status="待打单",
            ship_pushed_at=pushed,
            **_RECEIVER,
        )
        old_style = await product_factory.style(style_code="EXOLD01")
        old_sku = await product_factory.sku(old_style, sku_code="EXSKUOLD")
        await seed.promotion(
            style=old_style,
            internal_code="DEEX00000002",
            sku_id=old_sku.id,
            ship_status="待打单",
            ship_pushed_at=pushed + timedelta(minutes=1),
            style_short_name_snapshot="旧款",
            source_extra={"颜色及规格": "黑色 M 码"},
        )
        await seed.promotion(ship_status="已发货", internal_code="DEEX00000003")

        resp = await _call(
            session, flow_users.warehouse, "GET", _EXPORT, params={"keyword": "DEEX"}
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        rows = _xlsx_rows(resp.content)
        assert rows[0][:8] == (
            "内部编码",
            "推送时间",
            "收件人",
            "电话",
            "地址",
            "款式编码",
            "商品编码",
            "SKU 编码",
        )
        receiver = tuple(_RECEIVER.values())
        assert rows[1:] == [
            ("DEEX00000001", "2026-10-09 10:05", *receiver, "EXTOP01", goods.goods_code, "EXSKUTOP", "上衣", "黑色", "M", 1),
            ("DEEX00000001", "2026-10-09 10:05", *receiver, "EXPANTS01", goods.goods_code, "EXSKUPAN", "裤子", "白色", "L", 1),
            ("DEEX00000002", "2026-10-09 10:06", None, None, None, "EXOLD01", None, "EXSKUOLD", "旧款", "黑色 M 码", None, 1),
        ]  # fmt: skip
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "warehouse.shipments.export",
                        AuditLog.user_id == flow_users.warehouse.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(a.after, a.user_id) for a in audits] == [
            (
                {"bucket": "待打单", "keyword": "DEEX", "promotions": 2, "rows": 3},
                flow_users.warehouse.id,
            )
        ]

    async def test_over_5000_422(self, session: AsyncSession, seed: _Seed, flow_users: Any) -> None:
        """11-26：超过 5,000 张单 → 422 ``EXPORT_TOO_MANY_ROWS``，不写导出审计。"""
        base = await seed.promotion(ship_status="待打单")
        cols = [
            c.name
            for c in Promotion.__table__.columns
            if c.computed is None and c.name not in {"id", "internal_code"}
        ]
        col_sql = ", ".join(cols)
        await session.execute(
            sa_text(
                f"INSERT INTO promotion (id, internal_code, {col_sql}) "
                f"SELECT gen_random_uuid(), 'BK' || LPAD(g::text, 6, '0'), {col_sql} "
                "FROM promotion, generate_series(1, 5000) AS g WHERE id = :pid"
            ),
            {"pid": base.id},
        )
        resp = await _call(session, flow_users.warehouse, "GET", _EXPORT)
        assert (resp.status_code, resp.json()["code"]) == (422, "EXPORT_TOO_MANY_ROWS"), resp.text
        assert resp.json()["details"] == {"total": 5001, "limit": 5000}
        count = (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "warehouse.shipments.export",
                    AuditLog.user_id == flow_users.warehouse.id,
                )
            )
        ).scalar_one()
        assert count == 0
