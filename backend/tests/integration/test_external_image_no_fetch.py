"""AC 10（8a-2，设计 §7.4）：聚水潭「图片」外部链接只存不取。

导入一份带「图片」https 链接的 42 列 xlsx（走真实 runner），再调款式列表 / 商品列表 / 成本表接口；
期间把依赖里全部 HTTP 客户端的入口换成「计数并抛错」的替身，另把 ``socket.getaddrinfo`` 包一层
（host 是测试用的外部域名就计数并抛错，测试库 / Redis 原样放行）——任何客户端发请求前都要先解析
域名，绕过前面几个入口也会在这里被抓到。断言全部零调用。

测试款式只有外部链接、没有上传主图，列表接口不会走 boto3 签名。测试自己的 ``AsyncClient`` 用的是
进程内的 ``ASGITransport``，替身对它放行，只拦向外发的请求。
"""

from __future__ import annotations

import socket
import urllib.request
from collections import Counter
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import urllib3.connectionpool
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.core.db import get_session
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import Role, User, UserRole
from app.modules.auth.service import AuthService
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from tests.integration.test_import_style_sku_8a import _Env

EXTERNAL_HOST = "img.example.invalid"
IMAGE_URL = f"https://{EXTERNAL_HOST}/goods/ac10.jpg"


@pytest.fixture
async def env(engine: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    """同 test_import_style_sku_8a 的 env：真实 runner、提交的种子数据、用完清理。"""
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(StyleSkuImportAdapter())
    async with Maker() as s:
        tenant_id = (
            await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()[0]
    suffix = uuid4().hex[:8]
    user_id = uuid4()
    async with Maker() as s:
        await s.execute(
            text(
                'INSERT INTO "user" (id, tenant_id, username, password_hash, display_name, '
                "status, password_must_change, failed_login_count, created_at, updated_at) "
                "VALUES (:id, :tid, :u, 'x', '导入人', 'active', false, 0, NOW(), NOW())"
            ),
            {"id": user_id, "tid": tenant_id, "u": f"imp_{suffix}"},
        )
        await s.commit()
    e = _Env(Maker=Maker, suffix=suffix, tenant_id=tenant_id, user_id=user_id)
    import app.core.attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "get_object_bytes", lambda b, k: e.files[k])
    try:
        yield e
    finally:
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry._adapters.update(saved)
        await e.cleanup()  # 删用户时 user_role 级联删除


@pytest.fixture
def no_outbound(monkeypatch: pytest.MonkeyPatch) -> Iterator[Counter[str]]:
    """把全部 HTTP 客户端入口换成计数并抛错的替身；返回计数器。"""
    calls: Counter[str] = Counter()

    def _blocked(name: str) -> Any:
        def spy(*_a: Any, **_k: Any) -> Any:
            calls[name] += 1
            raise RuntimeError(f"出站请求被拦截：{name}")

        return spy

    real_async_send = httpx.AsyncClient.send

    async def async_send(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> Any:
        # 测试自己的进程内客户端（ASGITransport）放行，其余一律拦下
        if isinstance(getattr(self, "_transport", None), httpx.ASGITransport):
            return await real_async_send(self, request, **kw)
        calls["httpx.AsyncClient.send"] += 1
        raise RuntimeError("出站请求被拦截：httpx.AsyncClient.send")

    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: Any, *args: Any, **kw: Any) -> Any:
        if isinstance(host, bytes):
            host = host.decode()
        if isinstance(host, str) and host.lower().rstrip(".") == EXTERNAL_HOST:
            calls["socket.getaddrinfo"] += 1
            raise OSError(f"域名解析被拦截：{host}")
        return real_getaddrinfo(host, *args, **kw)

    monkeypatch.setattr(httpx.Client, "send", _blocked("httpx.Client.send"))
    monkeypatch.setattr(httpx.AsyncClient, "send", async_send)
    monkeypatch.setattr(
        urllib.request.OpenerDirector, "open", _blocked("urllib.request.OpenerDirector.open")
    )
    monkeypatch.setattr(
        urllib3.connectionpool.HTTPConnectionPool,
        "urlopen",
        _blocked("urllib3.connectionpool.HTTPConnectionPool.urlopen"),
    )
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield calls


def _app() -> Any:
    from app.main import app

    return app


@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_and_list_never_fetch_external_image(
    env: _Env, no_outbound: Counter[str]
) -> None:
    # 1）导入：「图片」是 https 外部链接，新建款式 / SKU / 单品商品
    batch_id, _ = await env.run([env.row("A", "1", **{"图片": IMAGE_URL})])
    batch = await env.batch(batch_id)
    assert (batch.status, batch.imported) == ("completed", 1)
    style = await env.style_row("A")
    assert style.external_image_url == IMAGE_URL

    # 2）接口：以管理员身份调款式列表 / 商品列表 / 成本表
    async with env.Maker() as s:
        admin = (await s.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        tok = tenant_id_ctx.set(env.tenant_id)
        try:
            s.add(UserRole(tenant_id=env.tenant_id, user_id=env.user_id, role_id=admin.id))
            await s.commit()
        finally:
            tenant_id_ctx.reset(tok)

    app = _app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        async with env.Maker() as s:
            yield s

    async with env.Maker() as s:
        user = (await s.execute(select(User).where(User.id == env.user_id))).scalar_one()
        perms = await AuthService(s).load_effective_permissions(env.user_id)
    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_current_user_active] = lambda: user
    app.dependency_overrides[get_current_perms] = lambda: perms
    tok = tenant_id_ctx.set(env.tenant_id)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            styles = await client.get("/api/styles/", params={"keyword": env.sc("A")})
            goods = await client.get("/api/goods/", params={"keyword": env.sc("A")})
            skus = await client.get("/api/skus/", params={"keyword": env.kc("1")})
    finally:
        tenant_id_ctx.reset(tok)
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)

    assert styles.status_code == 200, styles.text
    [s_item] = styles.json()["items"]
    assert (s_item["image_url"], s_item["image_source"]) == (IMAGE_URL, "external")
    assert s_item["external_image_url"] == IMAGE_URL
    assert s_item["main_image_url"] is None

    assert goods.status_code == 200, goods.text
    [g_item] = goods.json()["items"]
    assert [(i["url"], i["source"]) for i in g_item["images"]] == [(IMAGE_URL, "external")]

    assert skus.status_code == 200, skus.text
    [k_item] = skus.json()["items"]
    assert (k_item["image_url"], k_item["image_source"]) == (IMAGE_URL, "external")

    # 3）全程零出站请求
    assert dict(no_outbound) == {}
