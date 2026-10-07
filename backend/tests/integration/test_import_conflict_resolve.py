"""8a-6：冲突裁决、列表脱敏、按字段筛选、CSV（设计 §4.5、§4.5.1 ~ §4.5.3；AC 44 ~ 46、51、52，N3、N4）。

直接调 ``ImportConflictService``：用户按迁移 seed 的真实角色建（``load_effective_permissions``），
字段权限走 ``build_field_perm_context``（读库）。冲突直接插库（博主冲突 + 一条商品资料冲突）。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Permission, Role, UserPermissionOverride
from app.modules.auth.service import AuthService
from app.modules.blogger.models import Blogger
from app.modules.importer.conflicts import ConflictFilters, ImportConflictService
from app.modules.importer.exceptions import (
    ImportConflictExpectedRequiredError,
    ImportConflictFieldPermissionError,
    ImportConflictFieldUnknownError,
    ImportConflictNotFoundError,
)
from app.modules.importer.models import ImportConflict
from app.modules.importer.schemas import ConflictResolveRequest
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.models import Sku, Style

BLOGGER = "manual_blogger"
STYLE_SKU = "manual_style_sku"


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.fixture
def registered() -> Iterator[None]:
    """按字段筛选要用 adapter.compare_field_names()：注册真实 adapter，用完放回。"""
    from app.main import register_import_adapters
    from app.modules.importer.registry import ImportAdapterRegistry

    saved = dict(ImportAdapterRegistry._adapters)
    register_import_adapters()
    try:
        yield
    finally:
        ImportAdapterRegistry._adapters.clear()
        ImportAdapterRegistry._adapters.update(saved)


class _World:
    def __init__(
        self, session: AsyncSession, factory: Any, tenant: Any, batches: Any, bloggers: Any
    ):
        self.session = session
        self.factory = factory
        self.tenant = tenant
        self.batches = batches
        self.bloggers = bloggers
        self.svc = ImportConflictService(session)

    async def user(self, role_code: str, *, revoke: tuple[str, ...] = ()) -> tuple[Any, Any]:
        role = (await self.session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await self.factory.user(self.tenant, roles=[role])
        for scope in revoke:
            perm = (
                await self.session.execute(select(Permission).where(Permission.scope == scope))
            ).scalar_one()
            self.session.add(
                UserPermissionOverride(
                    tenant_id=self.tenant.id,
                    user_id=user.id,
                    permission_id=perm.id,
                    effect="revoke",
                )
            )
        await self.session.flush()
        perms = await AuthService(self.session).load_effective_permissions(user.id)
        return user, perms

    async def blogger(self, **kw: Any) -> Blogger:
        kw.setdefault("follower_count", 1000)
        return await self.bloggers.blogger(**kw)

    async def conflict(
        self,
        obj_id: UUID,
        fields: list[dict[str, Any]],
        *,
        source: str = BLOGGER,
        object_type: str = "blogger",
        kind: str = "fields",
        status: str = "pending",
        batch_id: UUID | None = None,
        label: str = "小美",
        message: str | None = None,
    ) -> ImportConflict:
        if batch_id is None:
            batch_id = (
                await self.batches.batch(source=source, original_filename="博主导入.csv")
            ).id
        c = ImportConflict(
            tenant_id=self.tenant.id,
            source=source,
            batch_id=batch_id,
            row_numbers=[2],
            object_type=object_type,
            object_id=obj_id,
            object_key="xhs-key",
            object_label=label,
            kind=kind,
            fields=fields,
            message=message,
            status=status,
            resolved_at=None if status == "pending" else datetime.now(UTC),
        )
        self.session.add(c)
        await self.session.flush()
        return c

    async def reload(self, model: Any, obj_id: UUID) -> Any:
        stmt = select(model).where(model.id == obj_id).execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one()

    async def audits(self, action: str, resource_id: UUID) -> list[AuditLog]:
        stmt = select(AuditLog).where(
            AuditLog.action == action, AuditLog.resource_id == str(resource_id)
        )
        return list((await self.session.execute(stmt)).scalars().all())


@pytest.fixture
def world(
    session: AsyncSession,
    factory: Any,
    tenant_a: Any,
    import_batch_factory: Any,
    blogger_factory: Any,
    tenant_ctx: None,
) -> _World:
    return _World(session, factory, tenant_a, import_batch_factory, blogger_factory)


def _f(
    field: str, system: Any, file: Any, *, sensitive: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "field": field,
        "label": {
            "follower_count": "粉丝数",
            "quote": "报价",
            "wechat": "微信",
            "remark": "备注",
        }.get(field, field),
        "system": system,
        "file": file,
        "system_display": None if system is None else str(system),
        "file_display": None if file is None else str(file),
        "sensitive": sensitive,
        **extra,
    }


def _req(
    decision: str, *items: tuple[UUID, dict[str, Any] | None], note: str | None = None
) -> ConflictResolveRequest:
    return ConflictResolveRequest(
        decision=decision,
        items=[{"id": i, "expected_system_values": e} for i, e in items],
        note=note,
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestResolveSingle:
    async def test_keep(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        resp = await world.svc.resolve(_req("keep", (c.id, None), note=" 以系统为准 "), user, perms)
        assert [(r.outcome, r.status) for r in resp.results] == [("resolved", "kept")]
        assert resp.summary.resolved == 1
        c = await world.reload(ImportConflict, c.id)
        assert (c.status, c.resolved_by, c.resolution_note) == ("kept", user.id, "以系统为准")
        assert c.resolved_at is not None
        assert (await world.reload(Blogger, b.id)).follower_count == 1000
        [audit] = await world.audits("import_conflict.resolve", c.id)
        assert audit.user_id == user.id
        assert audit.before == {"status": "pending"}
        assert audit.after["decision"] == "keep"
        assert audit.after["via"] == "single"
        assert audit.after["object_id"] == str(b.id)

    async def test_overwrite(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger(blogger_type="腰部")
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"follower_count": 1000})), user, perms
        )
        assert [(r.outcome, r.status) for r in resp.results] == [("resolved", "overwritten")]
        blogger = await world.reload(Blogger, b.id)
        assert blogger.follower_count == 2000
        assert blogger.blogger_type == "腰部"  # 不重算
        c = await world.reload(ImportConflict, c.id)
        assert (c.status, c.resolved_by) == ("overwritten", user.id)
        [obj_audit] = await world.audits("blogger.update", b.id)
        assert obj_audit.user_id == user.id
        assert obj_audit.before == {"follower_count": 1000}
        assert obj_audit.after["follower_count"] == 2000
        assert obj_audit.after["via"] == "import_conflict"
        assert obj_audit.after["import_conflict_id"] == str(c.id)
        assert obj_audit.after["import_batch_id"] == str(c.batch_id)
        assert len(await world.audits("import_conflict.resolve", c.id)) == 1

    async def test_overwrite_sensitive_audit(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger(quote=Decimal("60.00"))
        c = await world.conflict(
            b.id, [_f("quote", "60.00", "65.00", sensitive=["blogger", "quote"])]
        )
        resp = await world.svc.resolve(_req("overwrite", (c.id, {"quote": "60"})), user, perms)
        assert resp.results[0].outcome == "resolved"
        assert (await world.reload(Blogger, b.id)).quote == Decimal("65.00")
        [audit] = await world.audits("blogger.update", b.id)
        assert audit.before == {}
        assert audit.after["quote_changed"] is True
        assert "quote" not in audit.after

    async def test_stale_returns_current(self, world: _World) -> None:
        """AC 46：冲突生成后系统值被人改过 → stale、返回当前值、不改。"""
        user, perms = await world.user("pr")
        b = await world.blogger(follower_count=1500)
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"follower_count": 1000})), user, perms
        )
        [r] = resp.results
        assert (r.outcome, r.status) == ("stale", "pending")
        assert r.current_values == {"follower_count": 1500}
        assert (await world.reload(Blogger, b.id)).follower_count == 1500
        assert (await world.reload(ImportConflict, c.id)).status == "pending"
        # 带新的期望值重发 → 成功
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"follower_count": 1500})), user, perms
        )
        assert resp.results[0].outcome == "resolved"
        assert (await world.reload(Blogger, b.id)).follower_count == 2000

    async def test_gone(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger(is_deleted=True)
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"follower_count": 1000})), user, perms
        )
        assert [(r.outcome, r.status) for r in resp.results] == [("gone", "invalid")]
        c = await world.reload(ImportConflict, c.id)
        assert (c.status, c.resolved_by, c.resolution_note) == ("invalid", None, "对象已删除")
        assert c.resolved_at is not None

    async def test_not_pending(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)], status="kept")
        resp = await world.svc.resolve(_req("keep", (c.id, None)), user, perms)
        assert [(r.outcome, r.status) for r in resp.results] == [("not_pending", "kept")]

    async def test_invalid_value(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2**31)])
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"follower_count": 1000})), user, perms
        )
        [r] = resp.results
        assert (r.outcome, r.field) == ("invalid_value", "follower_count")
        assert r.message == "粉丝数 的值不合法（必须为 0 到 2147483647 之间的整数）"
        assert str(2**31) not in (r.message or "")
        assert (await world.reload(Blogger, b.id)).follower_count == 1000
        assert (await world.reload(ImportConflict, c.id)).status == "pending"

    async def test_key_conflict(self, world: _World) -> None:
        """键冲突不可覆盖；「保留系统值」一并关闭并入的字段并在备注里列出。"""
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(
            b.id,
            [_f("remark", "a", "b", from_batch_id=str(uuid4()))],
            kind="key",
            message="键冲突原因",
        )
        resp = await world.svc.resolve(_req("overwrite", (c.id, {})), user, perms)
        assert resp.results[0].outcome == "not_overwritable"
        assert (await world.reload(ImportConflict, c.id)).status == "pending"
        resp = await world.svc.resolve(_req("keep", (c.id, None)), user, perms)
        assert resp.results[0].outcome == "resolved"
        c = await world.reload(ImportConflict, c.id)
        assert c.status == "kept"
        assert "remark" in (c.resolution_note or "")


@pytest.mark.integration
@pytest.mark.asyncio
class TestResolveBatch:
    async def test_mixed_n3(self, world: _World) -> None:
        """N3：多选一次 4 条（含一条 stale、一条已处理）→ 其余成功，每条各自留痕。"""
        user, perms = await world.user("pr_manager")
        b1 = await world.blogger()
        b2 = await world.blogger()
        b3 = await world.blogger(follower_count=1500)
        b4 = await world.blogger()
        c1 = await world.conflict(b1.id, [_f("follower_count", 1000, 2000)])
        c2 = await world.conflict(b2.id, [_f("follower_count", 1000, 3000)])
        c3 = await world.conflict(b3.id, [_f("follower_count", 1000, 4000)])
        c4 = await world.conflict(b4.id, [_f("follower_count", 1000, 5000)], status="kept")
        expected = {"follower_count": 1000}
        resp = await world.svc.resolve(
            _req(
                "overwrite",
                (c1.id, expected),
                (c2.id, expected),
                (c3.id, expected),
                (c4.id, expected),
                note="批量覆盖",
            ),
            user,
            perms,
        )
        by_id = {r.id: r for r in resp.results}
        assert by_id[c1.id].outcome == "resolved"
        assert by_id[c2.id].outcome == "resolved"
        assert by_id[c3.id].outcome == "stale"
        assert by_id[c3.id].current_values == {"follower_count": 1500}
        assert by_id[c4.id].outcome == "not_pending"
        assert (resp.summary.resolved, resp.summary.stale, resp.summary.not_pending) == (2, 1, 1)
        assert [r.id for r in resp.results] == sorted([c1.id, c2.id, c3.id, c4.id])
        for cid, bid, value in ((c1.id, b1.id, 2000), (c2.id, b2.id, 3000)):
            c = await world.reload(ImportConflict, cid)
            assert (c.status, c.resolved_by, c.resolution_note) == (
                "overwritten",
                user.id,
                "批量覆盖",
            )
            assert c.resolved_at is not None
            [audit] = await world.audits("import_conflict.resolve", cid)
            assert audit.after["via"] == "batch"
            assert (await world.reload(Blogger, bid)).follower_count == value
        assert (await world.reload(Blogger, b3.id)).follower_count == 1500
        assert await world.audits("import_conflict.resolve", c3.id) == []

    async def test_expected_required(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        with pytest.raises(ImportConflictExpectedRequiredError):
            await world.svc.resolve(_req("overwrite", (c.id, None)), user, perms)

    async def test_missing_ids_404(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        ghost = uuid4()
        with pytest.raises(ImportConflictNotFoundError) as exc_info:
            await world.svc.resolve(_req("keep", (c.id, None), (ghost, None)), user, perms)
        assert exc_info.value.details["missing_ids"] == [str(ghost)]
        assert (await world.reload(ImportConflict, c.id)).status == "pending"

    async def test_invisible_source_is_404(self, world: _World) -> None:
        """跟单看不到博主来源：按不存在处理（不暴露存在性）。"""
        user, perms = await world.user("merchandiser")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        with pytest.raises(ImportConflictNotFoundError):
            await world.svc.resolve(_req("keep", (c.id, None)), user, perms)


class TestResolveRequestSchema:
    def test_request_validation(self) -> None:
        cid = uuid4()
        with pytest.raises(ValidationError):
            ConflictResolveRequest(decision="keep", items=[{"id": cid}, {"id": cid}])
        with pytest.raises(ValidationError):
            ConflictResolveRequest(decision="keep", items=[])
        with pytest.raises(ValidationError):
            ConflictResolveRequest(decision="keep", items=[{"id": cid, "x": 1}])
        with pytest.raises(ValidationError):
            ConflictResolveRequest(decision="drop", items=[{"id": cid}])
        assert ConflictResolveRequest(decision="keep", items=[{"id": cid}], note="  ").note is None


@pytest.mark.integration
@pytest.mark.asyncio
class TestResolvePermissions:
    async def test_operations_cannot_resolve_blogger(self, world: _World) -> None:
        user, perms = await world.user("operations")
        b = await world.blogger()
        c = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        with pytest.raises(PermissionDeniedError):
            await world.svc.resolve(_req("keep", (c.id, None)), user, perms)
        assert (await world.reload(ImportConflict, c.id)).status == "pending"

    async def test_pr_cannot_resolve_style_sku(self, world: _World) -> None:
        user, perms = await world.user("pr")
        b = await world.blogger()
        ok = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        sku = await world.conflict(
            uuid4(),
            [_f("cost_price", "60.00", "65.00", sensitive=["sku", "cost_price"])],
            source=STYLE_SKU,
            object_type="sku",
        )
        with pytest.raises(PermissionDeniedError) as exc_info:
            await world.svc.resolve(
                _req(
                    "overwrite",
                    (ok.id, {"follower_count": 1000}),
                    (sku.id, {"cost_price": "60.00"}),
                ),
                user,
                perms,
            )
        assert exc_info.value.details["required_scope"] == "product.import"
        # 整单零改动
        assert (await world.reload(ImportConflict, ok.id)).status == "pending"
        assert (await world.reload(Blogger, b.id)).follower_count == 1000

    async def test_revoked_field_write_403(self, world: _World) -> None:
        user, perms = await world.user("pr", revoke=("field.blogger.wechat:write",))
        b = await world.blogger(wechat="wx-old")
        ok = await world.conflict(b.id, [_f("follower_count", 1000, 2000)])
        b2 = await world.blogger(wechat="wx-old")
        wx = await world.conflict(
            b2.id, [_f("wechat", "wx-old", "wx-new", sensitive=["blogger", "wechat"])]
        )
        with pytest.raises(ImportConflictFieldPermissionError) as exc_info:
            await world.svc.resolve(
                _req("overwrite", (ok.id, {"follower_count": 1000}), (wx.id, {"wechat": "wx-old"})),
                user,
                perms,
            )
        assert exc_info.value.code == "FIELD_PERMISSION_DENIED"
        assert exc_info.value.details["denied"] == [{"id": str(wx.id), "field": "wechat"}]
        assert (await world.reload(ImportConflict, ok.id)).status == "pending"
        assert (await world.reload(Blogger, b2.id)).wechat == "wx-old"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("registered")
class TestListAndCsv:
    async def _seed(self, world: _World) -> tuple[ImportConflict, ImportConflict]:
        b = await world.blogger()
        sensitive = await world.conflict(
            b.id,
            [
                _f("quote", "60.00", "65.00", sensitive=["blogger", "quote"]),
                _f("wechat", "wx1", "wx2", sensitive=["blogger", "wechat"]),
                _f("follower_count", 1000, 2000),
            ],
        )
        b2 = await world.blogger()
        plain = await world.conflict(b2.id, [_f("remark", "a", "b")])
        return sensitive, plain

    async def test_masked_for_operations(self, world: _World) -> None:
        """AC 52：运营查看博主冲突 → 报价 / 微信脱敏，不能裁决。"""
        sensitive, _ = await self._seed(world)
        user, perms = await world.user("operations")
        items, _total = await world.svc.list_conflicts(
            ConflictFilters(source=BLOGGER), page=1, page_size=100, user=user, perms=perms
        )
        item = next(i for i in items if i.id == sensitive.id)
        by_field = {f.field: f for f in item.fields}
        for name in ("quote", "wechat"):
            f = by_field[name]
            assert (f.masked, f.sensitive) == (True, True)
            assert (f.system, f.file, f.system_display, f.file_display) == (None, None, None, None)
        assert by_field["follower_count"].masked is False
        assert by_field["follower_count"].file == 2000
        assert item.can_resolve is False
        assert item.overwritable is True
        assert item.batch_filename == "博主导入.csv"

    async def test_visible_for_pr(self, world: _World) -> None:
        sensitive, _ = await self._seed(world)
        user, perms = await world.user("pr")
        items, _total = await world.svc.list_conflicts(
            ConflictFilters(source=BLOGGER), page=1, page_size=100, user=user, perms=perms
        )
        item = next(i for i in items if i.id == sensitive.id)
        quote = next(f for f in item.fields if f.field == "quote")
        assert (quote.masked, quote.system, quote.file) == (False, "60.00", "65.00")
        assert item.can_resolve is True

    async def test_field_filter_n4(self, world: _World) -> None:
        sensitive, plain = await self._seed(world)
        user, perms = await world.user("pr")
        items, total = await world.svc.list_conflicts(
            ConflictFilters(source=BLOGGER, field="quote"),
            page=1,
            page_size=100,
            user=user,
            perms=perms,
        )
        ids = {i.id for i in items}
        assert sensitive.id in ids
        assert plain.id not in ids
        assert all(any(f.field == "quote" for f in i.fields) for i in items)
        assert total == len(items)
        with pytest.raises(ImportConflictFieldUnknownError):
            await world.svc.list_conflicts(
                ConflictFilters(source=BLOGGER, field="cost_price"),
                page=1,
                page_size=20,
                user=user,
                perms=perms,
            )
        with pytest.raises(ImportConflictFieldUnknownError):
            await world.svc.download_csv(ConflictFilters(source=BLOGGER, field="nope"), user, perms)

    async def test_status_filter_and_summary(self, world: _World) -> None:
        sensitive, plain = await self._seed(world)
        b = await world.blogger()
        kept = await world.conflict(b.id, [_f("remark", "a", "c")], status="kept")
        user, perms = await world.user("pr")
        pending_ids = {
            i.id
            for i in (
                await world.svc.list_conflicts(
                    ConflictFilters(source=BLOGGER), page=1, page_size=100, user=user, perms=perms
                )
            )[0]
        }
        assert {sensitive.id, plain.id} <= pending_ids
        assert kept.id not in pending_ids
        all_ids = {
            i.id
            for i in (
                await world.svc.list_conflicts(
                    ConflictFilters(source=BLOGGER, status="all"),
                    page=1,
                    page_size=100,
                    user=user,
                    perms=perms,
                )
            )[0]
        }
        assert kept.id in all_ids
        n = await world.svc.summary(BLOGGER, user, perms)
        assert n >= 2
        before = n
        await world.svc.resolve(_req("keep", (plain.id, None)), user, perms)
        assert await world.svc.summary(BLOGGER, user, perms) == before - 1

    async def test_csv(self, world: _World) -> None:
        """AC 51：BOM、= + - @ 开头被转义、「来源批次」列；运营下载时受保护字段写「有差异」。"""
        b = await world.blogger()
        old_batch = uuid4()
        c = await world.conflict(
            b.id,
            [
                _f("remark", "=SUM(A1)", "+1", **{"from_batch_id": str(old_batch)}),
                _f("nickname", "-x", "@y"),
                _f("quote", "60.00", "65.00", sensitive=["blogger", "quote"]),
            ],
            label="=HYPERLINK(1)",
        )
        user, perms = await world.user("operations")
        data = await world.svc.download_csv(ConflictFilters(source=BLOGGER), user, perms)
        assert data.startswith("\ufeff".encode())
        rows = list(csv.reader(io.StringIO(data.decode("utf-8").lstrip("\ufeff"))))
        header = rows[0]
        assert header == [
            "冲突ID",
            "来源",
            "批次文件",
            "行号",
            "对象类型",
            "对象",
            "对象编码",
            "类型",
            "字段",
            "系统值",
            "文件值",
            "来源批次",
            "状态",
            "处理人",
            "处理时间",
            "说明",
        ]
        mine = [r for r in rows[1:] if r[0] == str(c.id)]
        assert len(mine) == 3
        by_field = {r[8]: r for r in mine}
        remark = by_field["备注"]
        assert (remark[9], remark[10], remark[11]) == ("'=SUM(A1)", "'+1", str(old_batch))
        assert remark[5] == "'=HYPERLINK(1)"
        assert (remark[1], remark[2], remark[3], remark[4], remark[12]) == (
            "博主",
            "博主导入.csv",
            "2",
            "博主",
            "待处理",
        )
        nick = by_field["nickname"]
        assert (nick[9], nick[10]) == ("'-x", "'@y")
        quote = by_field["报价"]
        assert (quote[9], quote[10]) == ("有差异", "有差异")
        assert "65.00" not in data.decode("utf-8")


# ---------------------------------------------------------------------------
# 商品资料（款式 / SKU / 商品，8a-4）：裁决写入与留痕（AC 44、45，N8、N9）
# ---------------------------------------------------------------------------


def _pf(
    field: str,
    label: str,
    system: Any,
    file: Any,
    *,
    sensitive: list[str] | None = None,
    displays: tuple[str | None, str | None] | None = None,
) -> dict[str, Any]:
    sys_d, file_d = displays or (system, file)
    return {
        "field": field,
        "label": label,
        "system": system,
        "file": file,
        "system_display": sys_d,
        "file_display": file_d,
        "sensitive": sensitive,
    }


async def _goods(world: _World, style: Any, **kw: Any) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=world.tenant.id,
        goods_code=kw.pop("goods_code", style.style_code),
        goods_title=kw.pop("goods_title", "单品全称"),
        is_suit=False,
        **kw,
    )
    world.session.add(goods)
    await world.session.flush()
    world.session.add(
        GoodsStyleItem(tenant_id=world.tenant.id, goods_main_id=goods.id, style_id=style.id)
    )
    await world.session.flush()
    return goods


@pytest.mark.integration
@pytest.mark.asyncio
class TestResolveStyleSku:
    async def test_overwrite_style_external_url_audit(
        self, world: _World, product_factory: Any
    ) -> None:
        """N9：覆盖款式外部链接 → style.update 审计有 external_image_url 前后值（AC 44）。"""
        user, perms = await world.user("merchandiser")
        old, new = "https://img.example.invalid/0.jpg", "https://img.example.invalid/1.jpg"
        style = await product_factory.style()
        style.external_image_url = old
        await world.session.flush()
        c = await world.conflict(
            style.id,
            [_pf("external_image_url", "图片", old, new)],
            source=STYLE_SKU,
            object_type="style",
        )
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"external_image_url": old})), user, perms
        )
        assert [(r.outcome, r.status) for r in resp.results] == [("resolved", "overwritten")]
        assert (await world.reload(Style, style.id)).external_image_url == new
        c = await world.reload(ImportConflict, c.id)
        assert (c.status, c.resolved_by) == ("overwritten", user.id)
        [audit] = await world.audits("style.update", style.id)
        assert audit.before == {"external_image_url": old}
        assert audit.after["external_image_url"] == new
        assert (audit.after["via"], audit.after["import_conflict_id"]) == (
            "import_conflict",
            str(c.id),
        )

    async def test_overwrite_goods_season_brand_audit(
        self, world: _World, product_factory: Any
    ) -> None:
        """N9：覆盖商品季节 / 品牌 → goods.update 审计有 season / brand_id 与 brand_name。"""
        user, perms = await world.user("operations")
        b1 = await product_factory.brand(brand_name="旧品牌")
        b2 = await product_factory.brand(brand_name="新品牌")
        style = await product_factory.style()
        goods = await _goods(world, style, season="春", brand_id=b1.id)
        c = await world.conflict(
            goods.id,
            [
                _pf("season", "季节", "春", "夏"),
                _pf("brand_id", "品牌", str(b1.id), str(b2.id), displays=("旧品牌", "新品牌")),
            ],
            source=STYLE_SKU,
            object_type="goods",
        )
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"season": "春", "brand_id": str(b1.id)})), user, perms
        )
        assert [r.outcome for r in resp.results] == ["resolved"]
        g = await world.reload(GoodsMain, goods.id)
        assert (g.season, g.brand_id) == ("夏", b2.id)
        [audit] = await world.audits("goods.update", goods.id)
        assert audit.before == {"season": "春", "brand_id": str(b1.id), "brand_name": "旧品牌"}
        assert (audit.after["season"], audit.after["brand_id"], audit.after["brand_name"]) == (
            "夏",
            str(b2.id),
            "新品牌",
        )

    async def test_overwrite_cost_price_purchase_sourcing(
        self, world: _World, product_factory: Any
    ) -> None:
        """N8 / N9：库里货源「采购」的 SKU 覆盖成本价 → resolved；sku.update 只记 cost_price_changed。"""
        user, perms = await world.user("merchandiser")
        style = await product_factory.style()
        sku = await product_factory.sku(
            style, sourcing_type="采购", cost_price=Decimal("60.00"), base_price=Decimal("199.00")
        )
        c = await world.conflict(
            sku.id,
            [
                _pf("cost_price", "成本价", "60.00", "65.00", sensitive=["sku", "cost_price"]),
                _pf("base_price", "基本售价", "199.00", "209.00"),
            ],
            source=STYLE_SKU,
            object_type="sku",
        )
        resp = await world.svc.resolve(
            _req("overwrite", (c.id, {"cost_price": "60.00", "base_price": "199"})), user, perms
        )
        assert [r.outcome for r in resp.results] == ["resolved"]
        s = await world.reload(Sku, sku.id)
        assert (s.cost_price, s.base_price, s.sourcing_type) == (
            Decimal("65.00"),
            Decimal("209.00"),
            "采购",
        )
        [audit] = await world.audits("sku.update", sku.id)
        assert audit.after["cost_price_changed"] is True
        assert "cost_price" not in audit.before and "cost_price" not in audit.after
        assert (audit.before["base_price"], audit.after["base_price"]) == ("199.00", "209.00")

    async def test_invalid_values(self, world: _World, product_factory: Any) -> None:
        """价格 ≥ 1 亿 → invalid_value（原因不含值）；品牌已停用 → invalid_value；都不改。"""
        user, perms = await world.user("merchandiser")
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        money = await world.conflict(
            sku.id,
            [_pf("base_price", "基本售价", "200.00", "100000000.00")],
            source=STYLE_SKU,
            object_type="sku",
        )
        off = await product_factory.brand(brand_name="停用品牌", is_active=False)
        goods = await _goods(world, style)
        brand = await world.conflict(
            goods.id,
            [_pf("brand_id", "品牌", None, str(off.id))],
            source=STYLE_SKU,
            object_type="goods",
        )
        resp = await world.svc.resolve(
            _req("overwrite", (money.id, {"base_price": "200.00"}), (brand.id, {"brand_id": None})),
            user,
            perms,
        )
        by_id = {r.id: r for r in resp.results}
        assert by_id[money.id].outcome == "invalid_value"
        assert by_id[money.id].field == "base_price"
        assert "100000000" not in (by_id[money.id].message or "")
        assert (by_id[brand.id].outcome, by_id[brand.id].field) == ("invalid_value", "brand_id")
        assert "品牌不存在或已停用" in (by_id[brand.id].message or "")
        assert (await world.reload(Sku, sku.id)).base_price == Decimal("200.00")
        assert (await world.reload(GoodsMain, goods.id)).brand_id is None

    async def test_key_conflict_keep_closes_carried(
        self, world: _World, product_factory: Any
    ) -> None:
        """键冲突 → 覆盖 not_overwritable；带并入字段时「保留系统值」一并关闭并列出字段名。"""
        user, perms = await world.user("merchandiser")
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        c = await world.conflict(
            sku.id,
            [_pf("cost_price", "成本价", "100.00", "65.00", sensitive=["sku", "cost_price"])],
            source=STYLE_SKU,
            object_type="sku",
            kind="key",
            message="SKU 编码已属于款式 X",
        )
        resp = await world.svc.resolve(_req("overwrite", (c.id, {})), user, perms)
        assert [r.outcome for r in resp.results] == ["not_overwritable"]
        resp = await world.svc.resolve(_req("keep", (c.id, None)), user, perms)
        assert [r.outcome for r in resp.results] == ["resolved"]
        c = await world.reload(ImportConflict, c.id)
        assert c.status == "kept"
        assert "cost_price" in (c.resolution_note or "")

    async def test_pr_cannot_resolve_goods(self, world: _World, product_factory: Any) -> None:
        user, perms = await world.user("pr")
        style = await product_factory.style()
        goods = await _goods(world, style, season="春")
        c = await world.conflict(
            goods.id, [_pf("season", "季节", "春", "夏")], source=STYLE_SKU, object_type="goods"
        )
        with pytest.raises(PermissionDeniedError):
            await world.svc.resolve(_req("overwrite", (c.id, {"season": "春"})), user, perms)
        assert (await world.reload(GoodsMain, goods.id)).season == "春"

    async def test_ac45_revoked_cost_write_403(self, world: _World, product_factory: Any) -> None:
        """AC 45：撤销了 field.sku.cost_price:write 的跟单覆盖成本价 → 403、零改动。"""
        user, perms = await world.user("merchandiser", revoke=("field.sku.cost_price:write",))
        style = await product_factory.style()
        sku = await product_factory.sku(style, cost_price=Decimal("60.00"))
        c = await world.conflict(
            sku.id,
            [_pf("cost_price", "成本价", "60.00", "65.00", sensitive=["sku", "cost_price"])],
            source=STYLE_SKU,
            object_type="sku",
        )
        with pytest.raises(ImportConflictFieldPermissionError):
            await world.svc.resolve(_req("overwrite", (c.id, {"cost_price": "60.00"})), user, perms)
        assert (await world.reload(Sku, sku.id)).cost_price == Decimal("60.00")
        assert (await world.reload(ImportConflict, c.id)).status == "pending"
