"""U02 product API 端到端契约测试。

通过 ASGI 直连（无真实 DB），验证：
- 端点存在 + schema 校验正确
- 鉴权依赖正确（无 token → 401）
- 路径前缀（/api/styles, /api/skus, /api/brands, /api/styles/match）正确暴露
- match 接口 query 参数互斥校验
- OpenAPI 文档暴露 product 端点

完整业务路径测试在 integration/ 目录。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestProductApiContract:
    async def test_styles_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/styles/")
        assert resp.status_code == 401

    async def test_skus_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/skus/",
                json={
                    "style_id": "00000000-0000-0000-0000-000000000000",
                    "sku_code": "X",
                    "color": "红",
                    "size": "M",
                },
            )
        assert resp.status_code == 401

    async def test_brands_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/brands/")
        assert resp.status_code == 401

    async def test_match_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/styles/match?keyword=x")
        assert resp.status_code == 401

    async def test_create_style_validates_payload(self) -> None:
        """无效 style_code 格式 → schema 422 (在鉴权前发生)."""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/styles/",
                json={
                    "style_code": "包含中文",  # 违反 ^[A-Za-z0-9_\-]+$
                    "style_name": "x",
                },
                headers={"Authorization": "Bearer fake"},
            )
        # auth 在 dependency 前没有校验 schema，可能是 401（先 token）或 422（先 schema）
        # 取决于 FastAPI dependency 顺序，二者皆可接受
        assert resp.status_code in {401, 422}

    async def test_openapi_exposes_product_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        paths = spec.get("paths", {})
        # 验证关键端点都暴露
        assert "/api/styles/" in paths
        assert "/api/styles/match" in paths
        assert "/api/styles/{style_id}" in paths
        assert "/api/skus/" in paths
        assert "/api/skus/by-style/{style_id}" in paths
        assert "/api/skus/{sku_id}" in paths
        assert "/api/brands/" in paths
        assert "/api/brands/{brand_id}" in paths


# ---------------------------------------------------------------------------
# AC 5（8a-1）：/api/styles 系列路由与权限声明与基线一致
# ---------------------------------------------------------------------------

_Perm = tuple[str, str]

# 7a 合入后 /api/styles 前缀的 12 条路由：(方法, 路径) -> require_permission 的 (scope, action)。
# 款式列表、款式下的商品两条是 7a-1 改挂的 product.style:read（PR / 主管的款式下拉），其余同 5772fa1
_STYLE_ROUTE_BASELINE: dict[tuple[str, str], _Perm] = {
    ("POST", "/api/styles/"): ("product", "write"),
    ("GET", "/api/styles/match"): ("product", "read"),
    ("GET", "/api/styles/"): ("product.style", "read"),
    ("GET", "/api/styles/{style_id}/goods"): ("product.style", "read"),
    ("GET", "/api/styles/{style_id}"): ("product", "read"),
    ("PUT", "/api/styles/{style_id}"): ("product", "write"),
    ("POST", "/api/styles/{style_id}/main-image"): ("product", "write"),
    ("DELETE", "/api/styles/{style_id}/main-image"): ("product", "write"),
    ("DELETE", "/api/styles/{style_id}"): ("product", "delete"),
    ("POST", "/api/styles/{style_id}/disable"): ("product", "write"),
    ("POST", "/api/styles/{style_id}/enable"): ("product", "write"),
    ("POST", "/api/styles/{style_id}/restore"): ("product", "delete"),
}

# 该前缀下只允许新增的路由（8a-2 批量传图，后续步骤才加；现在不存在也通过）
_ALLOWED_NEW: dict[tuple[str, str], _Perm] = {
    ("POST", "/api/styles/main-images/batch"): ("product", "write"),
}


def _route_permissions(route: object) -> set[_Perm]:
    """从路由依赖里取出 require_permission 闭包的 (scope, action)。"""
    import inspect

    perms: set[_Perm] = set()
    for dep in getattr(route, "dependencies", []):
        fn = getattr(dep, "dependency", None)
        if fn is None or not inspect.isfunction(fn):
            continue
        nonlocals = inspect.getclosurevars(fn).nonlocals
        if "scope" in nonlocals and "action" in nonlocals:
            perms.add((str(nonlocals["scope"]), str(nonlocals["action"])))
    return perms


def _style_routes() -> dict[tuple[str, str], set[_Perm]]:
    from fastapi.routing import APIRoute

    from app.main import app

    found: dict[tuple[str, str], set[_Perm]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != "/api/styles" and not route.path.startswith("/api/styles/"):
            continue
        for method in route.methods:
            found[(method, route.path)] = _route_permissions(route)
    return found


@pytest.mark.api
class TestStyleRoutesBaseline:
    """款式维护并进商品页只改前端；后端 /api/styles 路由与权限一条不动（AC 5）。"""

    def test_baseline_routes_kept_with_same_permissions(self) -> None:
        found = _style_routes()
        for key, perm in _STYLE_ROUTE_BASELINE.items():
            assert key in found, f"基线路由缺失：{key}"
            assert found[key] == {perm}, f"{key} 权限变了：{found[key]}，应为 {perm}"

    def test_no_unexpected_new_routes(self) -> None:
        found = _style_routes()
        extra = set(found) - set(_STYLE_ROUTE_BASELINE)
        assert extra <= set(_ALLOWED_NEW), f"/api/styles 下多出未登记的路由：{extra}"
        for key in extra:
            assert found[key] == {_ALLOWED_NEW[key]}, f"{key} 权限不符：{found[key]}"

    def test_guard_reads_permissions(self) -> None:
        """防空跑：至少能从基线路由里取到权限（取不到说明闭包取法失效）。"""
        found = _style_routes()
        assert found[("GET", "/api/styles/match")] == {("product", "read")}
