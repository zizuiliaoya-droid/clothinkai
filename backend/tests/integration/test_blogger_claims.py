"""认领关键业务边界：使用真实角色、实际服务及真实数据库。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.exceptions import (
    FieldPermissionDenied,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride
from app.modules.blogger.claim_access import ensure_new_cooperation_allowed
from app.modules.blogger.claim_import import prepare_claim_import
from app.modules.blogger.claim_models import BloggerClaim
from app.modules.blogger.claim_service import WORK_WECHAT_TYPE, BloggerClaimService
from app.modules.blogger.repository import BloggerListFilters
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService
from app.modules.importer.outcome import BatchSeen, ImportRowContext
from app.modules.product.dict_models import DictItem

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.usefixtures("tenant_ctx")
@pytest.mark.parametrize("field", ["wechat", "phone"])
@pytest.mark.parametrize("overwrite", [False, True])
async def test_preparation_real_adapter_contact_deny(
    session, tenant_a, flow_users, blogger_factory, monkeypatch, field, overwrite
):
    from app.modules.importer.adapters.blogger import BloggerImportAdapter
    from app.modules.importer.duplicate_rules import DUPLICATE_RULES, DuplicatePolicy, DuplicateRule

    assert tenant_a.blogger_claims_enabled_at is None
    original = "original" if overwrite else None
    blogger = await blogger_factory.blogger(follower_count=100, **{field: original})
    permission = await session.scalar(
        select(Permission).where(Permission.scope == f"field.blogger.{field}:write")
    )
    assert permission is not None
    session.add(
        UserPermissionOverride(
            tenant_id=tenant_a.id,
            user_id=flow_users.pr.id,
            permission_id=permission.id,
            effect="revoke",
        )
    )
    await session.flush()
    policy = DuplicatePolicy.OVERWRITE if overwrite else DuplicatePolicy.COMPARE
    monkeypatch.setitem(
        DUPLICATE_RULES,
        "manual_blogger",
        DuplicateRule(policy, key="平台 + 账号", configurable=True),
    )
    ctx = ImportRowContext(tenant_a.id, "manual_blogger", None, 2, flow_users.pr.id, BatchSeen())
    outcome = await BloggerImportAdapter().upsert_with_context(
        {
            "xiaohongshu_id": blogger.xiaohongshu_id,
            "nickname": blogger.nickname,
            "platform": blogger.platform,
            field: "blocked",
            "follower_count": 200,
        },
        session=session,
        ctx=ctx,
    )
    await session.refresh(blogger)
    assert getattr(blogger, field) == original
    assert blogger.follower_count == 200
    assert any("无写入权限" in item for item in outcome.warnings)


@pytest.fixture
def tenant_ctx(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def work(session, tenant):
    row = DictItem(
        tenant_id=tenant.id, dict_type=WORK_WECHAT_TYPE, value="工作微信-" + uuid4().hex[:8]
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.usefixtures("tenant_ctx")
async def test_nonexclusive_idempotent_claim_and_original_wechat_check(
    session, tenant_a, flow_users, blogger_factory
):
    tenant_a.blogger_claims_enabled_at = datetime.now(UTC)
    b = await blogger_factory.blogger(wechat=" wx-original ")
    w = await work(session, tenant_a)
    svc = BloggerClaimService(session)
    assert await svc.self_claim(b.id, "wrong", w.id, flow_users.pr) == "not_verified"
    with pytest.raises(FieldPermissionDenied):
        await BloggerService(session).update_blogger(
            b.id, BloggerUpdate(wechat="mine"), flow_users.pr
        )
    assert b.wechat == " wx-original "
    assert await svc.self_claim(b.id, "wx-original", w.id, flow_users.pr) == "claimed"
    assert await svc.self_claim(b.id, "wx-original", w.id, flow_users.pr2) == "claimed"
    assert await svc.self_claim(b.id, "ignored", w.id, flow_users.pr) == "already_claimed"
    assert (
        await session.scalar(
            select(func.count()).select_from(BloggerClaim).where(BloggerClaim.blogger_id == b.id)
        )
        == 2
    )
    b2 = await blogger_factory.blogger(wechat=None)
    assert await svc.self_claim(b2.id, " ", w.id, flow_users.pr) == "not_verified"


@pytest.mark.usefixtures("tenant_ctx")
async def test_preparation_enable_search_read_clear_revoke_and_deny(
    session, tenant_a, flow_users, blogger_factory
):
    b = await blogger_factory.blogger(wechat="private-match-only", phone="13800138000")
    w = await work(session, tenant_a)
    claims = BloggerClaimService(session)
    service = BloggerService(session)
    assert (await service.get_blogger(b.id, flow_users.pr)).wechat == b.wechat
    await claims.enable(flow_users.pr_manager)
    assert (await service.get_blogger(b.id, flow_users.pr)).wechat is None
    result = await service.list_bloggers(
        filters=BloggerListFilters(keyword=b.wechat), page=1, page_size=20, user=flow_users.pr
    )
    assert result.total == 0
    with pytest.raises(FieldPermissionDenied):
        await service.update_blogger(b.id, BloggerUpdate(wechat=None), flow_users.pr)
    await claims.assign(b.id, flow_users.pr.id, w.id, flow_users.pr_manager)
    assert (await service.get_blogger(b.id, flow_users.pr)).wechat == b.wechat
    permission = await session.scalar(
        select(Permission).where(Permission.scope == "field.blogger.wechat:read")
    )
    assert permission is not None
    session.add(
        UserPermissionOverride(
            tenant_id=tenant_a.id,
            user_id=flow_users.pr.id,
            permission_id=permission.id,
            effect="revoke",
        )
    )
    await session.flush()
    assert (await service.get_blogger(b.id, flow_users.pr)).wechat is None
    await claims.revoke(b.id, flow_users.pr.id, flow_users.pr_manager)
    with pytest.raises(PermissionDeniedError):
        await ensure_new_cooperation_allowed(session, flow_users.pr, b.id)


@pytest.mark.usefixtures("tenant_ctx")
async def test_create_auto_claim_manager_transfer_and_stopped_work_wechat(
    session, tenant_a, flow_users
):
    w = await work(session, tenant_a)
    result = await BloggerService(session).create_blogger(
        BloggerCreate(
            xiaohongshu_id="auto-" + uuid4().hex[:8], nickname="新博主", claim_work_wechat_id=w.id
        ),
        flow_users.pr,
    )
    assert result.claimed_by_me and result.claim_count == 1
    claims = BloggerClaimService(session)
    await claims.transfer(
        result.id, flow_users.pr.id, flow_users.pr2.id, w.id, flow_users.pr_manager
    )
    rows = await claims.list_claims(result.id, flow_users.pr_manager)
    assert [r.pr_id for r in rows] == [flow_users.pr2.id]
    w.is_active = False
    await session.flush()
    assert (await claims.list_claims(result.id, flow_users.pr2))[0].work_wechat_active is False
    with pytest.raises(ValidationError):
        await claims.switch_work_wechat(result.id, flow_users.pr2.id, w.id, flow_users.pr2)


@pytest.mark.usefixtures("tenant_ctx")
async def test_cross_tenant_blogger_pr_and_work_rejected(
    session, tenant_a, tenant_b, flow_users, factory, pr_role, blogger_factory
):
    own = await blogger_factory.blogger()
    other = await blogger_factory.blogger(tenant=tenant_b)
    other_pr = await factory.user(tenant_b, roles=[pr_role])
    own_work, other_work = await work(session, tenant_a), await work(session, tenant_b)
    svc = BloggerClaimService(session)
    with pytest.raises(ResourceNotFoundError):
        await svc.assign(other.id, flow_users.pr.id, own_work.id, flow_users.pr_manager)
    with pytest.raises(ValidationError):
        await svc.assign(own.id, other_pr.id, own_work.id, flow_users.pr_manager)
    with pytest.raises(ValidationError):
        await svc.assign(own.id, flow_users.pr.id, other_work.id, flow_users.pr_manager)


@pytest.mark.usefixtures("tenant_ctx")
async def test_worker_cannot_fill_unclaimed_contacts_or_override_personal_deny(
    session, tenant_a, flow_users, blogger_factory
):
    tenant_a.blogger_claims_enabled_at = datetime.now(UTC)
    b = await blogger_factory.blogger(wechat="original")
    ctx = ImportRowContext(tenant_a.id, "manual_blogger", None, 2, flow_users.pr.id, BatchSeen())
    warnings = []
    parsed = await prepare_claim_import(
        session, ctx, {"wechat": "different", "phone": "123", "nickname": "same"}, b, warnings
    )
    assert parsed == {"nickname": "same"} and warnings
    assert b.wechat == "original"
    w = await work(session, tenant_a)
    await BloggerClaimService(session).assign(b.id, flow_users.pr.id, w.id, flow_users.pr_manager)
    permission = await session.scalar(
        select(Permission).where(Permission.scope == "field.blogger.wechat:write")
    )
    assert permission is not None
    session.add(
        UserPermissionOverride(
            tenant_id=tenant_a.id,
            user_id=flow_users.pr.id,
            permission_id=permission.id,
            effect="revoke",
        )
    )
    await session.flush()
    parsed = await prepare_claim_import(session, ctx, {"wechat": "changed", "phone": "123"}, b, [])
    assert parsed == {"phone": "123"}


@pytest.mark.usefixtures("tenant_ctx")
async def test_all_new_cooperation_entries_check_claims_but_old_draft_can_save(
    session, tenant_a, flow_users, blogger_factory, product_factory, negotiation_factory
):
    from app.modules.importer.adapters.promotion import PromotionImportAdapter
    from app.modules.negotiation.schemas import NegotiationCreate, NegotiationUpdate
    from app.modules.negotiation.service import NegotiationService
    from app.modules.promotion.enums import CooperationMode
    from app.modules.promotion.schemas import PromotionCreate
    from app.modules.promotion.service import PromotionService

    style = await product_factory.style()
    old, new = await blogger_factory.blogger(), await blogger_factory.blogger()
    draft = await negotiation_factory.negotiation(style=style, blogger=old, pr=flow_users.pr)
    rejected = await negotiation_factory.negotiation(
        style=style,
        blogger=old,
        pr=flow_users.pr,
        status="审核驳回",
        submitted_at=datetime.now(UTC),
    )
    tenant_a.blogger_claims_enabled_at = datetime.now(UTC)
    await session.flush()
    service = NegotiationService(session)
    with pytest.raises(PermissionDeniedError, match="请先认领"):
        await service.create(
            NegotiationCreate(
                style_id=style.id, blogger_id=new.id, cooperation_mode=CooperationMode.GIFT
            ),
            flow_users.pr,
        )
    with pytest.raises(PermissionDeniedError, match="请先认领"):
        await service.copy(rejected.id, flow_users.pr)
    with pytest.raises(PermissionDeniedError, match="请先认领"):
        await service.update(draft.id, NegotiationUpdate(blogger_id=new.id), flow_users.pr)
    with pytest.raises(PermissionDeniedError, match="请先认领"):
        await PromotionService(session).create_promotion(
            PromotionCreate(
                style_id=style.id,
                blogger_id=new.id,
                platform="小红书",
                cooperation_mode=CooperationMode.GIFT,
            ),
            flow_users.pr,
        )
    with pytest.raises(PermissionDeniedError, match="请先认领"):
        await PromotionImportAdapter().upsert(
            {
                "style_code": style.style_code,
                "xiaohongshu_id": new.xiaohongshu_id,
                "platform": new.platform,
            },
            session=session,
            tenant_id=tenant_a.id,
            actor_id=flow_users.pr.id,
        )
    updated = await service.update(draft.id, NegotiationUpdate(remark="旧单继续"), flow_users.pr)
    assert updated.pr_id == flow_users.pr.id and updated.remark == "旧单继续"


@pytest.mark.usefixtures("tenant_ctx")
async def test_revoke_does_not_block_existing_negotiation_finalizing(
    session, tenant_a, flow_users, blogger_factory, product_factory, negotiation_factory
):
    from app.modules.negotiation.schemas import NegotiationReceiverConfirm
    from app.modules.negotiation.service import NegotiationService

    style = await product_factory.style()
    sku = await product_factory.sku(style)
    blogger = await blogger_factory.blogger()
    w = await work(session, tenant_a)
    claims = BloggerClaimService(session)
    await claims.assign(blogger.id, flow_users.pr.id, w.id, flow_users.pr_manager)
    n = await negotiation_factory.negotiation(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        status="待补收货地址",
        submitted_at=datetime.now(UTC),
        items=[(style, sku)],
    )
    await claims.enable(flow_users.pr_manager)
    await claims.revoke(blogger.id, flow_users.pr.id, flow_users.pr_manager)
    result = await NegotiationService(session).confirm_receiver(
        n.id,
        NegotiationReceiverConfirm(
            receiver_name="张三",
            receiver_phone="13812345678",
            receiver_address="浙江省杭州市西湖区某路1号",
        ),
        flow_users.pr,
    )
    assert (
        result.status == "已定稿"
        and result.promotion_id is not None
        and result.pr_id == flow_users.pr.id
    )


async def test_support_cannot_claim_change_work_wechat_or_enable(
    session, factory, tenant_a, admin_role, monkeypatch
):
    from app.modules.auth import support_access
    from tests.integration.test_platform_support import client
    from tests.integration.test_tenant_management import context, users

    accounts = await users(session, factory, tenant_a, admin_role)
    with context(accounts.platform):
        issued = await support_access.issue_support_token(session, accounts.platform, tenant_a.id)
    async with client(session, monkeypatch) as http:
        headers = {"Authorization": "Bearer " + issued["access_token"]}
        for path, body in (
            ("/api/bloggers/claims/self", {"items": []}),
            ("/api/bloggers/work-wechats", {"value": "support-must-not-create"}),
            ("/api/bloggers/claim-settings/enable", {}),
        ):
            response = await http.post(path, json=body, headers=headers)
            assert response.status_code == 403 and response.json()["code"] == "SUPPORT_READ_ONLY"
