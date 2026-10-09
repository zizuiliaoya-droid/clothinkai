"""8b 灰豚抖音博主库导入（来源 ``huitun_douyin``，设计 §6.3、§6.5、§10；补充一 D1、补充二 D3 / R2）。

走真实 runner（``_run_import_batch``）：openpyxl 照灰豚「抖音博主库」造两行表头（分组行 + 列名行）、
重名列、表头后面 9 个空列、另一个「7天视频详情」sheet 当活动 sheet。造数全是脱敏假数据（假昵称、假 ID）。
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.auth.service import AuthService
from app.modules.blogger.repository import BloggerListFilters, BloggerRepository
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.adapters.blogger_douyin import HuitunDouyinImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.service import ImportService
from app.tasks.import_tasks import _run_import_batch

SOURCE = "huitun_douyin"
MISSING_ID = (
    "博主ID为空：灰豚导出的这一行没有博主ID，未导入；请在灰豚补齐后重新导出，或在博主页手工新建"
)

# 灰豚「抖音博主库」第 1 行（分组，只写在每组第一格）与第 2 行（列名），后面 9 个空列
_GROUP: list[Any] = (
    ["达人概况", *[None] * 8, "30天数据概况", *[None] * 4, "粉丝概况", None]
    + ["30天视频分析", *[None] * 5, "7天视频分析", *[None] * 6]
    + [None] * 9
)
_HEADER: list[Any] = [
    "联系人",
    "抖音博主",
    "博主ID",
    "网页ID",
    "微信号",
    "报价",
    "灰豚指数",
    "粉丝总量",
    "赞粉比",
    "新增粉丝",
    "新增内容",
    "预估曝光",
    "新增点赞",
    "新增评论",
    "性别分布",
    "年龄分布",
    "视频数（不用抓取）",
    "点赞",
    "评论",
    "分享",
    "收藏",
    "互动量",
    "视频数",
    "点赞",
    "评论",
    "分享",
    "收藏",
    "互动量",
    "曝光点赞比",
    *[None] * 9,
]
assert len(_GROUP) == len(_HEADER) == 38


def _line(
    account: Any,
    nickname: Any,
    *,
    web_id: Any = None,
    wechat: Any = None,
    quote: Any = None,
    fans: Any = None,
    contact: Any = "员工甲",
    **metrics: Any,
) -> list[Any]:
    """一行数据；统计列用列名关键字给（重名列用 ``like30`` / ``like7`` 这两个别名）。"""
    by_name = {
        "联系人": contact,
        "抖音博主": nickname,
        "博主ID": account,
        "网页ID": web_id,
        "微信号": wechat,
        "报价": quote,
        "粉丝总量": fans,
    }
    out: list[Any] = []
    seen: dict[str, int] = {}
    for name in _HEADER:
        if name is None:
            out.append(None)
            continue
        seen[name] = seen.get(name, 0) + 1
        if name in by_name:
            out.append(by_name[name])
        elif name == "点赞":
            out.append(metrics.get("like30" if seen[name] == 1 else "like7"))
        else:
            out.append(metrics.get(name))
    return out


def _book(*lines: list[Any], sheet: str = "抖音博主库") -> bytes:
    wb = Workbook()
    detail = wb.active
    detail.title = "7天视频详情"
    detail.append(["视频标题", "博主ID"])
    detail.append(["假视频", "不该被读"])
    ws = wb.create_sheet(sheet)
    ws.append(_GROUP)
    ws.append(_HEADER)
    for line in lines:
        ws.append(line)
    wb.active = 0  # 活动 sheet 是「7天视频详情」：版式要按名字选「抖音博主库」
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dataclass
class _Env:
    Maker: Any
    suffix: str
    tenant_id: Any
    user_id: UUID
    files: dict[str, bytes] = field(default_factory=dict)
    batch_ids: list[UUID] = field(default_factory=list)
    extra_accounts: list[str] = field(default_factory=list)  # 不带前缀的账号（纯数字博主ID）

    def acc(self, tag: str) -> str:
        return f"dy{self.suffix}.{tag}"

    async def run(self, body: bytes, filename: str = "抖音博主数据库.xlsx") -> tuple[UUID, dict]:
        batch_id = uuid4()
        self.batch_ids.append(batch_id)
        key = f"imports/{self.tenant_id}/{batch_id}/x.xlsx"
        async with self.Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                    "original_filename, file_r2_key, file_bucket, status, total_rows, "
                    "imported, failed, retry_count, created_by, created_at, updated_at) "
                    "VALUES (:id, :tid, :src, :h, :fn, :k, "
                    "'private', 'processing', 0, 0, 0, 0, :cb, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": self.tenant_id,
                    "src": SOURCE,
                    "h": f"dy-{batch_id}",
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

    async def row(self, account: str, platform: str = "抖音") -> Any:
        return await self.one(
            "SELECT id, nickname, platform, wechat, follower_count, quote, quote_note, web_id, "
            "contact_primary, blogger_type, platform_metrics, remark FROM blogger "
            "WHERE tenant_id = :t AND xiaohongshu_id = :a AND platform = :p "
            "AND is_deleted = false",
            t=self.tenant_id,
            a=account,
            p=platform,
        )

    async def jobs(self, batch_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT row_number, status, notes, error_detail, raw_data FROM import_job "
            "WHERE batch_id = :b ORDER BY row_number",
            b=batch_id,
        )

    async def conflicts(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT status, source, object_key, fields FROM import_conflict "
            "WHERE object_id = :o ORDER BY created_at",
            o=object_id,
        )

    async def audits(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT action, before, after FROM audit_log WHERE resource = 'blogger' "
            "AND resource_id = :r ORDER BY created_at",
            r=str(object_id),
        )

    async def xhs_blogger(self, account: str) -> UUID:
        bid = uuid4()
        async with self.Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "follower_count, quote, category_tags, quality_tags, created_at, updated_at) "
                    "VALUES (:id, :tid, :acc, '小美', '小红书', 1000, 500, '[]', '[]', "
                    "NOW(), NOW())"
                ),
                {"id": bid, "tid": self.tenant_id, "acc": account},
            )
            await s.commit()
        return bid

    async def cleanup(self) -> None:
        pattern = f"dy{self.suffix}%"
        mine = (
            "tenant_id = :t AND (xiaohongshu_id LIKE :p "
            "OR xiaohongshu_id = ANY(CAST(:extra AS text[])))"
        )
        params = {"t": self.tenant_id, "p": pattern, "extra": self.extra_accounts}
        async with self.Maker() as c:
            ids = [
                r[0]
                for r in (
                    await c.execute(text(f"SELECT id FROM blogger WHERE {mine}"), params)
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
                    text("DELETE FROM import_conflict WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
            await c.execute(text(f"DELETE FROM blogger WHERE {mine}"), params)
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
    ImportAdapterRegistry.register(HuitunDouyinImportAdapter())
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
            {"id": user_id, "tid": tenant_id, "u": f"impdy_{suffix}"},
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


_METRICS_A: dict[str, Any] = {
    "灰豚指数": "85.3",
    "赞粉比": "12.5",
    "新增粉丝": "-1,234",
    "新增内容": 3,
    "预估曝光": "3.5亿",
    "新增点赞": "--",
    "性别分布": "女性居多，占比60.00%",
    "年龄分布": "18-23岁",
    "视频数（不用抓取）": "12",
    "like30": "2.1w",
    "like7": "800",
    "互动量": "1.5万+",
    "曝光点赞比": "1.20%",
}


@pytest.mark.integration
@pytest.mark.asyncio
class TestHuitunDouyin:
    async def test_create_from_export(self, env: _Env) -> None:
        """建抖音博主：两行表头、重名列、数字 / 含「.」/ 空的博主ID、w / 亿、文字报价、空列。"""
        numeric_id = int("8" + env.suffix.translate(str.maketrans("abcdef", "123456")))
        env.extra_accounts.append(str(numeric_id))
        batch_id, result = await env.run(
            _book(
                _line(
                    numeric_id,
                    "抖音甲",
                    web_id=61900000001,
                    wechat="wx-dy-a",
                    quote="图文500 视频800",
                    fans="1.2w",
                    **_METRICS_A,
                ),
                _line(env.acc("B"), "抖音乙", quote="500", fans=3000, contact=None),
                _line(None, "抖音丙", wechat="wx-dy-c", quote="300"),
            )
        )
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert [(j.row_number, j.status) for j in jobs] == [
            (1, "success"),
            (2, "success"),
            (3, "failed"),
        ]
        assert jobs[2].error_detail == f"RowValidationError: {MISSING_ID}"

        a = await env.row(str(numeric_id))
        assert a is not None
        assert (a.nickname, a.platform, a.follower_count, a.web_id, a.wechat) == (
            "抖音甲",
            "抖音",
            12000,
            "61900000001",
            "wx-dy-a",
        )
        assert (a.quote, a.quote_note) == (None, "图文500 视频800")
        assert a.contact_primary is None  # D4：联系人不导
        assert a.blogger_type is None  # 抖音不按粉丝数分级，导入也不算
        # 统计快照：原文（非空、非占位）+ 能解析的数值；样本里每个统计列都核对一次
        assert a.platform_metrics == {
            "source": SOURCE,
            "raw": {
                "灰豚指数": "85.3",
                "粉丝总量": "1.2w",
                "赞粉比": "12.5",
                "新增粉丝": "-1,234",
                "新增内容": "3",
                "预估曝光": "3.5亿",
                "性别分布": "女性居多，占比60.00%",
                "年龄分布": "18-23岁",
                "视频数（不用抓取）": "12",
                "30天视频分析·点赞": "2.1w",
                "7天视频分析·点赞": "800",
                "30天视频分析·互动量": "1.5万+",
                "7天视频分析·互动量": "1.5万+",
                "曝光点赞比": "1.20%",
            },
            "values": {
                "灰豚指数": 85.3,
                "粉丝总量": 12000,
                "新增粉丝": -1234,
                "新增内容": 3,
                "预估曝光": 350000000,
                "视频数（不用抓取）": 12,
                "30天视频分析·点赞": 21000,
                "7天视频分析·点赞": 800,
            },
        }
        dumped = json.dumps(a.platform_metrics, ensure_ascii=False)
        for secret in ("wx-dy-a", "图文500", "员工甲"):
            assert secret not in dumped  # 报价、微信、联系人都不进统计快照

        b = await env.row(env.acc("B"))
        assert (b.quote, b.quote_note, b.follower_count) == (Decimal("500.00"), "500", 3000)
        assert b.platform_metrics == {
            "source": SOURCE,
            "raw": {"粉丝总量": "3000"},
            "values": {"粉丝总量": 3000},
        }

        # 网页ID 可搜（博主列表关键字）
        async with env.Maker() as s:
            items, _ = await BloggerRepository(s).list(
                filters=BloggerListFilters(keyword="61900000001")
            )
        assert [i.id for i in items if i.tenant_id == env.tenant_id] == [a.id]

    async def test_same_id_in_batch(self, env: _Env) -> None:
        """D1：同批同博主ID——网页ID 相同（或没给）的后续行按第一行为准；网页ID 不同的整行不导。"""
        acc = env.acc("S")
        batch_id, result = await env.run(
            _book(
                _line(acc, "抖音甲", web_id="61900000011", quote="图文500", fans=1000),
                _line(acc, "抖音甲2", web_id="61900000011", fans=2000, **{"灰豚指数": "70"}),
                _line(acc, "别人", web_id="61900000099", quote="图文900", fans=9000),
                _line(acc, "抖音甲", fans=1000),
            )
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["success", "skipped", "skipped", "skipped"]
        assert jobs[2].notes["warnings"] == [
            f"博主ID {acc} 与第 1 行相同但网页ID 不同，可能是不同的人，本行未导入"
        ]
        assert jobs[1].notes["warnings"] == [
            "第 2 行的昵称与第 1 行不一致，按第 1 行处理",
            "第 2 行的粉丝数与第 1 行不一致，按第 1 行处理",
            "第 2 行的抖音统计与第 1 行不一致，按第 1 行处理",
        ]
        assert jobs[3].notes is None
        row = await env.row(acc)
        assert (row.nickname, row.web_id, row.follower_count, row.quote_note) == (
            "抖音甲",
            "61900000011",
            1000,
            "图文500",
        )
        assert row.platform_metrics["raw"] == {"粉丝总量": "1000"}
        assert await env.conflicts(row.id) == []

    async def test_reimport_snapshot_overwrites_web_id_conflicts(self, env: _Env) -> None:
        """再导新快照：粉丝数与统计以文件为准覆盖（R2，留审计）；网页ID、报价备注不同进冲突；
        同账号的小红书博主不受影响。"""
        acc = env.acc("R")
        xhs = await env.xhs_blogger(acc)
        await env.run(_book(_line(acc, "抖音甲", web_id="61900000021", quote="图文500", fans="1w")))
        first = await env.row(acc)
        batch2, result2 = await env.run(
            _book(
                _line(
                    acc,
                    "抖音甲",
                    web_id="61900000022",
                    quote="图文600",
                    fans="1.5w",
                    **{"灰豚指数": "90"},
                )
            )
        )
        assert result2["status"] == "completed"
        [job] = await env.jobs(batch2)
        assert job.status == "conflict"
        row = await env.row(acc)
        assert row.id == first.id
        assert row.follower_count == 15000
        assert row.platform_metrics["raw"] == {"灰豚指数": "90", "粉丝总量": "1.5w"}
        assert (row.web_id, row.quote_note) == ("61900000021", "图文500")
        [c] = await env.conflicts(row.id)
        assert (c.status, c.source, c.object_key) == ("pending", SOURCE, f"抖音·{acc}")
        assert [(f["field"], f["system"], f["file"], f["sensitive"]) for f in c.fields] == [
            ("web_id", "61900000021", "61900000022", None),
            ("quote_note", "图文500", "图文600", ["blogger", "quote"]),
        ]
        audits = await env.audits(row.id)
        overwrites = [a.after for a in audits if a.after.get("via") == "import_overwrite"]
        assert [o["follower_count"] for o in overwrites if "follower_count" in o] == [15000]
        snap = [o for o in overwrites if o.get("platform_metrics_changed")]
        assert len(snap) == 1
        assert snap[0] == {
            "platform_metrics_changed": True,
            "via": "import_overwrite",
            "import_batch_id": str(batch2),
            "row_number": 1,
        }
        assert all(a.action == "blogger.update" for a in audits)
        # 同账号的小红书博主不动
        x = await env.row(acc, "小红书")
        assert (x.id, x.nickname, x.follower_count, x.platform_metrics) == (xhs, "小美", 1000, None)
        assert await env.conflicts(xhs) == []

        # 同一份快照再导：全等 → 重复已跳过，不再写审计
        before = len(audits)
        batch3, _ = await env.run(
            _book(
                _line(
                    acc,
                    "抖音甲",
                    web_id="61900000021",
                    quote="图文500",
                    fans="1.5w",
                    **{"灰豚指数": "90"},
                )
            )
        )
        [job3] = await env.jobs(batch3)
        assert job3.status == "skipped"
        assert len(await env.audits(row.id)) == before

    async def test_quote_note_too_long_dropped(self, env: _Env) -> None:
        acc = env.acc("L")
        batch_id, _ = await env.run(_book(_line(acc, "抖音甲", quote="图" * 501)))
        [job] = await env.jobs(batch_id)
        assert job.status == "success"
        assert job.notes["warnings"] == ["报价备注 超过 500 字，未写入"]
        row = await env.row(acc)
        assert (row.quote, row.quote_note) == (None, None)

    async def test_wrong_file_fails_batch(self, env: _Env) -> None:
        batch_id, result = await env.run(_book(_line("X1", "甲"), sheet="Sheet9"))
        assert result["status"] == "failed"
        b = await env.one("SELECT error_summary FROM import_batch WHERE id = :b", b=batch_id)
        assert b.error_summary == (
            "没有找到『抖音博主库』sheet 或『博主ID』表头，请上传灰豚导出的原文件"
        )

    async def test_failed_rows_mask_wechat_and_quote(
        self, env: _Env, session: AsyncSession, factory: Any
    ) -> None:
        """失败明细：运营看不到微信号 / 报价原文（N3、M4）；PR 看得到。博主ID 不受保护。"""
        acc = env.acc("M")
        batch_id, result = await env.run(
            _book(_line(acc, "抖音甲", wechat="wx-dy-m", quote="图文700", fans="很多"))
        )
        assert result["status"] == "failed"
        [job] = await env.jobs(batch_id)
        assert job.error_detail == "RowValidationError: 粉丝数必须为非负整数"
        assert (job.raw_data["微信号"], job.raw_data["报价"]) == ("wx-dy-m", "图文700")

        class _Tenant:
            id = env.tenant_id

        async def download(role_code: str) -> dict[str, Any]:
            role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
            user = await factory.user(_Tenant(), roles=[role])
            perms = await AuthService(session).load_effective_permissions(user.id)
            data = await ImportService(session).build_error_csv(batch_id, user, perms)
            [rec] = list(csv.DictReader(io.StringIO(data.decode("utf-8").lstrip("\ufeff"))))
            return json.loads(rec["raw_data"])

        token = tenant_id_ctx.set(env.tenant_id)
        try:
            ops = await download("operations")
            pr = await download("pr")
        finally:
            tenant_id_ctx.reset(token)
        assert (ops["微信号"], ops["报价"], ops["博主ID"]) == ("***", "***", acc)
        assert (pr["微信号"], pr["报价"]) == ("wx-dy-m", "图文700")
