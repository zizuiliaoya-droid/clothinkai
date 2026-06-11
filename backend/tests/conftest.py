"""pytest 全局 fixtures。

设计要点：
- 使用真实 PostgreSQL（pytest-postgresql 启动一次性实例 / 或环境变量复用本地 PG）
- 每个测试一个事务 + 自动 rollback（避免污染）
- 默认连接 clothing_bypass 角色（绕过 RLS），独立的 RLS 测试通过 set_rls_user fixture 切到 clothing_app 角色
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# 预先导入所有模块的 ORM models，确保 Base.metadata 完整 —— 否则跨模块外键
# （如 settlement.promotion_id → promotion.id / attachment.id）在 mapper 配置时
# 报 NoReferencedTableError。app 启动靠 router 链式 import 全部 models，测试需显式补齐。
import app.core.attachment  # noqa: F401, E402
import app.modules.auth.models  # noqa: F401, E402
import app.modules.blogger.models  # noqa: F401, E402
import app.modules.finance.models  # noqa: F401, E402
import app.modules.finance.order_adjustment_models  # noqa: F401, E402  (U16 order_adjustment/balance_record)
import app.modules.product.models  # noqa: F401, E402
import app.modules.promotion.models  # noqa: F401, E402

# 测试用的 DB URL 通过环境变量注入；CI 由 docker-compose / pytest-postgresql 提供
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL_BYPASS",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/clothing_erp_test",
)


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """让 pytest-asyncio 用单一事件循环策略。"""
    import asyncio as _asyncio

    return _asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[Any]:
    """函数级引擎（每个测试独立 engine + event loop，避免 asyncpg 跨 loop 清理报错）。

    NullPool：不复用连接，测试结束即关闭，杜绝 "greenlet is being finalized" /
    "attached to a different loop" 等异步连接生命周期问题。
    """
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(
        TEST_DATABASE_URL, echo=False, future=True, poolclass=NullPool
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: Any) -> AsyncIterator[AsyncSession]:
    """每个测试一个事务，结束自动 rollback。

    用 ``join_transaction_mode="create_savepoint"``（SQLAlchemy 2.0）：被测代码
    调用 ``session.commit()`` 时只 release SAVEPOINT，外层真实事务保持打开，
    fixture 结束统一 rollback。否则 service 层 commit 会提交外层事务并让
    asyncpg 连接状态错乱（"another operation is in progress"）。
    """
    connection = await engine.connect()
    transaction = await connection.begin()
    Session = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        class_=AsyncSession,
        join_transaction_mode="create_savepoint",
    )
    sess = Session()

    try:
        yield sess
    finally:
        await sess.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


# ---------------------------------------------------------------------------
# 业务 fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def tenant_a(session: AsyncSession) -> Any:
    """创建测试租户 A。"""
    from app.modules.auth.models import Tenant

    t = Tenant(id=uuid4(), code=f"test_a_{uuid4().hex[:6]}", name="Test Tenant A")
    session.add(t)
    await session.flush()
    return t


@pytest_asyncio.fixture
async def tenant_b(session: AsyncSession) -> Any:
    from app.modules.auth.models import Tenant

    t = Tenant(id=uuid4(), code=f"test_b_{uuid4().hex[:6]}", name="Test Tenant B")
    session.add(t)
    await session.flush()
    return t


@pytest_asyncio.fixture
async def admin_role(session: AsyncSession) -> Any:
    """从 seed 数据中加载 admin role；如不存在则创建。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "admin"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="admin", name="管理员", is_system=True)
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def designer_role(session: AsyncSession) -> Any:
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "designer"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="designer", name="设计师", is_system=True)
        session.add(role)
        await session.flush()
    return role


def _make_user_record(tenant_id: UUID, **kwargs: Any) -> Any:
    from app.core.security.auth import hash_password
    from app.modules.auth.models import User

    return User(
        id=kwargs.get("id", uuid4()),
        tenant_id=tenant_id,
        username=kwargs.get("username", f"user_{uuid4().hex[:6]}"),
        password_hash=kwargs.get("password_hash", hash_password("Password123")),
        display_name=kwargs.get("display_name", None),
        email=kwargs.get("email", None),
        status=kwargs.get("status", "active"),
        password_must_change=kwargs.get("password_must_change", False),
    )


@pytest_asyncio.fixture
async def factory(session: AsyncSession) -> Any:
    """统一的测试数据工厂。"""

    class Factory:
        async def user(self, tenant: Any, **kwargs: Any) -> Any:
            from app.modules.auth.models import UserRole

            user = _make_user_record(tenant.id, **kwargs)
            session.add(user)
            await session.flush()
            roles = kwargs.get("roles", [])
            for role in roles:
                session.add(
                    UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id)
                )
            await session.flush()
            return user

    return Factory()


# ---------------------------------------------------------------------------
# U02 product 模块 fixtures（追加）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def follower_role(session: AsyncSession) -> Any:
    """跟单角色（U02 价格字段可见）。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "merchandiser"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(
            id=uuid4(), code="merchandiser", name="跟单", is_system=True
        )
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def finance_role(session: AsyncSession) -> Any:
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "finance"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="finance", name="财务", is_system=True)
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def pr_role(session: AsyncSession) -> Any:
    """PR 角色（U02 价格字段不可见）。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "pr"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="pr", name="PR", is_system=True)
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def product_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U02 测试数据工厂：brand / style / sku / 角色绑定。"""
    from app.core.tenancy import tenant_id_ctx
    from app.modules.auth.models import UserRole
    from app.modules.product.models import Brand, Sku, Style

    class ProductFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def assign_role(self, user: Any, role: Any) -> None:
            session.add(
                UserRole(
                    tenant_id=user.tenant_id, user_id=user.id, role_id=role.id
                )
            )
            await session.flush()

        async def brand(
            self, tenant: Any | None = None, **kw: Any
        ) -> Brand:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                b = Brand(
                    tenant_id=t.id,
                    brand_code=kw.get("brand_code", f"BR{uuid4().hex[:6]}"),
                    brand_name=kw.get("brand_name", "测试品牌"),
                    is_active=kw.get("is_active", True),
                )
                session.add(b)
                await session.flush()
                return b
            finally:
                tenant_id_ctx.reset(tok)

        async def style(
            self, tenant: Any | None = None, **kw: Any
        ) -> Style:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                s = Style(
                    tenant_id=t.id,
                    style_code=kw.get("style_code", f"ST{uuid4().hex[:6]}"),
                    style_name=kw.get("style_name", "测试款式"),
                    short_name=kw.get("short_name"),
                    brand_id=kw.get("brand_id"),
                    category=kw.get("category", "连衣裙"),
                    season=kw.get("season"),
                    gender=kw.get("gender"),
                    tags=kw.get("tags", []),
                    tag_color=kw.get("tag_color", []),
                    main_image_key=kw.get("main_image_key"),
                    remark=kw.get("remark"),
                    owner_id=kw.get("owner_id"),
                    design_status=kw.get("design_status", "大货"),
                    is_active=kw.get("is_active", True),
                    is_deleted=kw.get("is_deleted", False),
                )
                session.add(s)
                await session.flush()
                return s
            finally:
                tenant_id_ctx.reset(tok)

        async def sku(
            self,
            style: Style,
            tenant: Any | None = None,
            **kw: Any,
        ) -> Sku:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                from decimal import Decimal

                sk = Sku(
                    tenant_id=t.id,
                    style_id=style.id,
                    sku_code=kw.get("sku_code", f"SK{uuid4().hex[:6]}"),
                    color=kw.get("color", "红"),
                    size=kw.get("size", "M"),
                    cost_price=kw.get("cost_price", Decimal("100.00")),
                    purchase_price=kw.get("purchase_price"),
                    base_price=kw.get("base_price", Decimal("200.00")),
                    sourcing_type=kw.get("sourcing_type", "自产"),
                    is_active=kw.get("is_active", True),
                    is_deleted=kw.get("is_deleted", False),
                )
                session.add(sk)
                await session.flush()
                return sk
            finally:
                tenant_id_ctx.reset(tok)

    return ProductFactory(tenant_a)


# ---------------------------------------------------------------------------
# U03 blogger 模块 fixtures（追加）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def pr_manager_role(session: AsyncSession) -> Any:
    """PR 主管角色（U03 quote/wechat/phone 全可见可写）。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "pr_manager"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="pr_manager", name="PR 主管", is_system=True)
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def blogger_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U03 测试数据工厂."""
    from app.core.tenancy import tenant_id_ctx
    from app.modules.blogger.models import Blogger

    class BloggerFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def blogger(
            self, tenant: Any | None = None, **kw: Any
        ) -> Blogger:
            from decimal import Decimal

            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                b = Blogger(
                    tenant_id=t.id,
                    xiaohongshu_id=kw.get(
                        "xiaohongshu_id", f"XHS{uuid4().hex[:8]}"
                    ),
                    nickname=kw.get("nickname", "测试博主"),
                    platform=kw.get("platform", "小红书"),
                    wechat=kw.get("wechat"),
                    phone=kw.get("phone"),
                    follower_count=kw.get("follower_count"),
                    blogger_type=kw.get("blogger_type"),
                    gender_target=kw.get("gender_target"),
                    category_tags=kw.get("category_tags", []),
                    quality_tags=kw.get("quality_tags", []),
                    quote=kw.get("quote", Decimal("500.00")),
                    cooperation_history=kw.get("cooperation_history"),
                    remark=kw.get("remark"),
                    is_suspected_fake=kw.get("is_suspected_fake", False),
                    audience_profile=kw.get("audience_profile"),
                    is_active=kw.get("is_active", True),
                    is_deleted=kw.get("is_deleted", False),
                )
                session.add(b)
                await session.flush()
                return b
            finally:
                tenant_id_ctx.reset(tok)

    return BloggerFactory(tenant_a)


# ---------------------------------------------------------------------------
# U04 promotion 模块 fixtures（追加）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _clear_event_handlers() -> AsyncIterator[None]:
    """每个测试前清空事件总线（FB6：防测试间累计 handler 污染）。"""
    from app.core.events import clear_handlers

    clear_handlers()
    yield
    clear_handlers()


@pytest_asyncio.fixture
async def promotion_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U04 测试数据工厂."""
    from datetime import date as _date
    from decimal import Decimal

    from app.core.tenancy import tenant_id_ctx
    from app.modules.promotion.enums import (
        PublishStatus,
        RecallStatus,
        SettlementStatus,
    )
    from app.modules.promotion.models import Promotion

    class PromotionFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def promotion(
            self,
            *,
            style: Any,
            blogger: Any,
            pr: Any | None = None,
            tenant: Any | None = None,
            **kw: Any,
        ) -> Promotion:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                p = Promotion(
                    tenant_id=t.id,
                    style_id=style.id,
                    sku_id=kw.get("sku_id"),
                    blogger_id=blogger.id,
                    pr_id=kw.get("pr_id", pr.id if pr else None),
                    internal_code=kw.get(
                        "internal_code",
                        f"DE2605260{uuid4().hex[:3].upper()}",
                    ),
                    style_code_snapshot=kw.get(
                        "style_code_snapshot",
                        getattr(style, "style_code", "ST000"),
                    ),
                    style_short_name_snapshot=kw.get(
                        "style_short_name_snapshot",
                        getattr(style, "short_name", None)
                        or getattr(style, "style_name", "测试款式"),
                    ),
                    quote_amount=kw.get("quote_amount", Decimal("500.00")),
                    cost_snapshot=kw.get("cost_snapshot"),
                    platform=kw.get("platform", "小红书"),
                    cooperation_date=kw.get(
                        "cooperation_date", _date(2026, 5, 26)
                    ),
                    scheduled_publish_date=kw.get("scheduled_publish_date"),
                    actual_publish_date=kw.get("actual_publish_date"),
                    publish_url=kw.get("publish_url"),
                    note_title=kw.get("note_title"),
                    remark=kw.get("remark"),
                    publish_status=kw.get(
                        "publish_status", PublishStatus.UNPUBLISHED.value
                    ),
                    recall_status=kw.get(
                        "recall_status", RecallStatus.NOT_RECALLED.value
                    ),
                    settlement_status=kw.get(
                        "settlement_status",
                        SettlementStatus.NOT_REVIEWED.value,
                    ),
                    is_active=kw.get("is_active", True),
                    like_count=kw.get("like_count"),
                )
                session.add(p)
                await session.flush()
                return p
            finally:
                tenant_id_ctx.reset(tok)

    return PromotionFactory(tenant_a)


@pytest_asyncio.fixture
async def event_capture() -> AsyncIterator[Any]:
    """订阅事件总线，把 dispatch 的事件 push 到 list 供测试断言。"""
    from app.core.events import clear_handlers, subscribe

    captured: list[Any] = []

    async def _settlement_handler(event: Any, _session: Any) -> None:
        captured.append(event)

    async def _published_handler(event: Any, _session: Any) -> None:
        captured.append(event)

    async def _paid_handler(event: Any, _session: Any) -> None:
        captured.append(event)

    subscribe("SettlementRequested", _settlement_handler)
    subscribe("PromotionPublished", _published_handler)
    subscribe("SettlementPaid", _paid_handler)
    yield captured
    clear_handlers()


# ---------------------------------------------------------------------------
# U05 finance 模块 fixtures（追加）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def attachment_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U05 attachment 测试数据工厂（shared 基础设施）。

    默认创建一个满足 ProofAttachmentValidator 6 项校验的 ready 附件。
    可通过 kwargs 覆盖任一字段以构造各类失败用例。
    """
    from app.core.attachment import Attachment
    from app.core.tenancy import tenant_id_ctx

    class AttachmentFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def attachment(
            self, tenant: Any | None = None, **kw: Any
        ) -> Attachment:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                att = Attachment(
                    id=kw.get("id", uuid4()),
                    tenant_id=t.id,
                    bucket=kw.get("bucket", "private"),
                    r2_key=kw.get(
                        "r2_key", f"{t.id}/settlement_proof/{uuid4().hex}/proof.jpg"
                    ),
                    purpose=kw.get("purpose", "settlement_proof"),
                    filename=kw.get("filename", "proof.jpg"),
                    mime_type=kw.get("mime_type", "image/jpeg"),
                    size_bytes=kw.get("size_bytes", 1024),
                    status=kw.get("status", "ready"),
                    created_by=kw.get("created_by"),
                )
                session.add(att)
                await session.flush()
                return att
            finally:
                tenant_id_ctx.reset(tok)

    return AttachmentFactory(tenant_a)


@pytest_asyncio.fixture
async def settlement_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U05 settlement 测试数据工厂。

    直接落 Settlement 行（绕过 listener），用于测试 review / fill_payment /
    mark_paid / extra_item / daily_summary 等下游流程。
    """
    from datetime import date as _date
    from decimal import Decimal

    from app.core.tenancy import tenant_id_ctx
    from app.modules.finance.enums import SettlementStatus
    from app.modules.finance.models import Settlement

    class SettlementFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def _ensure_promotion(
            self, *, tenant: Any, style: Any, blogger: Any, pr: Any | None
        ) -> Any:
            """创建一个满足 FK 的真实 promotion 行（settlement.promotion_id 非空 + FK）。"""
            from datetime import date as _date

            from app.modules.promotion.models import Promotion

            p = Promotion(
                id=uuid4(),
                tenant_id=tenant.id,
                style_id=style.id,
                blogger_id=blogger.id,
                pr_id=pr.id if pr else None,
                internal_code=f"DE{uuid4().hex[:12].upper()}",
                style_code_snapshot=getattr(style, "style_code", "ST000"),
                style_short_name_snapshot=(
                    getattr(style, "short_name", None)
                    or getattr(style, "style_name", "测试款式")
                ),
                quote_amount=Decimal("500.00"),
                platform="小红书",
                cooperation_date=_date(2026, 5, 26),
                publish_status="已发布",
                recall_status="未召回",
                settlement_status="待付款",
                is_active=True,
            )
            session.add(p)
            await session.flush()
            return p

        async def settlement(
            self,
            *,
            style: Any,
            blogger: Any,
            promotion: Any | None = None,
            pr: Any | None = None,
            tenant: Any | None = None,
            **kw: Any,
        ) -> Settlement:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                # FK 完整性：promotion_id 非空且必须存在于 promotion 表
                if "promotion_id" not in kw:
                    if promotion is None:
                        promotion = await self._ensure_promotion(
                            tenant=t, style=style, blogger=blogger, pr=pr
                        )
                    resolved_promotion_id = promotion.id
                else:
                    resolved_promotion_id = kw["promotion_id"]

                s = Settlement(
                    id=kw.get("id", uuid4()),
                    tenant_id=t.id,
                    promotion_id=resolved_promotion_id,
                    blogger_id=blogger.id,
                    style_id=style.id,
                    pr_id=kw.get("pr_id", pr.id if pr else None),
                    settlement_no=kw.get(
                        "settlement_no", f"TES{uuid4().hex[:12]}"
                    ),
                    amount=kw.get("amount", Decimal("500.00")),
                    total_amount=kw.get("total_amount", Decimal("500.00")),
                    payment_amount=kw.get("payment_amount"),
                    payment_date=kw.get("payment_date"),
                    payment_proof_attachment_id=kw.get(
                        "payment_proof_attachment_id"
                    ),
                    note_title=kw.get("note_title"),
                    remark=kw.get("remark"),
                    settlement_status=kw.get(
                        "settlement_status",
                        SettlementStatus.PENDING_REVIEW.value,
                    ),
                    reviewed_by=kw.get("reviewed_by"),
                    reviewed_at=kw.get("reviewed_at"),
                    review_action=kw.get("review_action"),
                    review_reason=kw.get("review_reason"),
                    paid_by=kw.get("paid_by"),
                    request_event_id=kw.get("request_event_id", uuid4()),
                )
                session.add(s)
                await session.flush()
                return s
            finally:
                tenant_id_ctx.reset(tok)

    _ = _date  # 保留 import（部分测试通过 kwargs 传 date）
    return SettlementFactory(tenant_a)


@pytest_asyncio.fixture
async def cross_unit_event_bus() -> AsyncIterator[Any]:
    """注册 U05 finance + U04 promotion 双向 listener（端到端跨单元事件测试用）。

    与 event_capture（只捕获不处理）不同：本 fixture 注册真实 handler，
    用于 test_e2e_review_to_paid / test_settlement_create_via_event /
    test_settlement_paid_listener 等需要真实事件链路的测试。
    """
    from app.core.events import clear_handlers
    from app.modules.finance.listeners import register as register_finance
    from app.modules.promotion.listeners import register as register_promotion

    clear_handlers()
    register_finance()
    register_promotion()
    yield
    clear_handlers()


# ---------------------------------------------------------------------------
# U06a importer 模块 fixtures（追加）
# ---------------------------------------------------------------------------

# 显式导入 importer ORM models（跨模块 FK mapper 配置完整性）
import app.modules.importer.models  # noqa: F401, E402
import app.modules.wecom.models  # noqa: F401, E402  (U07 wecom 5 表)
import app.modules.credential.models  # noqa: F401, E402  (U12 credential)
import app.modules.collect.models  # noqa: F401, E402  (U13 采集 5 表)
import app.modules.report.work_progress_models  # noqa: F401, E402  (U14 target_planning/store_daily)
import app.modules.report.user_preference_models  # noqa: F401, E402  (U17 user_preference)
import app.modules.product.bundle_models  # noqa: F401, E402  (U17 bundle_product/bundle_item)
import app.modules.ai.models  # noqa: F401, E402  (U18 ai_advice_log)
import app.modules.wecom.alert_models  # noqa: F401, E402  (U15 wecom_alert_config/log)


class FakeImportAdapter:
    """测试用导入 Adapter（不依赖真实业务表）。

    upsert 写一行到 ``brand`` 表（U02 真实租户表，受 RLS 约束），用于验证
    runner per-row 事务 + SET LOCAL + RLS 隔离。可通过 fail_on_rows 模拟行级失败。
    """

    source = "fake_source"
    target_table = "brand"

    def __init__(self, fail_on_rows: set[int] | None = None) -> None:
        self.fail_on_rows = fail_on_rows or set()
        self.parsed_rows: list[dict[str, Any]] = []

    def parse_row(self, row: dict[str, Any], mapping: Any) -> dict[str, Any]:
        # 恒等映射 + 记录 row_index（用于失败模拟）
        parsed = dict(row)
        self.parsed_rows.append(parsed)
        return parsed

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        # raw_data 中 _force_fail=1 → 行校验失败
        if str(parsed.get("_force_fail", "")) == "1":
            return ["forced row failure"]
        return []

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        from app.modules.product.models import Brand

        brand = Brand(
            id=uuid4(),
            tenant_id=tenant_id,
            brand_code=parsed.get("brand_code", f"FK{uuid4().hex[:8]}"),
            brand_name=parsed.get("brand_name", "fake"),
            is_active=True,
        )
        session.add(brand)
        await session.flush()
        return brand.id, True


@pytest_asyncio.fixture
async def operations_role(session: AsyncSession) -> Any:
    from sqlalchemy import select

    from app.modules.auth.models import Role

    role = (
        await session.execute(select(Role).where(Role.code == "operations"))
    ).scalar_one_or_none()
    if role is None:
        role = Role(id=uuid4(), code="operations", name="运营", is_system=True)
        session.add(role)
        await session.flush()
    return role


@pytest_asyncio.fixture
async def import_batch_factory(session: AsyncSession, tenant_a: Any) -> Any:
    """U06a import_batch 测试数据工厂。"""
    from app.core.tenancy import tenant_id_ctx
    from app.modules.importer.models import ImportBatch

    class ImportBatchFactory:
        def __init__(self, default_tenant: Any) -> None:
            self.default_tenant = default_tenant

        async def batch(self, tenant: Any | None = None, **kw: Any) -> ImportBatch:
            t = tenant or self.default_tenant
            tok = tenant_id_ctx.set(t.id)
            try:
                b = ImportBatch(
                    id=kw.get("id", uuid4()),
                    tenant_id=t.id,
                    source=kw.get("source", "fake_source"),
                    file_hash=kw.get("file_hash", uuid4().hex),
                    original_filename=kw.get("original_filename", "test.csv"),
                    file_r2_key=kw.get(
                        "file_r2_key", f"imports/{t.id}/{uuid4()}/test.csv"
                    ),
                    file_bucket=kw.get("file_bucket", "private"),
                    mapping_version=kw.get("mapping_version"),
                    status=kw.get("status", "processing"),
                    total_rows=kw.get("total_rows", 0),
                    imported=kw.get("imported", 0),
                    failed=kw.get("failed", 0),
                    retry_count=kw.get("retry_count", 0),
                    error_summary=kw.get("error_summary"),
                    created_by=kw.get("created_by"),
                )
                session.add(b)
                await session.flush()
                return b
            finally:
                tenant_id_ctx.reset(tok)

    return ImportBatchFactory(tenant_a)
