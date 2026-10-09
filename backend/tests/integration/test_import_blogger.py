"""U06c 集成测试：BloggerImportAdapter 端到端（真实 adapter → runner → blogger 入库）。

复用 U06a/U06b test_import_runner 模式：monkeypatch AsyncSessionApp/Bypass → 测试 engine
+ mock attachment_service.get_object_bytes 注入样本 CSV + committed 数据 + finally 清理。

8a-6（设计 §5.5）：同小红书 ID 不再覆盖——全等 → 重复已跳过、系统为空 → 补空、两边都有值且不同 →
持久化冲突；规则声明换成 OVERWRITE / KEEP 行为随之改变；同批同一对象以第一个已提交的给值行为准；
占位符当没给值（N17）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.duplicate_rules import DUPLICATE_RULES, DuplicatePolicy, DuplicateRule
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


def _csv(suffix: str) -> bytes:
    """样本 CSV：新建 / 同 ID 第二行（值不同）/ 缺 ID 失败。"""
    return (
        "小红书ID,昵称,粉丝数,报价,类目标签,质量标签\n"
        f'xhs{suffix}A,小美,"12,500",500.00,美妆;护肤,优质\n'
        f"xhs{suffix}A,小美改名,13000,600,美妆,\n"
        f",无ID博主,1000,100,,\n"
    ).encode()


async def _tenant_id(Maker: Any) -> Any:
    async with Maker() as s:
        return (
            await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()[0]


async def _seed_batch(Maker, suffix: str, batch_id, created_by: Any = None) -> Any:
    async with Maker() as seed:
        tenant_id = (
            await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()[0]
        await seed.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, total_rows, "
                "imported, failed, retry_count, created_by, created_at, updated_at) "
                "VALUES (:id, :tid, 'manual_blogger', :h, 'bloggers.csv', :k, "
                "'private', 'processing', 0, 0, 0, 0, :cb, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": f"{suffix}-{batch_id}",
                "k": f"imports/{tenant_id}/{batch_id}/bloggers.csv",
                "cb": created_by,
            },
        )
        await seed.commit()
    return tenant_id


_QUALITY_TAGS_NOTICE = "质量标签是系统标签，由重算自动计算，导入不写入（整批只提示一次）"


async def _add_tags(Maker, tenant_id: Any, *values: str) -> None:
    """8b D2：类目标签只收字典里的；测试先建本租户的博主标签字典项（N18：重跑不撞唯一键）。"""
    async with Maker() as s:
        for value in values:
            await s.execute(
                text(
                    "INSERT INTO dict_item (id, tenant_id, dict_type, value, sort_order, "
                    "is_active, created_at, updated_at) VALUES (CAST(:id AS uuid), "
                    "CAST(:tid AS uuid), 'blogger_tag', CAST(:v AS text), 0, true, NOW(), NOW()) "
                    "ON CONFLICT (tenant_id, dict_type, value) DO NOTHING"
                ),
                {"id": str(uuid4()), "tid": str(tenant_id), "v": value},
            )
        await s.commit()


async def _drop_tags(Maker, tenant_id: Any, *values: str) -> None:
    async with Maker() as s:
        await s.execute(
            text(
                "DELETE FROM dict_item WHERE tenant_id = CAST(:tid AS uuid) "
                "AND dict_type = 'blogger_tag' AND value = ANY(CAST(:v AS text[]))"
            ),
            {"tid": str(tenant_id), "v": list(values)},
        )
        await s.commit()


async def _cleanup(Maker, suffix: str, batch_id) -> None:
    async with Maker() as c:
        await c.execute(text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id})
        await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
        await c.execute(
            text("DELETE FROM blogger WHERE xiaohongshu_id LIKE :p"),
            {"p": f"xhs{suffix}%"},
        )
        await c.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestBloggerImportEndToEnd:
    async def test_end_to_end_partial_and_update(self, engine: Any, monkeypatch) -> None:
        """新建 + 同 ID 第二行 + 缺 ID failed → partial；标签 JSONB + int + Decimal。

        8a-6 起同一批次里同一小红书 ID 以第一行为准（N15 博主版）：第 2 行与第 1 行不一致的字段
        只有提示、不覆盖，该行计「重复已跳过」（旧：第 2 行 UPDATE 覆盖成「小美改名」）。
        """
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(BloggerImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = _csv(suffix)
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        tenant_id = await _seed_batch(Maker, suffix, batch_id)
        await _add_tags(Maker, tenant_id, "美妆", "护肤")
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            # 行 1 新建 + 行 2 同 ID 重复已跳过 + 行 3 缺 ID failed
            assert result["imported"] == 1
            assert result["failed"] == 1

            async with Maker() as check:
                bloggers = (
                    await check.execute(
                        text(
                            "SELECT xiaohongshu_id, nickname, follower_count, quote, "
                            "category_tags, tenant_id FROM blogger "
                            "WHERE xiaohongshu_id LIKE :p"
                        ),
                        {"p": f"xhs{suffix}%"},
                    )
                ).fetchall()
                assert len(bloggers) == 1
                blg = bloggers[0]
                # 以第 1 行为准
                assert blg[1] == "小美"
                assert blg[2] == 12500
                assert blg[3] == Decimal("500.00")
                # 标签 JSONB 数组
                assert blg[4] == ["美妆", "护肤"]
                # 跨租户正确
                assert str(blg[5]) == str(tenant_id)
                # 8b D5：「质量标签」列（旧模版）不写
                quality = (
                    await check.execute(
                        text("SELECT quality_tags FROM blogger WHERE xiaohongshu_id LIKE :p"),
                        {"p": f"xhs{suffix}%"},
                    )
                ).scalar_one()
                assert quality == []

                jobs = (
                    await check.execute(
                        text(
                            "SELECT row_number, status, error_detail, notes FROM import_job "
                            "WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert [j[1] for j in jobs] == ["success", "skipped", "failed"]
                assert "账号" in (jobs[2][2] or "")
                assert jobs[0][3]["warnings"] == [_QUALITY_TAGS_NOTICE]
                assert set(jobs[1][3]["warnings"]) == {
                    "第 2 行的昵称与第 1 行不一致，按第 1 行处理",
                    "第 2 行的粉丝数与第 1 行不一致，按第 1 行处理",
                    "第 2 行的报价与第 1 行不一致，按第 1 行处理",
                    "第 2 行的类目标签与第 1 行不一致，按第 1 行处理",
                }
                n_conflicts = (
                    await check.execute(
                        text("SELECT count(*) FROM import_conflict WHERE batch_id = :b"),
                        {"b": batch_id},
                    )
                ).scalar_one()
                assert n_conflicts == 0
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
            await _drop_tags(Maker, tenant_id, "美妆", "护肤")

    async def test_tags_jsonb_first_row(self, engine: Any, monkeypatch) -> None:
        """验证多标签解析为 JSONB 数组（单行隔离，不被第 2 行 UPDATE 覆盖）。"""
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(BloggerImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "小红书ID,昵称,类目标签\n" f'xhs{suffix}T,标签博主,"美妆;护肤,穿搭"\n'
        ).encode()
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        tenant_id = await _seed_batch(Maker, suffix, batch_id)
        await _add_tags(Maker, tenant_id, "美妆", "护肤", "穿搭")
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "completed"
            async with Maker() as check:
                tags = (
                    await check.execute(
                        text("SELECT category_tags FROM blogger " "WHERE xiaohongshu_id = :x"),
                        {"x": f"xhs{suffix}T"},
                    )
                ).scalar_one()
                assert tags == ["美妆", "护肤", "穿搭"]
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
            await _drop_tags(Maker, tenant_id, "美妆", "护肤", "穿搭")


# ---------------------------------------------------------------------------
# 8a-6：按声明的重复规则（COMPARE / OVERWRITE / KEEP）
# ---------------------------------------------------------------------------

_HEADER = "小红书ID,昵称,平台,微信,粉丝数,报价,类目标签,备注\n"


@dataclass
class _Env:
    """一个用例的造数与清理：博主、批次、文件、导入人。"""

    Maker: Any
    suffix: str
    tenant_id: Any
    user_id: UUID
    files: dict[str, bytes] = field(default_factory=dict)
    batch_ids: list[UUID] = field(default_factory=list)

    def xhs(self, tag: str) -> str:
        return f"xhs{self.suffix}{tag}"

    async def blogger(self, tag: str, **values: Any) -> UUID:
        row = {
            "nickname": "小美",
            "platform": "小红书",
            "wechat": None,
            "follower_count": 1000,
            "quote": Decimal("500.00"),
            "remark": None,
            "category_tags": '["美妆"]',
        }
        row.update(values)
        bid = uuid4()
        async with self.Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "wechat, follower_count, quote, remark, category_tags, quality_tags, "
                    "created_at, updated_at) VALUES (:id, :tid, :xhs, :nickname, :platform, "
                    ":wechat, :follower_count, :quote, :remark, CAST(:category_tags AS jsonb), "
                    "'[]'::jsonb, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')"
                ),
                {"id": bid, "tid": self.tenant_id, "xhs": self.xhs(tag), **row},
            )
            await s.commit()
        return bid

    async def run(self, csv_body: str, *, only_failed: bool = False) -> tuple[UUID, dict]:
        batch_id = uuid4()
        self.batch_ids.append(batch_id)
        await _seed_batch(self.Maker, self.suffix, batch_id, created_by=self.user_id)
        self.files[f"imports/{self.tenant_id}/{batch_id}/bloggers.csv"] = (
            _HEADER + csv_body
        ).encode()
        result = await _run_import_batch(batch_id, only_failed=only_failed)
        return batch_id, result

    async def one(self, sql: str, **params: Any) -> Any:
        async with self.Maker() as s:
            return (await s.execute(text(sql), params)).first()

    async def all(self, sql: str, **params: Any) -> list[Any]:
        async with self.Maker() as s:
            return list((await s.execute(text(sql), params)).fetchall())

    async def blogger_row(self, tag: str) -> Any:
        return await self.one(
            "SELECT id, nickname, wechat, follower_count, quote, remark, updated_at, "
            "blogger_type FROM blogger WHERE xiaohongshu_id = :x AND is_deleted = false",
            x=self.xhs(tag),
        )

    async def jobs(self, batch_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT row_number, status, notes, error_detail FROM import_job "
            "WHERE batch_id = :b ORDER BY row_number",
            b=batch_id,
        )

    async def batch(self, batch_id: UUID) -> Any:
        return await self.one(
            "SELECT status, total_rows, imported, failed, filled, skipped, conflicted, "
            "warning_count, filled_objects FROM import_batch WHERE id = :b",
            b=batch_id,
        )

    async def conflicts(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT id, status, batch_id, fields, row_numbers, superseded_by, created_by "
            "FROM import_conflict WHERE object_id = :o ORDER BY created_at",
            o=object_id,
        )

    async def audits(self, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT action, actor_type, user_id, before, after FROM audit_log "
            "WHERE resource = 'blogger' AND resource_id = :r ORDER BY created_at",
            r=str(object_id),
        )

    async def cleanup(self) -> None:
        async with self.Maker() as c:
            ids = [
                r[0]
                for r in (
                    await c.execute(
                        text("SELECT id FROM blogger WHERE xiaohongshu_id LIKE :p"),
                        {"p": f"xhs{self.suffix}%"},
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
                    text("DELETE FROM import_conflict WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
            await c.execute(
                text("DELETE FROM blogger WHERE xiaohongshu_id LIKE :p"),
                {"p": f"xhs{self.suffix}%"},
            )
            await c.execute(text('DELETE FROM "user" WHERE id = :u'), {"u": self.user_id})
            await c.commit()
        await _drop_tags(self.Maker, self.tenant_id, "美妆")


@pytest.fixture
async def env(engine: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(BloggerImportAdapter())

    tenant_id = await _tenant_id(Maker)
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
    # 8b D2：_line 默认类目标签「美妆」，建进字典，否则用例会多出字典外标签的提示
    await _add_tags(Maker, tenant_id, "美妆")
    e = _Env(Maker=Maker, suffix=suffix, tenant_id=tenant_id, user_id=user_id)

    import app.core.attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "get_object_bytes", lambda b, k: e.files[k])
    try:
        yield e
    finally:
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry._adapters.update(saved)
        await e.cleanup()


def _line(xhs: str, **cells: Any) -> str:
    cols = ["nickname", "platform", "wechat", "follower_count", "quote", "tags", "remark"]
    defaults = {"nickname": "小美", "follower_count": "1000", "quote": "500", "tags": "美妆"}
    values = {**defaults, **cells}
    return ",".join([xhs, *(str(values.get(c, "")) for c in cols)]) + "\n"


@pytest.mark.integration
@pytest.mark.asyncio
class TestBloggerDuplicateRules:
    async def test_identical_is_skipped(self, env: _Env) -> None:
        """AC 49：映射字段全等（500 = 500.00）→ 重复已跳过，updated_at 不变，批次 completed。"""
        bid = await env.blogger("S")
        before = await env.blogger_row("S")
        batch_id, result = await env.run(_line(env.xhs("S"), quote="500.00"))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert (job.status, job.notes) == ("skipped", None)
        b = await env.batch(batch_id)
        assert (b.status, b.total_rows, b.imported, b.skipped, b.conflicted, b.filled) == (
            "completed",
            1,
            0,
            1,
            0,
            0,
        )
        after = await env.blogger_row("S")
        assert after.updated_at == before.updated_at
        assert await env.conflicts(bid) == []
        assert await env.audits(bid) == []

    async def test_conflict_not_overwritten_and_fill(self, env: _Env) -> None:
        """AC 49 / N1：两边都有值且不同 → 冲突不覆盖；系统为空 → 补空照做；另一行失败 → partial。"""
        bid = await env.blogger("C", remark=None)
        before = await env.blogger_row("C")
        batch_id, result = await env.run(
            _line(env.xhs("C"), quote="600", remark="新备注") + ",缺ID,,,,,,\n"
        )
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["conflict", "failed"]
        assert jobs[0].notes["filled"] == [
            {"object_type": "blogger", "object_label": "小美", "fields": ["remark"]}
        ]
        b = await env.batch(batch_id)
        assert (b.status, b.conflicted, b.failed, b.filled, b.filled_objects) == (
            "partial",
            1,
            1,
            0,
            1,
        )
        after = await env.blogger_row("C")
        assert after.quote == Decimal("500.00")  # 冲突字段不改
        assert after.remark == "新备注"  # 补空照做
        assert after.updated_at != before.updated_at

        [c] = await env.conflicts(bid)
        assert (c.status, c.batch_id, c.row_numbers, c.created_by) == (
            "pending",
            batch_id,
            [1],
            env.user_id,
        )
        assert [(f["field"], f["system"], f["file"]) for f in c.fields] == [
            ("quote", "500.00", "600.00")
        ]
        [audit] = await env.audits(bid)
        assert (audit.action, audit.actor_type, audit.user_id) == (
            "blogger.update",
            "worker",
            env.user_id,
        )
        assert audit.before == {"remark": None}
        assert audit.after["remark"] == "新备注"
        assert audit.after["via"] == "import_fill"
        assert audit.after["import_batch_id"] == str(batch_id)
        assert audit.after["row_number"] == 1

    async def test_fill_only_counts_filled(self, env: _Env) -> None:
        bid = await env.blogger("F", wechat=None, remark=None)
        batch_id, result = await env.run(_line(env.xhs("F"), wechat="wx123", remark="r"))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "filled"
        assert set(job.notes["filled"][0]["fields"]) == {"wechat", "remark"}
        b = await env.batch(batch_id)
        assert (b.filled, b.filled_objects, b.imported) == (1, 1, 0)
        [audit] = await env.audits(bid)
        assert audit.after["wechat_changed"] is True
        assert "wechat" not in audit.after
        assert "wechat" not in audit.before
        assert "wx123" not in str(audit.after)

    async def test_same_id_two_rows_first_wins(self, env: _Env) -> None:
        """N15 博主版：同一小红书 ID 两行报价不同 → 以第一行为准、第二行提示，不记冲突。"""
        bid = await env.blogger("Q", quote=None)
        batch_id, result = await env.run(
            _line(env.xhs("Q"), quote="500") + _line(env.xhs("Q"), quote="600")
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["filled", "skipped"]
        assert jobs[1].notes["warnings"] == ["第 2 行的报价与第 1 行不一致，按第 1 行处理"]
        assert (await env.blogger_row("Q")).quote == Decimal("500.00")
        assert await env.conflicts(bid) == []
        b = await env.batch(batch_id)
        assert b.warning_count == 1

    async def test_placeholders(self, env: _Env) -> None:
        """N17：新博主微信「-」→ NULL；已有博主微信 wx、文件「-」→ 不补空不冲突；粉丝数「-」不失败。"""
        bid = await env.blogger("P", wechat="wx")
        batch_id, result = await env.run(
            _line(env.xhs("N"), wechat="-", follower_count="-", quote="--", tags="-")
            + _line(env.xhs("P"), wechat="—")
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["success", "skipped"]
        new = await env.one(
            "SELECT wechat, follower_count, quote, category_tags FROM blogger "
            "WHERE xiaohongshu_id = :x",
            x=env.xhs("N"),
        )
        assert (new.wechat, new.follower_count, new.quote, new.category_tags) == (
            None,
            None,
            None,
            [],
        )
        assert (await env.blogger_row("P")).wechat == "wx"
        assert await env.conflicts(bid) == []

    async def test_rule_overwrite(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC 53：声明换成 OVERWRITE → 同样的数据走覆盖路径，审计 via import_overwrite。"""
        monkeypatch.setitem(
            DUPLICATE_RULES,
            "manual_blogger",
            DuplicateRule(DuplicatePolicy.OVERWRITE, key="小红书 ID", configurable=True),
        )
        bid = await env.blogger("O")  # blogger_type 为空：覆盖粉丝数后仍为空
        batch_id, result = await env.run(_line(env.xhs("O"), follower_count="2000000"))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "success"
        row = await env.blogger_row("O")
        assert row.follower_count == 2_000_000
        assert row.blogger_type is None  # 不重算博主类型
        assert await env.conflicts(bid) == []
        [audit] = await env.audits(bid)
        assert audit.after["via"] == "import_overwrite"
        assert (audit.before, audit.after["follower_count"]) == (
            {"follower_count": 1000},
            2_000_000,
        )

    async def test_rule_keep(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(
            DUPLICATE_RULES,
            "manual_blogger",
            DuplicateRule(DuplicatePolicy.KEEP, key="小红书 ID", configurable=True),
        )
        bid = await env.blogger("K", remark=None)
        batch_id, result = await env.run(_line(env.xhs("K"), follower_count="2000", remark="r"))
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "skipped"
        row = await env.blogger_row("K")
        assert (row.follower_count, row.remark) == (1000, None)  # 不比较、不补空
        assert await env.conflicts(bid) == []

    async def test_other_batch_pending_superseded(self, env: _Env) -> None:
        """AC 47：别的批次的待处理冲突再导入 → 旧冲突取代、始终只一条待处理。"""
        bid = await env.blogger("X")
        b1, _ = await env.run(_line(env.xhs("X"), quote="600"))
        b2, r2 = await env.run(_line(env.xhs("X"), quote="700", nickname="小美 "))
        assert r2["status"] == "completed"
        rows = await env.conflicts(bid)
        assert [r.status for r in rows] == ["superseded", "pending"]
        old, new = rows
        assert old.batch_id == b1
        assert old.superseded_by == new.id
        assert new.batch_id == b2
        assert [(f["field"], f["file"]) for f in new.fields] == [("quote", "700.00")]
        # 第三次导入与系统一致 → 旧冲突的字段都比较过且一致 → 取代、不建新冲突
        _, r3 = await env.run(_line(env.xhs("X"), quote="500"))
        assert r3["status"] == "completed"
        rows = await env.conflicts(bid)
        assert [r.status for r in rows] == ["superseded", "superseded"]

    async def test_registered_row_then_failed(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N15e：第 1 行登记后失败 → 第 2 行的值正常补空、没有「按第 1 行处理」提示。"""
        bid = await env.blogger("E", remark=None)
        real: Callable[..., Any] = tasks._upsert_job

        async def flaky(session: Any, **kw: Any) -> None:
            if kw["row_number"] == 1 and kw["status"] != "failed":
                raise RuntimeError("模拟第 1 行写入失败")
            await real(session, **kw)

        monkeypatch.setattr(tasks, "_upsert_job", flaky)
        batch_id, result = await env.run(
            _line(env.xhs("E"), remark="A") + _line(env.xhs("E"), remark="B")
        )
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["failed", "filled"]
        assert jobs[1].notes["warnings"] == []
        assert (await env.blogger_row("E")).remark == "B"
        assert await env.conflicts(bid) == []
