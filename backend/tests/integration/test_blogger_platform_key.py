"""8b-1 博主按（平台, 账号）唯一：CRUD、软删释放、大小写、upsert、审计、搜索（设计 §7.1、§3.9、§10）。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog
from app.modules.blogger.enums import Platform
from app.modules.blogger.exceptions import BloggerXhsIdConflictError
from app.modules.blogger.repository import BloggerListFilters, BloggerRepository
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _admin(factory: Any, tenant: Any, admin_role: Any) -> Any:
    return await factory.user(tenant, roles=[admin_role])


@pytest.mark.usefixtures("tenant_ctx")
class TestCreate:
    async def test_same_account_other_platform_coexists(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        user = await _admin(factory, tenant_a, admin_role)
        svc = BloggerService(session)
        xhs = await svc.create_blogger(BloggerCreate(xiaohongshu_id="K8B1", nickname="甲"), user)
        dy = await svc.create_blogger(
            BloggerCreate(xiaohongshu_id="K8B1", nickname="乙", platform=Platform.DOUYIN), user
        )
        assert xhs.id != dy.id
        assert (xhs.platform, dy.platform) == ("小红书", "抖音")

    async def test_same_key_conflict_details(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        existing = await blogger_factory.blogger(xiaohongshu_id="K8B2", platform="抖音")
        existing_id = existing.id
        user = await _admin(factory, tenant_a, admin_role)
        with pytest.raises(BloggerXhsIdConflictError) as exc:
            await BloggerService(session).create_blogger(
                BloggerCreate(xiaohongshu_id="K8B2", nickname="x", platform=Platform.DOUYIN), user
            )
        assert exc.value.message == "抖音 已有账号 K8B2 的博主"
        assert exc.value.details == {
            "platform": "抖音",
            "xiaohongshu_id": "K8B2",
            "existing_blogger_id": str(existing_id),
        }

    async def test_soft_deleted_releases_key(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        await blogger_factory.blogger(xiaohongshu_id="K8B3", is_deleted=True, is_active=False)
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).create_blogger(
            BloggerCreate(xiaohongshu_id="K8B3", nickname="新"), user
        )
        assert resp.xiaohongshu_id == "K8B3"

    async def test_case_different_coexists(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        await blogger_factory.blogger(xiaohongshu_id="k8bcase")
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).create_blogger(
            BloggerCreate(xiaohongshu_id="K8BCASE", nickname="大写"), user
        )
        assert resp.xiaohongshu_id == "K8BCASE"

    async def test_new_fields_saved_and_audit_has_platform(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).create_blogger(
            BloggerCreate(
                xiaohongshu_id="ab.c_9",
                nickname="抖音号",
                platform=Platform.DOUYIN,
                web_id="61900000001",
                homepage_url="https://www.douyin.com/user/fake",
            ),
            user,
        )
        assert resp.web_id == "61900000001"
        assert resp.homepage_url == "https://www.douyin.com/user/fake"
        assert resp.platform_metrics is None
        log = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "blogger.create", AuditLog.resource_id == str(resp.id)
                )
            )
        ).scalar_one()
        assert log.after["platform"] == "抖音"
        assert log.after["xiaohongshu_id"] == "ab.c_9"


@pytest.mark.usefixtures("tenant_ctx")
class TestUpdate:
    async def test_change_platform_collides(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        other = await blogger_factory.blogger(xiaohongshu_id="K8U1", platform="抖音")
        other_id = other.id
        mine = await blogger_factory.blogger(xiaohongshu_id="K8U1", platform="小红书")
        user = await _admin(factory, tenant_a, admin_role)
        with pytest.raises(BloggerXhsIdConflictError) as exc:
            await BloggerService(session).update_blogger(
                mine.id, BloggerUpdate(platform=Platform.DOUYIN), user
            )
        assert exc.value.details["platform"] == "抖音"
        assert exc.value.details["existing_blogger_id"] == str(other_id)

    async def test_change_account_collides_same_platform_only(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        await blogger_factory.blogger(xiaohongshu_id="K8U2", platform="抖音")
        mine = await blogger_factory.blogger(xiaohongshu_id="K8U2X", platform="小红书")
        mine_id = mine.id
        user = await _admin(factory, tenant_a, admin_role)
        svc = BloggerService(session)
        # 抖音那个不挡小红书改成同号
        resp = await svc.update_blogger(mine_id, BloggerUpdate(xiaohongshu_id="K8U2"), user)
        assert resp.xiaohongshu_id == "K8U2"
        await blogger_factory.blogger(xiaohongshu_id="K8U2Y", platform="小红书")
        with pytest.raises(BloggerXhsIdConflictError):
            await svc.update_blogger(mine_id, BloggerUpdate(xiaohongshu_id="K8U2Y"), user)

    async def test_platform_change_free_key_ok_and_audited(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        mine = await blogger_factory.blogger(xiaohongshu_id="K8U3", platform="小红书")
        mine_id = mine.id
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).update_blogger(
            mine_id,
            BloggerUpdate(platform=Platform.DOUYIN, web_id="123", homepage_url="https://a.com/u"),
            user,
        )
        assert (resp.platform, resp.web_id, resp.homepage_url) == ("抖音", "123", "https://a.com/u")
        log = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "blogger.update", AuditLog.resource_id == str(mine_id)
                )
            )
        ).scalar_one()
        assert log.before == {"platform": "小红书", "web_id": None, "homepage_url": None}
        assert log.after == {"platform": "抖音", "web_id": "123", "homepage_url": "https://a.com/u"}

    async def test_clear_homepage_url(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        mine = await blogger_factory.blogger()
        mine.homepage_url = "https://a.com/u"
        await session.flush()
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).update_blogger(
            mine.id, BloggerUpdate(homepage_url=""), user
        )
        assert resp.homepage_url is None


@pytest.mark.usefixtures("tenant_ctx")
class TestRestore:
    async def test_restore_collides_same_platform(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        old = await blogger_factory.blogger(
            xiaohongshu_id="K8R1", platform="抖音", is_deleted=True, is_active=False
        )
        old_id = old.id
        await blogger_factory.blogger(xiaohongshu_id="K8R1", platform="抖音")
        user = await _admin(factory, tenant_a, admin_role)
        with pytest.raises(BloggerXhsIdConflictError) as exc:
            await BloggerService(session).restore_blogger(old_id, user)
        assert exc.value.details["platform"] == "抖音"

    async def test_restore_ok_when_only_other_platform(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        old = await blogger_factory.blogger(
            xiaohongshu_id="K8R2", platform="抖音", is_deleted=True, is_active=False
        )
        old_id = old.id
        await blogger_factory.blogger(xiaohongshu_id="K8R2", platform="小红书")
        user = await _admin(factory, tenant_a, admin_role)
        resp = await BloggerService(session).restore_blogger(old_id, user)
        assert resp.is_deleted is False


@pytest.mark.usefixtures("tenant_ctx")
class TestRepository:
    async def test_get_by_account_uses_platform(
        self, session: AsyncSession, blogger_factory: Any
    ) -> None:
        dy = await blogger_factory.blogger(xiaohongshu_id="K8G1", platform="抖音")
        repo = BloggerRepository(session)
        found = await repo.get_by_account("抖音", "K8G1")
        assert found is not None and found.id == dy.id
        assert await repo.get_by_account("小红书", "K8G1") is None

    async def test_list_by_account_order(self, session: AsyncSession, blogger_factory: Any) -> None:
        # 按 Platform 枚举声明序，不在枚举里的排最后；软删的不算
        await blogger_factory.blogger(xiaohongshu_id="K8L1", platform="未知平台")
        await blogger_factory.blogger(xiaohongshu_id="K8L1", platform="B站")
        await blogger_factory.blogger(xiaohongshu_id="K8L1", platform="抖音")
        await blogger_factory.blogger(xiaohongshu_id="K8L1", platform="小红书")
        await blogger_factory.blogger(
            xiaohongshu_id="K8L1", platform="快手", is_deleted=True, is_active=False
        )
        await blogger_factory.blogger(xiaohongshu_id="K8L1X", platform="快手")
        found = await BloggerRepository(session).list_by_account("K8L1")
        assert [b.platform for b in found] == ["小红书", "抖音", "B站", "未知平台"]

    async def test_upsert_atomic_per_platform(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        xhs = await blogger_factory.blogger(xiaohongshu_id="K8P1", platform="小红书")
        repo = BloggerRepository(session)
        dy, inserted = await repo.upsert_atomic(
            tenant_id=tenant_a.id,
            values={"xiaohongshu_id": "K8P1", "nickname": "抖音的", "platform": "抖音"},
        )
        assert inserted is True
        assert dy.id != xhs.id
        again, inserted2 = await repo.upsert_atomic(
            tenant_id=tenant_a.id,
            values={"xiaohongshu_id": "K8P1", "nickname": "抖音改名", "platform": "抖音"},
        )
        assert inserted2 is False
        assert again.id == dy.id

    async def test_keyword_hits_web_id(self, session: AsyncSession, blogger_factory: Any) -> None:
        b = await blogger_factory.blogger(xiaohongshu_id="K8S1", platform="抖音")
        b.web_id = "71234500099"
        await session.flush()
        items, total = await BloggerRepository(session).list(
            filters=BloggerListFilters(keyword="12345000")
        )
        assert total == 1
        assert items[0].id == b.id
