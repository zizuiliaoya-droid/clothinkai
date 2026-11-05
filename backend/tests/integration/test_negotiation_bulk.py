"""真实路由、逐项事务与创建重试；数据库由CI执行。"""

from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.schemas import NegotiationReassignRequest
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def call(session, user_id, tenant_id, action, body):
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    user = await session.get(User, user_id, populate_existing=True)
    perms = await AuthService(session).load_effective_permissions(user_id)

    async def request_session():
        token = tenant_id_ctx.set(tenant_id)
        try:
            yield session
        finally:
            tenant_id_ctx.reset(token)

    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_current_user_active] = lambda: user
    app.dependency_overrides[get_current_perms] = lambda: perms
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(f"/api/negotiations/bulk/{action}", json=body)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def ready_payload(product_factory, blogger_factory):
    style = await product_factory.style()
    sku = await product_factory.sku(style)
    blogger = await blogger_factory.blogger(quote=500)
    return {
        "blogger_id": str(blogger.id),
        "style_id": str(style.id),
        "cooperation_mode": "送拍",
        "scheduled_publish_date": get_today().isoformat(),
        "items": [{"style_id": str(style.id), "sku_id": str(sku.id)}],
    }


def create_body(*payloads):
    return {
        "creation_group_id": str(uuid4()),
        "items": [{"client_create_key": str(uuid4()), "payload": payload} for payload in payloads],
    }


def actions(*ids, payload=None):
    return {"items": [{"negotiation_id": str(nid), "payload": payload or {}} for nid in ids]}


async def test_creation_mixed_rows_replay_original_price_conflict_and_lookup(
    session, flow_users, product_factory, blogger_factory
):
    uid, tid = flow_users.pr.id, flow_users.tenant.id
    payload = await ready_payload(product_factory, blogger_factory)
    body = create_body(
        payload,
        {**payload, "quote_amount": -1},
        {"blogger_id": payload["blogger_id"], "cooperation_mode": "送拍"},
    )
    response = await call(session, uid, tid, "create", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["success"], result["failed"], result["skipped"]) == (2, 1, 0)
    assert result["results"][1]["code"] == "VALIDATION_ERROR"
    first_id = UUID(result["results"][0]["negotiation_id"])
    assert result["results"][0]["negotiation"]["account_snapshot"] is not None
    await session.execute(
        text("UPDATE blogger SET quote=999 WHERE id=:id"), {"id": UUID(payload["blogger_id"])}
    )
    await session.commit()
    retry_body = {**body, "items": [body["items"][0]]}
    retry = (await call(session, uid, tid, "create", retry_body)).json()
    assert retry["results"][0]["reused"]
    assert retry["results"][0]["negotiation_id"] == str(first_id)
    assert retry["results"][0]["negotiation"]["quote_amount"] == "500.00"
    conflict = {
        **retry_body,
        "items": [{**body["items"][0], "payload": {**payload, "remark": "不同请求"}}],
    }
    assert (await call(session, uid, tid, "create", conflict)).json()["results"][0][
        "code"
    ] == "CREATE_KEY_CONFLICT"
    lookup = (
        await call(
            session,
            uid,
            tid,
            "lookup",
            {"client_create_keys": [body["items"][0]["client_create_key"], str(uuid4())]},
        )
    ).json()
    assert lookup["results"][0]["negotiation_id"] == str(first_id)
    assert lookup["results"][1]["code"] == "NOT_CREATED"
    assert (
        await session.scalar(
            select(text("count(*)"))
            .select_from(Negotiation)
            .where(Negotiation.creation_group_id == UUID(body["creation_group_id"]))
        )
        == 2
    )


async def test_post_commit_response_failure_is_recovered_by_original_key(
    session, flow_users, product_factory, blogger_factory, monkeypatch
):
    uid, tid = flow_users.pr.id, flow_users.tenant.id
    body = create_body(await ready_payload(product_factory, blogger_factory))
    key = UUID(body["items"][0]["client_create_key"])
    original_get = NegotiationService.get
    committed_ids = []

    async def fail_response(service, negotiation_id, user):
        # create 已完成真实 Session.commit，尚未开始读取响应的新事务。
        # 不在写入前抛错，才能覆盖“返回失败但单据其实已创建”的恢复路径。
        assert not service._session.in_transaction()
        committed_ids.append(negotiation_id)
        raise RuntimeError("injected-post-commit-response-failure")

    monkeypatch.setattr(NegotiationService, "get", fail_response)
    response = await call(session, uid, tid, "create", body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["success"], result["failed"]) == (0, 1)
    assert result["results"][0]["code"] == "INTERNAL_ERROR"
    assert result["results"][0]["negotiation_id"] is None
    assert len(committed_ids) == 1
    nid = committed_ids[0]
    stored = (
        await session.execute(
            select(Negotiation.id, Negotiation.client_create_key, Negotiation.creation_pr_id).where(
                Negotiation.tenant_id == tid, Negotiation.client_create_key == key
            )
        )
    ).one()
    assert stored == (nid, key, uid)

    monkeypatch.setattr(NegotiationService, "get", original_get)
    lookup = (await call(session, uid, tid, "lookup", {"client_create_keys": [str(key)]})).json()
    found = lookup["results"][0]
    assert lookup["success"] == 1 and found["reused"]
    assert found["negotiation_id"] == str(nid)
    assert found["negotiation"]["status"] == "草稿"
    assert found["negotiation"]["quote_amount"] == "500.00"
    retried = (await call(session, uid, tid, "create", body)).json()
    assert retried["success"] == 1 and retried["results"][0]["reused"]
    assert retried["results"][0]["negotiation_id"] == str(nid)
    assert (
        await session.scalar(
            text(
                "SELECT count(*) FROM negotiation WHERE tenant_id=:tid AND client_create_key=:key"
            ),
            {"tid": tid, "key": key},
        )
        == 1
    )
    assert (
        await session.scalar(
            text(
                "SELECT count(*) FROM line_event WHERE negotiation_id=:nid AND event_type='negotiation.created'"
            ),
            {"nid": nid},
        )
        == 1
    )


async def test_item_rolls_back_after_event_and_next_row_can_create(
    session, flow_users, product_factory, blogger_factory, monkeypatch
):
    uid, tid = flow_users.pr.id, flow_users.tenant.id
    payload = await ready_payload(product_factory, blogger_factory)
    body = create_body({**payload, "remark": "fail-after-event"}, payload)
    original = LineEventRepository.append

    async def fail_once(repo, **kwargs):
        event = await original(repo, **kwargs)
        if kwargs["event_type"] == LineEventType.NEGOTIATION_CREATED:
            remark = await repo._session.scalar(
                select(Negotiation.remark).where(Negotiation.id == kwargs["negotiation_id"])
            )
            if remark == "fail-after-event":
                raise RuntimeError("injected-before-commit")
        return event

    monkeypatch.setattr(LineEventRepository, "append", fail_once)
    # 提交夹具造数，后面的真实单项 rollback 不应回滚准备好的账号/商品。
    await session.commit()
    result = (await call(session, uid, tid, "create", body)).json()
    assert [row["status"] for row in result["results"]] == ["failed", "success"]
    assert result["results"][0]["code"] == "INTERNAL_ERROR"
    count = await session.scalar(
        text("SELECT count(*) FROM negotiation WHERE creation_group_id=:gid"),
        {"gid": UUID(body["creation_group_id"])},
    )
    assert count == 1
    events = await session.scalar(
        text(
            "SELECT count(*) FROM line_event WHERE tenant_id=:tid AND event_type='negotiation.created'"
        ),
        {"tid": tid},
    )
    assert events == 1
    # 未成功的原行键可修正后重试，不受不存在的摘要占位阻塞。
    body["items"][0]["payload"]["remark"] = "corrected"
    body["items"] = body["items"][:1]
    fixed = (await call(session, uid, tid, "create", body)).json()
    assert fixed["success"] == 1 and not fixed["results"][0]["reused"]


async def test_mixed_submit_permissions_and_original_two_level_sequence(
    session, flow_users, product_factory, blogger_factory
):
    pr, other, manager, boss, tid = (
        flow_users.pr.id,
        flow_users.pr2.id,
        flow_users.pr_manager.id,
        flow_users.admin.id,
        flow_users.tenant.id,
    )
    payload = await ready_payload(product_factory, blogger_factory)
    body = create_body(payload, {"blogger_id": payload["blogger_id"], "cooperation_mode": "送拍"})
    created = (await call(session, pr, tid, "create", body)).json()["results"]
    good, missing = (row["negotiation_id"] for row in created)
    theirs = (await call(session, other, tid, "create", create_body(payload))).json()["results"][0][
        "negotiation_id"
    ]
    result = (await call(session, pr, tid, "submit", actions(good, missing, theirs, good))).json()
    assert [r["status"] for r in result["results"]] == ["success", "failed", "skipped", "skipped"]
    assert result["results"][1]["code"] == "FLOW_GATE_MISSING"
    assert result["results"][2]["negotiation"] is None
    assert result["results"][3]["code"] == "DUPLICATE_ITEM"
    again = (await call(session, pr, tid, "submit", actions(good))).json()
    assert again["success"] == 0 and again["skipped"] == 1
    assert (
        await call(session, pr, tid, "review", actions(good, payload={"action": "approve"}))
    ).status_code == 403
    early = (
        await call(session, boss, tid, "final-review", actions(good, payload={"action": "approve"}))
    ).json()
    assert early["success"] == 0
    assert (
        await call(session, manager, tid, "review", actions(good, payload={"action": "approve"}))
    ).json()["success"] == 1
    assert (
        await call(session, boss, tid, "final-review", actions(good, payload={"action": "approve"}))
    ).json()["success"] == 1
    receiver = {
        "receiver_name": "收件人",
        "receiver_phone": "13800138000",
        "receiver_address": "杭州测试地址",
    }
    final = (
        await call(session, pr, tid, "confirm-receiver", actions(good, payload=receiver))
    ).json()
    assert final["success"] == 1
    promotion_id = final["results"][0]["negotiation"]["promotion_id"]
    assert promotion_id
    repeated = (
        await call(session, pr, tid, "confirm-receiver", actions(good, payload=receiver))
    ).json()
    assert repeated["success"] == 0 and repeated["skipped"] == 1
    assert (
        await session.scalar(
            text(
                "SELECT count(*) FROM promotion WHERE line_id=(SELECT line_id FROM negotiation WHERE id=:id)"
            ),
            {"id": UUID(good)},
        )
        == 1
    )


async def test_creation_identity_survives_reassign_without_exposing_document(
    session, flow_users, product_factory, blogger_factory
):
    pr, other, boss, tid = (
        flow_users.pr.id,
        flow_users.pr2.id,
        flow_users.admin.id,
        flow_users.tenant.id,
    )
    body = create_body(await ready_payload(product_factory, blogger_factory))
    created = (await call(session, pr, tid, "create", body)).json()["results"][0]
    nid = UUID(created["negotiation_id"])
    assert (await call(session, pr, tid, "submit", actions(nid))).json()["success"] == 1
    await NegotiationService(session).reassign(
        nid,
        NegotiationReassignRequest(pr_id=other, reason="正常转交"),
        await session.get(User, boss),
    )
    lookup = (
        await call(
            session,
            pr,
            tid,
            "lookup",
            {"client_create_keys": [body["items"][0]["client_create_key"]]},
        )
    ).json()["results"][0]
    assert (
        lookup["status"] == "skipped"
        and lookup["negotiation"] is None
        and lookup["negotiation_id"] is None
    )
    retry = (await call(session, pr, tid, "create", body)).json()["results"][0]
    assert retry["status"] == "skipped" and retry["negotiation_id"] is None
    row = (
        await session.execute(
            select(Negotiation.creation_pr_id, Negotiation.pr_id).where(Negotiation.id == nid)
        )
    ).one()
    assert row == (pr, other)
    assert (
        await session.scalar(
            text("SELECT count(*) FROM negotiation WHERE creation_group_id=:gid"),
            {"gid": UUID(body["creation_group_id"])},
        )
        == 1
    )
