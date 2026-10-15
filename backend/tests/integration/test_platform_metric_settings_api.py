"""``GET`` / ``PUT /api/settings/promotion-platforms``（7e R7，报表视图设计 5.1 / 3.3）。

- GET：没写过行回默认值；``can_edit`` 按 ``metric_config:write``
- PUT：只写变了的行与 ``updated_by``；同事务清覆盖、写审计；提交后投递只重算质量标签的任务
  （投递失败仍 200）；值没变不审计 / 不清 / 不投递；422 带行号；权限
- 并发：刷新锁在另一条连接上（测试 session 单连接里 advisory lock 可重入）。持锁 0.5 s 后放 →
  PUT 等到锁再成功；一直持锁 → 409，配置不落库、覆盖还在、无审计、未投递

service 会 ``commit``（测试 session 里只释放保存点）/ 撞锁 ``rollback``（只回滚到上一个保存点），
所以发请求前先 ``commit`` 落种子。外层事务由 fixture 统一回滚，不用另外清理。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import get_session
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.metric_config import service as metric_service
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.repository import PromotionPlatformSettingRepository
from app.modules.metric_config.service import PlatformMetricSettingService
from app.modules.report import summary_invalidation

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

URL = "/api/settings/promotion-platforms"

DEFAULT_ITEMS = [
    {"platform": "小红书", "like_coefficient": "1.000", "hit_threshold": 1000},
    {"platform": "抖音", "like_coefficient": "0.100", "hit_threshold": 10000},
    {"platform": "快手", "like_coefficient": "0.100", "hit_threshold": 1000},
    {"platform": "B站", "like_coefficient": "1.000", "hit_threshold": 1000},
    {"platform": "得物", "like_coefficient": "1.000", "hit_threshold": 1000},
]


def _body(**changes: dict[str, Any]) -> list[dict[str, Any]]:
    """默认 5 行，按平台改几项：``_body(抖音={"hit_threshold": 5000})``。"""
    return [{**row, **changes.get(str(row["platform"]), {})} for row in DEFAULT_ITEMS]


def _app() -> Any:
    from app.main import app

    return app


class Calls:
    def __init__(self) -> None:
        self.delay: list[tuple[Any, ...]] = []
        self.sentry: list[BaseException] = []
        self.delay_error: Exception | None = None


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """拦下投递与 Sentry；``calls.delay_error`` 设了就让投递抛错。"""
    import app.tasks.blogger_tasks as tasks

    c = Calls()

    def _delay(*args: Any) -> None:
        if c.delay_error is not None:
            raise c.delay_error
        c.delay.append(args)

    def _forbidden(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("不该投递全量重算 recompute_all_blogger_tags")

    monkeypatch.setattr(tasks.recompute_blogger_quality_tags, "delay", _delay)
    monkeypatch.setattr(tasks.recompute_all_blogger_tags, "delay", _forbidden)
    monkeypatch.setattr(metric_service.sentry_sdk, "capture_exception", c.sentry.append)
    return c


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.fixture
def as_role(
    session: AsyncSession,
    factory: Any,
    tenant_a: Any,
    tenant_ctx: None,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str], Any]]:
    """``await as_role("pr_manager")`` → 之后的请求以该角色的新用户身份发出（种子已 commit）。"""
    from app.main import limiter

    app = _app()
    # 本文件一次约 50 个请求，全局 100/分钟 的限流（按客户端 IP，测试里都是同一个）会和
    # 相邻文件叠起来误报 429；限流不是这里要测的
    monkeypatch.setattr(limiter, "enabled", False)

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override

    async def apply(role_code: str) -> Any:
        role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await factory.user(tenant_a, roles=[role])
        perms = await AuthService(session).load_effective_permissions(user.id)
        await session.commit()
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        return user

    yield apply
    for dep in (get_session, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dep, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


async def _seed_coverage(session: AsyncSession, tid: Any) -> None:
    await session.execute(
        text(
            "INSERT INTO report_summary_coverage "
            "(id, tenant_id, stat_date, refreshed_at, created_at, updated_at) "
            "SELECT gen_random_uuid(), CAST(:t AS uuid), d::date, NOW(), NOW(), NOW() "
            "FROM generate_series(DATE '2026-03-01', DATE '2026-03-03', interval '1 day') AS d"
        ),
        {"t": tid},
    )
    await session.commit()


async def _coverage(session: AsyncSession, tid: Any) -> int:
    return int(
        (
            await session.execute(
                text("SELECT count(*) FROM report_summary_coverage WHERE tenant_id = :t"),
                {"t": tid},
            )
        ).scalar_one()
    )


async def _rows(session: AsyncSession, tid: Any) -> list[tuple[str, Decimal, int, Any]]:
    result = await session.execute(
        select(
            PromotionPlatformSetting.platform,
            PromotionPlatformSetting.like_coefficient,
            PromotionPlatformSetting.hit_threshold,
            PromotionPlatformSetting.updated_by,
        ).where(PromotionPlatformSetting.tenant_id == tid)
    )
    # Python 里排序：不依赖库的 collation
    return sorted((r[0], r[1], r[2], r[3]) for r in result.all())


async def _audits(session: AsyncSession, tid: Any) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.tenant_id == tid, AuditLog.action == "metric_config.update")
    )
    return list(result.scalars().all())


# --------------------------------------------------------------------------- #
# GET
# --------------------------------------------------------------------------- #
class TestGet:
    @pytest.mark.parametrize(
        ("role_code", "can_edit"),
        [("pr_manager", True), ("admin", True), ("operations", False)],
    )
    async def test_no_rows_returns_defaults(
        self,
        session: AsyncSession,
        tenant_a: Any,
        as_role: Any,
        role_code: str,
        can_edit: bool,
    ) -> None:
        await as_role(role_code)
        assert await _rows(session, tenant_a.id) == []
        async with _client() as ac:
            resp = await ac.get(URL)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "items": DEFAULT_ITEMS,
            "can_edit": can_edit,
            "changed": [],
            "quality_recompute_queued": False,
        }

    async def test_reads_saved_rows(
        self, session: AsyncSession, tenant_a: Any, as_role: Any
    ) -> None:
        await as_role("operations")
        await PromotionPlatformSettingRepository(session).upsert(
            tenant_a.id, [("抖音", Decimal("0.5"), 500)], updated_by=None
        )
        await session.commit()
        async with _client() as ac:
            resp = await ac.get(URL)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == _body(
            抖音={"like_coefficient": "0.500", "hit_threshold": 500}
        )


# --------------------------------------------------------------------------- #
# PUT
# --------------------------------------------------------------------------- #
class TestPut:
    async def test_one_change_writes_row_clears_audits_and_enqueues(
        self,
        session: AsyncSession,
        tenant_a: Any,
        as_role: Any,
        calls: Calls,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await as_role("pr_manager")
        await _seed_coverage(session, tenant_a.id)
        original = str((await session.execute(text("SHOW lock_timeout"))).scalar_one())

        # spy：upsert 时 lock_timeout 已经还原（helper 拿到锁就还原）
        seen_timeout: list[str] = []
        real_upsert = PromotionPlatformSettingRepository.upsert

        async def _spy(self: PromotionPlatformSettingRepository, *a: Any, **kw: Any) -> None:
            seen_timeout.append(
                str((await self._session.execute(text("SHOW lock_timeout"))).scalar_one())
            )
            await real_upsert(self, *a, **kw)

        monkeypatch.setattr(PromotionPlatformSettingRepository, "upsert", _spy)

        async with _client() as ac:
            resp = await ac.put(URL, json=_body(抖音={"hit_threshold": 5000}))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "items": _body(抖音={"hit_threshold": 5000}),
            "can_edit": True,
            "changed": ["抖音"],
            "quality_recompute_queued": True,
        }
        assert seen_timeout == [original]
        # 只写了变的那一行，带 updated_by
        assert await _rows(session, tenant_a.id) == [("抖音", Decimal("0.100"), 5000, user.id)]
        assert await _coverage(session, tenant_a.id) == 0
        audits = await _audits(session, tenant_a.id)
        assert len(audits) == 1
        assert audits[0].resource == "promotion_platform_setting"
        assert audits[0].user_id == user.id
        assert audits[0].before == {"抖音": {"like_coefficient": "0.1", "hit_threshold": 10000}}
        assert audits[0].after == {
            "抖音": {"like_coefficient": "0.1", "hit_threshold": 5000},
            "coverage_invalidated": 3,
        }
        assert calls.delay == [(str(tenant_a.id),)]
        assert calls.sentry == []
        # 读取侧立刻按新值
        settings = await PlatformMetricSettingService(session).get_settings(tenant_a.id)
        assert settings.hit_threshold("抖音") == 5000

    async def test_second_save_updates_existing_row(
        self, session: AsyncSession, tenant_a: Any, as_role: Any, calls: Calls
    ) -> None:
        user = await as_role("admin")
        async with _client() as ac:
            first = await ac.put(URL, json=_body(快手={"like_coefficient": "0.25"}))
            second = await ac.put(
                URL,
                json=_body(快手={"like_coefficient": "0.3"}, 小红书={"hit_threshold": 2000}),
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["changed"] == ["小红书", "快手"]
        assert await _rows(session, tenant_a.id) == [
            ("小红书", Decimal("1.000"), 2000, user.id),
            ("快手", Decimal("0.300"), 1000, user.id),
        ]
        audits = await _audits(session, tenant_a.id)
        assert len(audits) == 2
        assert calls.delay == [(str(tenant_a.id),), (str(tenant_a.id),)]

    async def test_unchanged_does_nothing(
        self, session: AsyncSession, tenant_a: Any, as_role: Any, calls: Calls
    ) -> None:
        await as_role("pr_manager")
        await _seed_coverage(session, tenant_a.id)
        # 0.1 与 0.100 是同一个系数；数字写法不同也不算改
        body = _body(抖音={"like_coefficient": "0.1"}, 小红书={"like_coefficient": 1})
        async with _client() as ac:
            resp = await ac.put(URL, json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "items": DEFAULT_ITEMS,
            "can_edit": True,
            "changed": [],
            "quality_recompute_queued": False,
        }
        assert await _rows(session, tenant_a.id) == []
        assert await _coverage(session, tenant_a.id) == 3
        assert await _audits(session, tenant_a.id) == []
        assert calls.delay == []

    async def test_enqueue_failure_still_saves(
        self, session: AsyncSession, tenant_a: Any, as_role: Any, calls: Calls
    ) -> None:
        await as_role("pr_manager")
        calls.delay_error = RuntimeError("broker down")
        async with _client() as ac:
            resp = await ac.put(URL, json=_body(得物={"hit_threshold": 3000}))
        assert resp.status_code == 200, resp.text
        assert resp.json()["changed"] == ["得物"]
        assert resp.json()["quality_recompute_queued"] is False
        assert [r[:3] for r in await _rows(session, tenant_a.id)] == [
            ("得物", Decimal("1.000"), 3000)
        ]
        assert len(await _audits(session, tenant_a.id)) == 1
        assert len(calls.sentry) == 1


# --------------------------------------------------------------------------- #
# 422
# --------------------------------------------------------------------------- #
class TestValidation:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("like_coefficient", "0"),
            ("like_coefficient", "-0.1"),
            ("like_coefficient", "10.001"),
            ("like_coefficient", "0.1234"),
            ("like_coefficient", "NaN"),
            ("like_coefficient", "Infinity"),
            ("like_coefficient", "abc"),
            ("hit_threshold", 0),
            ("hit_threshold", 10_000_001),
            ("hit_threshold", 1000.5),
        ],
    )
    async def test_out_of_range_has_row_index(
        self,
        session: AsyncSession,
        tenant_a: Any,
        as_role: Any,
        calls: Calls,
        field: str,
        value: Any,
    ) -> None:
        await as_role("pr_manager")
        async with _client() as ac:
            resp = await ac.put(URL, json=_body(快手={field: value}))
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "VALIDATION_ERROR"
        locs = [e["loc"] for e in body["details"]["errors"]]
        assert locs == [["body", 2, field]]
        assert await _rows(session, tenant_a.id) == []
        assert calls.delay == []

    async def test_boundaries_accepted(
        self, session: AsyncSession, tenant_a: Any, as_role: Any, calls: Calls
    ) -> None:
        await as_role("pr_manager")
        body = _body(
            小红书={"like_coefficient": "10", "hit_threshold": 10_000_000},
            B站={"like_coefficient": "0.001", "hit_threshold": 1},
        )
        async with _client() as ac:
            resp = await ac.put(URL, json=body)
        assert resp.status_code == 200, resp.text
        assert [r[:3] for r in await _rows(session, tenant_a.id)] == [
            ("B站", Decimal("0.001"), 1),
            ("小红书", Decimal("10.000"), 10_000_000),
        ]

    @pytest.mark.parametrize(
        ("label", "body", "error_type"),
        [
            (
                "unknown_platform",
                [*DEFAULT_ITEMS[:4], {**DEFAULT_ITEMS[4], "platform": "微博"}],
                "enum",
            ),
            ("duplicate", [*DEFAULT_ITEMS, DEFAULT_ITEMS[1]], "platform_duplicated"),
            ("missing", DEFAULT_ITEMS[:4], "platform_missing"),
            ("empty", [], "platform_missing"),
            ("not_a_list", {"platform": "抖音"}, "list_type"),
        ],
    )
    async def test_platform_set_must_be_exactly_five(
        self,
        session: AsyncSession,
        tenant_a: Any,
        as_role: Any,
        calls: Calls,
        label: str,
        body: Any,
        error_type: str,
    ) -> None:
        await as_role("pr_manager")
        async with _client() as ac:
            resp = await ac.put(URL, json=body)
        assert resp.status_code == 422, f"{label}: {resp.text}"
        errors = resp.json()["details"]["errors"]
        assert [e["type"] for e in errors] == [error_type], label
        assert await _rows(session, tenant_a.id) == []


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #
class TestPermissions:
    @pytest.mark.parametrize(
        ("role_code", "scopes", "status", "can_edit"),
        [
            ("pr", {"metric_config:read"}, 200, False),
            ("designer", {"metric_config.*:read"}, 200, False),
            ("designer", {"metric_config.*:*"}, 200, True),
            ("pr", {"*"}, 200, True),
            ("pr_manager", set(), 403, False),
            ("pr", {"promotion.*:*"}, 403, False),
        ],
    )
    async def test_get_capability_uses_effective_permissions(
        self, as_role: Any, role_code: str, scopes: set[str], status: int, can_edit: bool
    ) -> None:
        """前端 GET 探测沿用后端权限语义：个人授权、撤回及本域通配。"""
        user = await as_role(role_code)
        effective = EffectivePermissions(user_id=str(user.id), scopes=frozenset(scopes))
        _app().dependency_overrides[get_current_perms] = lambda: effective
        async with _client() as ac:
            response = await ac.get(URL)
        assert response.status_code == status, response.text
        if status == 200:
            assert response.json()["can_edit"] is can_edit
        else:
            assert response.json()["code"] == "PERMISSION_DENIED"

    @pytest.mark.parametrize(
        ("role_code", "get_status", "put_status"),
        [
            ("pr", 403, 403),
            ("finance", 403, 403),
            ("operations", 200, 403),
            ("pr_manager", 200, 200),
            ("admin", 200, 200),
        ],
    )
    async def test_matrix(
        self,
        session: AsyncSession,
        tenant_a: Any,
        as_role: Any,
        calls: Calls,
        role_code: str,
        get_status: int,
        put_status: int,
    ) -> None:
        await as_role(role_code)
        async with _client() as ac:
            got = await ac.get(URL)
            put = await ac.put(URL, json=_body(抖音={"hit_threshold": 8000}))
        assert got.status_code == get_status, got.text
        assert put.status_code == put_status, put.text
        if put_status == 403:
            assert put.json()["code"] == "PERMISSION_DENIED"
            assert await _rows(session, tenant_a.id) == []
            assert calls.delay == []


# --------------------------------------------------------------------------- #
# 并发：刷新持锁
# --------------------------------------------------------------------------- #
_HOLD_SQL = text("SELECT pg_try_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))")


class TestRefreshLock:
    async def test_waits_for_running_refresh_then_saves(
        self, session: AsyncSession, engine: Any, tenant_a: Any, as_role: Any, calls: Calls
    ) -> None:
        await as_role("pr_manager")
        await _seed_coverage(session, tenant_a.id)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with maker() as holder:
            key = summary_invalidation.summary_lock_key(tenant_a.id)
            assert (await holder.execute(_HOLD_SQL, {"key": key})).scalar_one() is True

            async def _release_later() -> None:
                await asyncio.sleep(0.5)
                await holder.rollback()

            releaser = asyncio.create_task(_release_later())
            started = time.monotonic()
            async with _client() as ac:
                resp = await ac.put(URL, json=_body(抖音={"hit_threshold": 6000}))
            waited = time.monotonic() - started
            await releaser
        assert resp.status_code == 200, resp.text
        assert waited >= 0.4, f"没等刷新锁就保存了（{waited:.3f}s）"
        assert [r[:3] for r in await _rows(session, tenant_a.id)] == [
            ("抖音", Decimal("0.100"), 6000)
        ]
        assert await _coverage(session, tenant_a.id) == 0
        assert calls.delay == [(str(tenant_a.id),)]

    async def test_busy_returns_409_and_saves_nothing(
        self,
        session: AsyncSession,
        engine: Any,
        tenant_a: Any,
        as_role: Any,
        calls: Calls,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(summary_invalidation, "LOCK_TIMEOUT", "300ms")
        await as_role("pr_manager")
        # service 撞锁会 rollback，之后再读 tenant_a.id 会 MissingGreenlet：先取出来
        tid = tenant_a.id
        await _seed_coverage(session, tid)
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with maker() as holder:
            key = summary_invalidation.summary_lock_key(tid)
            assert (await holder.execute(_HOLD_SQL, {"key": key})).scalar_one() is True
            async with _client() as ac:
                resp = await ac.put(URL, json=_body(小红书={"hit_threshold": 4090}))
            await holder.rollback()
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "REPORT_SUMMARY_REFRESH_BUSY"
        assert resp.json()["message"] == "报表正在刷新，请稍后再保存"
        assert await _rows(session, tid) == []
        assert await _coverage(session, tid) == 3
        assert await _audits(session, tid) == []
        assert calls.delay == []
        assert calls.sentry == []
