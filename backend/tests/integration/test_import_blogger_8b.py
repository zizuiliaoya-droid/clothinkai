"""8b manual_blogger 导入（设计 §6.2、§6.5、§6.6、§10；评审 N2 / N7 / N17 / N18）。

走真实 runner（``_run_import_batch``）：CSV / xlsx 中文表头、别名表头、``w`` / ``万``、``--``、千分位、多余列。
造数全是脱敏假数据。末尾一个用例走冲突裁决服务：旧冲突里的「平台」选覆盖 → ``invalid_value``、可改选保留。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.auth.service import AuthService
from app.modules.blogger.models import Blogger
from app.modules.blogger.tag_dict import BloggerTagDictService
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.conflicts import ImportConflictService
from app.modules.importer.models import ImportConflict
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.schemas import ConflictResolveRequest
from app.tasks.import_tasks import _run_import_batch

UNKNOWN_TAGS_NOTICE = "本批有类目标签不在标签字典、未写入，汇总见博主页「标签字典 → 导入缺的标签」"
QUALITY_TAGS_NOTICE = "质量标签是系统标签，由重算自动计算，导入不写入（整批只提示一次）"


@dataclass
class _Env:
    Maker: Any
    suffix: str
    tenant_id: Any
    user_id: UUID
    files: dict[str, bytes] = field(default_factory=dict)
    batch_ids: list[UUID] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)

    def acc(self, tag: str) -> str:
        return f"bx{self.suffix}{tag}"

    def tag(self, name: str) -> str:
        return f"{name}{self.suffix[:6]}"

    async def add_tags(self, *values: str) -> None:
        """建本租户的博主标签字典项（N18：重跑不撞唯一键）。"""
        async with self.Maker() as s:
            for value in values:
                await s.execute(
                    text(
                        "INSERT INTO dict_item (id, tenant_id, dict_type, value, sort_order, "
                        "is_active, created_at, updated_at) VALUES (CAST(:id AS uuid), "
                        "CAST(:tid AS uuid), 'blogger_tag', CAST(:v AS text), 0, true, NOW(), NOW()) "
                        "ON CONFLICT (tenant_id, dict_type, value) DO NOTHING"
                    ),
                    {"id": str(uuid4()), "tid": str(self.tenant_id), "v": value},
                )
                self.tags.add(value)
            await s.commit()

    async def blogger(self, tag: str, **values: Any) -> UUID:
        row: dict[str, Any] = {
            "nickname": "小美",
            "platform": "小红书",
            "follower_count": 1000,
            "quote": Decimal("500.00"),
            "remark": None,
            "category_tags": "[]",
            "quality_tags": "[]",
            "web_id": None,
        }
        row.update(values)
        bid = uuid4()
        async with self.Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "follower_count, quote, remark, category_tags, quality_tags, web_id, "
                    "created_at, updated_at) VALUES (:id, :tid, :acc, :nickname, :platform, "
                    ":follower_count, :quote, :remark, CAST(:category_tags AS jsonb), "
                    "CAST(:quality_tags AS jsonb), :web_id, "
                    "NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')"
                ),
                {"id": bid, "tid": self.tenant_id, "acc": self.acc(tag), **row},
            )
            await s.commit()
        return bid

    async def run(self, body: bytes, filename: str = "bloggers.csv") -> tuple[UUID, dict]:
        batch_id = uuid4()
        self.batch_ids.append(batch_id)
        key = f"imports/{self.tenant_id}/{batch_id}/{filename}"
        async with self.Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                    "original_filename, file_r2_key, file_bucket, status, total_rows, "
                    "imported, failed, retry_count, created_by, created_at, updated_at) "
                    "VALUES (:id, :tid, 'manual_blogger', :h, :fn, :k, "
                    "'private', 'processing', 0, 0, 0, 0, :cb, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": self.tenant_id,
                    "h": f"8b-{batch_id}",
                    "fn": filename,
                    "k": key,
                    "cb": self.user_id,
                },
            )
            await s.commit()
        self.files[key] = body
        return batch_id, await _run_import_batch(batch_id, only_failed=False)

    async def one(self, sql: str, **params: Any) -> Any:
        async with self.Maker() as s:
            return (await s.execute(text(sql), params)).first()

    async def all(self, sql: str, **params: Any) -> list[Any]:
        async with self.Maker() as s:
            return list((await s.execute(text(sql), params)).fetchall())

    async def row(self, tag: str, platform: str = "小红书") -> Any:
        return await self.one(
            "SELECT id, nickname, platform, wechat, follower_count, quote, remark, "
            "category_tags, quality_tags, web_id, homepage_url, blogger_type FROM blogger "
            "WHERE xiaohongshu_id = :a AND platform = :p AND is_deleted = false",
            a=self.acc(tag),
            p=platform,
        )

    async def jobs(self, batch_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT row_number, status, notes, error_detail FROM import_job "
            "WHERE batch_id = :b ORDER BY row_number",
            b=batch_id,
        )

    async def missing(self, batch_id: UUID) -> list[tuple[str, int, list[int]]]:
        """``/api/blogger-tags/missing`` 背后的服务（管理员视角）。"""
        perms = EffectivePermissions(user_id=str(self.user_id), scopes=frozenset({"*"}))
        async with self.Maker() as s:
            result = await BloggerTagDictService(s).missing_tags_for_batch(
                self.tenant_id, perms, batch_id
            )
        assert result.batch_id == batch_id
        return [(i.tag, i.count, i.rows) for i in result.items]

    async def conflicts(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT status, object_key, fields FROM import_conflict "
            "WHERE object_id = :o ORDER BY created_at",
            o=object_id,
        )

    async def audits(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT before, after FROM audit_log WHERE resource = 'blogger' "
            "AND resource_id = :r ORDER BY created_at",
            r=str(object_id),
        )

    async def cleanup(self) -> None:
        async with self.Maker() as c:
            ids = [
                r[0]
                for r in (
                    await c.execute(
                        text("SELECT id FROM blogger WHERE xiaohongshu_id LIKE :p"),
                        {"p": f"bx{self.suffix}%"},
                    )
                ).fetchall()
            ]
            if ids:
                await c.execute(
                    text("DELETE FROM import_conflict WHERE object_id = ANY(:ids)"), {"ids": ids}
                )
                await c.execute(
                    text(
                        "DELETE FROM audit_log WHERE resource = 'blogger' AND resource_id = ANY(:r)"
                    ),
                    {"r": [str(i) for i in ids]},
                )
            for batch_id in self.batch_ids:
                await c.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
            await c.execute(
                text("DELETE FROM blogger WHERE xiaohongshu_id LIKE :p"), {"p": f"bx{self.suffix}%"}
            )
            if self.tags:
                await c.execute(
                    text(
                        "DELETE FROM dict_item WHERE dict_type = 'blogger_tag' "
                        "AND value = ANY(CAST(:v AS text[]))"
                    ),
                    {"v": sorted(self.tags)},
                )
            await c.execute(text('DELETE FROM "user" WHERE id = :u'), {"u": self.user_id})
            await c.commit()


@pytest.fixture
async def env(engine: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(BloggerImportAdapter())
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
            {"id": user_id, "tid": tenant_id, "u": f"imp8b_{suffix}"},
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
        await e.cleanup()


def _csv(header: str, *lines: str) -> bytes:
    return (header + "\n" + "".join(f"{line}\n" for line in lines)).encode()


_H = "账号,昵称,平台,粉丝数,报价,类目标签,备注"


@pytest.mark.integration
@pytest.mark.asyncio
class TestManualBlogger8b:
    async def test_platform_in_key(self, env: _Env) -> None:
        """同账号、平台填抖音 → 新建抖音博主；平台空 → 命中小红书那个（全等 → 重复已跳过）。"""
        xhs = await env.blogger("P")
        batch_id, result = await env.run(
            _csv(_H, f"{env.acc('P')},抖音号,抖音,2000,,,", f"{env.acc('P')},小美,,1000,500,,")
        )
        assert result["status"] == "completed"
        assert [j.status for j in await env.jobs(batch_id)] == ["success", "skipped"]
        dy = await env.row("P", "抖音")
        assert (dy.nickname, dy.follower_count, dy.quality_tags) == ("抖音号", 2000, [])
        assert dy.id != xhs
        assert (await env.row("P")).nickname == "小美"
        assert await env.conflicts(xhs) == []

    async def test_invalid_platform_and_account(self, env: _Env) -> None:
        batch_id, result = await env.run(
            _csv(_H, f"{env.acc('W')},甲,微博,,,,", f"{env.acc('W')} x,乙,,,,,")
        )
        assert result["status"] == "failed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["failed", "failed"]
        assert "平台必须为" in jobs[0].error_detail
        assert "账号只能包含字母、数字、_ . -" in jobs[1].error_detail

    async def test_follower_overwritten_others_conflict(self, env: _Env) -> None:
        """R2：粉丝数不同 → 以文件为准覆盖、审计 via=import_overwrite；报价照旧进冲突；备注补空。"""
        bid = await env.blogger("F", blogger_type=None)
        batch_id, result = await env.run(_csv(_H, f'{env.acc("F")},小美,,"1.2w",600,,新备注'))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "conflict"
        row = await env.row("F")
        assert (row.follower_count, row.quote, row.remark) == (12000, Decimal("500.00"), "新备注")
        assert row.blogger_type is None  # 不重算
        [c] = await env.conflicts(bid)
        assert c.status == "pending"
        assert c.object_key == f"小红书·{env.acc('F')}"
        assert [(f["field"], f["system"], f["file"]) for f in c.fields] == [
            ("quote", "500.00", "600.00")
        ]
        audits = await env.audits(bid)
        by_via = {a.after["via"]: a for a in audits}
        assert set(by_via) == {"import_fill", "import_overwrite"}
        over = by_via["import_overwrite"]
        assert over.before == {"follower_count": 1000}
        assert over.after["follower_count"] == 12000
        assert over.after["import_batch_id"] == str(batch_id)
        assert over.after["row_number"] == 1

    async def test_follower_only_is_updated(self, env: _Env) -> None:
        bid = await env.blogger("U")
        batch_id, _ = await env.run(_csv(_H, f"{env.acc('U')},小美,,3000,500,,"))
        [job] = await env.jobs(batch_id)
        assert job.status == "success"
        assert (await env.row("U")).follower_count == 3000
        assert await env.conflicts(bid) == []

    async def test_unknown_tags_dropped_one_notice(self, env: _Env) -> None:
        """D2：字典外的类目标签与系统标签词丢掉，整批只提示一次。"""
        a, x, y = env.tag("美妆"), env.tag("未建"), env.tag("另一个")
        await env.add_tags(a)
        batch_id, result = await env.run(
            _csv(
                _H,
                f"{env.acc('T1')},甲,,,,{a};{x},",
                f"{env.acc('T2')},乙,,,,{y};高性价比,",
                f"{env.acc('T3')},丙,,,,{a},",
            )
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["success", "success", "success"]
        assert jobs[0].notes["warnings"] == [UNKNOWN_TAGS_NOTICE]
        assert jobs[1].notes is None
        assert jobs[2].notes is None
        assert (await env.row("T1")).category_tags == [a]
        assert (await env.row("T2")).category_tags == []
        assert (await env.row("T3")).category_tags == [a]
        b = await env.one("SELECT warning_count FROM import_batch WHERE id = :b", b=batch_id)
        assert b.warning_count == 1
        # 整批汇总（/missing 的服务）读的就是 runner 落下的原始行
        missing = await env.missing(batch_id)
        assert missing == sorted(
            [(x, 1, [jobs[0].row_number]), (y, 1, [jobs[1].row_number])], key=lambda m: m[0]
        )

        # §6.6 补回：主管补字典后再导（新文件）→ 原来没有标签的补空；已有别的标签的进冲突
        await env.add_tags(x, y)
        assert await env.missing(batch_id) == []
        batch2, result2 = await env.run(
            _csv(_H, f"{env.acc('T1')},甲,,,,{a};{x},", f"{env.acc('T2')},乙,,,,{y},")
        )
        assert result2["status"] == "completed"
        assert [j.status for j in await env.jobs(batch2)] == ["conflict", "filled"]
        t1 = await env.row("T1")
        assert t1.category_tags == [a]
        [c] = await env.conflicts(t1.id)
        assert [(f["field"], f["system"], f["file"]) for f in c.fields] == [
            ("category_tags", [a], sorted([a, x]))
        ]
        assert (await env.row("T2")).category_tags == [y]

    async def test_quality_tags_not_written(self, env: _Env) -> None:
        """D5：质量标签只由重算写；文件里有也不写、不比较，整批提示一次。"""
        bid = await env.blogger("Q", quality_tags='["高性价比"]')
        header = "账号,昵称,粉丝数,报价,质量标签"
        batch_id, result = await env.run(
            _csv(header, f"{env.acc('N')},新博主,,,优质", f"{env.acc('Q')},小美,1000,500,带货型")
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["success", "skipped"]
        assert jobs[0].notes["warnings"] == [QUALITY_TAGS_NOTICE]
        assert jobs[1].notes is None
        assert (await env.row("N")).quality_tags == []
        assert (await env.row("Q")).quality_tags == ["高性价比"]
        assert await env.conflicts(bid) == []

    async def test_web_id_column(self, env: _Env) -> None:
        """N2：手工模版可选「网页ID」；跨批网页ID 不同 → 进冲突、不覆盖（D1①）。"""
        header = "账号,昵称,网页ID,主页链接"
        b1, _ = await env.run(
            _csv(header, f"{env.acc('I')},小美,61900000001,https://example.com/u/1")
        )
        row = await env.row("I")
        assert (row.web_id, row.homepage_url) == ("61900000001", "https://example.com/u/1")
        b2, _ = await env.run(_csv(header, f"{env.acc('I')},小美,61900000002,"))
        [job] = await env.jobs(b2)
        assert job.status == "conflict"
        assert (await env.row("I")).web_id == "61900000001"
        [c] = await env.conflicts(row.id)
        assert [(f["field"], f["system"], f["file"]) for f in c.fields] == [
            ("web_id", "61900000001", "61900000002")
        ]

    async def test_invalid_homepage_url_dropped(self, env: _Env) -> None:
        header = "账号,昵称,主页链接"
        batch_id, result = await env.run(_csv(header, f"{env.acc('L')},小美,javascript:alert(1)"))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "success"
        assert job.notes["warnings"] == [
            "主页链接 的值不合法（不是 http/https 地址或超过 1024 字符），未写入"
        ]
        assert (await env.row("L")).homepage_url is None

    async def test_xlsx_aliases(self, env: _Env) -> None:
        """xlsx：别名表头、万 / w、千分位、占位符、多余列。"""
        wb = Workbook()
        ws = wb.active
        ws.append(["小红书号", "小红书昵称", "微信号", "粉丝量", "报价", "多余列"])
        ws.append([env.acc("X1"), "甲", "wx-1", "3.5万", "1,200", "忽略"])
        ws.append([env.acc("X2"), "乙", "--", 12345, "--", None])
        buf = io.BytesIO()
        wb.save(buf)
        batch_id, result = await env.run(buf.getvalue(), filename="bloggers.xlsx")
        assert result["status"] == "completed"
        x1, x2 = await env.row("X1"), await env.row("X2")
        assert (x1.nickname, x1.wechat, x1.follower_count, x1.quote) == (
            "甲",
            "wx-1",
            35000,
            Decimal("1200.00"),
        )
        assert (x2.wechat, x2.follower_count, x2.quote) == (None, 12345, None)


# ---------------------------------------------------------------------------
# 旧冲突里的「平台」：选覆盖 → invalid_value，可改选保留（§6.2）
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
async def test_old_platform_conflict_invalid_then_keep(
    session: AsyncSession,
    factory: Any,
    tenant_a: Any,
    import_batch_factory: Any,
    blogger_factory: Any,
) -> None:
    role = (await session.execute(select(Role).where(Role.code == "pr"))).scalar_one()
    user = await factory.user(tenant_a, roles=[role])
    perms = await AuthService(session).load_effective_permissions(user.id)
    b = await blogger_factory.blogger()
    batch = await import_batch_factory.batch(source="manual_blogger")
    c = ImportConflict(
        tenant_id=tenant_a.id,
        source="manual_blogger",
        batch_id=batch.id,
        row_numbers=[2],
        object_type="blogger",
        object_id=b.id,
        object_key=b.xiaohongshu_id,
        object_label=b.nickname,
        kind="fields",
        fields=[
            {
                "field": "platform",
                "label": "平台",
                "system": "小红书",
                "file": "抖音",
                "system_display": "小红书",
                "file_display": "抖音",
                "sensitive": None,
            }
        ],
        status="pending",
    )
    session.add(c)
    await session.flush()
    svc = ImportConflictService(session)

    def req(decision: str, expected: dict[str, Any] | None) -> ConflictResolveRequest:
        return ConflictResolveRequest(
            decision=decision, items=[{"id": c.id, "expected_system_values": expected}]
        )

    resp = await svc.resolve(req("overwrite", {"platform": "小红书"}), user, perms)
    [r] = resp.results
    assert (r.outcome, r.field) == ("invalid_value", "platform")
    assert r.message == "平台 的值不合法（平台是判重键，不能经导入修改）"
    stmt = select(Blogger).where(Blogger.id == b.id).execution_options(populate_existing=True)
    assert (await session.execute(stmt)).scalar_one().platform == "小红书"

    resp = await svc.resolve(req("keep", None), user, perms)
    assert [(x.outcome, x.status) for x in resp.results] == [("resolved", "kept")]
