"""8a-4 商品资料导入：对齐聚水潭导出，简称 / 季节 / 品牌写商品层并顺手建单品（设计 §5）。

走真实 runner（``_run_import_batch``）：提交种子数据 → monkeypatch ``get_object_bytes`` 返回现生成的
xlsx（表头照抄聚水潭 42 列，设计 §1.2）→ 断言 → 清理。同一份文件再传会被哈希去重，这里直接造
批次行、不经上传，所以「再导一次」只需换一个新批次。
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.duplicate_rules import DUPLICATE_RULES, DuplicatePolicy, DuplicateRule
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch

SOURCE = "manual_style_sku"

# 聚水潭商品资料导出的 42 列（仓库根「商品资料导入模版.xlsx」实测，设计 §1.2）
JST_42 = [
    "图片", "款式编码", "商品编码", "商品名称", "商品简称", "颜色及规格", "颜色", "规格",
    "基本售价", "其它属性1", "其它属性2", "其它属性3", "成本价", "采购价", "市场|吊牌价",
    "品牌", "分类", "虚拟分类", "商品标签", "国标码", "供应商名称", "重量", "长", "宽", "高",
    "体积", "单位", "商品状态", "库存同步", "备注", "库容下限", "库容上限", "溢出数量",
    "标准装箱数量", "标准装箱体积", "主仓位", "其它价格1", "其它价格2", "其它价格3",
    "修改时间", "创建时间", "创建人",
]  # fmt: skip
assert len(JST_42) == 42

# 精简导出：必填列 + 商品简称（+ 季节，两份样本都没有，A4）
SLIM = ["款式编码", "商品编码", "商品名称", "颜色及规格", "商品简称", "季节"]


def _xlsx(header: list[str], rows: list[dict[str, Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for row in rows:
        unknown = set(row) - set(header)
        assert not unknown, f"表头里没有这些列：{unknown}"
        ws.append([row.get(h) for h in header])
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
    brand_ids: list[UUID] = field(default_factory=list)
    mapping_ids: list[UUID] = field(default_factory=list)

    # -- 编码 ------------------------------------------------------------------
    def sc(self, tag: str) -> str:
        """款号（= 新建单品商品的编码）。"""
        return f"S{self.suffix}{tag}"

    def kc(self, tag: str) -> str:
        """SKU 编码。"""
        return f"K{self.suffix}{tag}"

    # -- 造数（全部提交，created_at / updated_at 放到一天前） -------------------------
    async def _exec(self, sql: str, **params: Any) -> None:
        async with self.Maker() as s:
            await s.execute(text(sql), params)
            await s.commit()

    async def style(
        self,
        tag: str,
        *,
        name: str = "旧款名",
        url: str | None = None,
        deleted: bool = False,
        category: str | None = None,
    ) -> UUID:
        sid = uuid4()
        await self._exec(
            "INSERT INTO style (id, tenant_id, style_code, style_name, category, "
            "external_image_url, tags, tag_color, design_status, is_active, is_deleted, "
            "created_at, updated_at) VALUES (:id, :tid, :code, :name, :cat, :url, '[]'::jsonb, "
            "'[]'::jsonb, '大货', true, :deleted, NOW() - INTERVAL '1 day', "
            "NOW() - INTERVAL '1 day')",
            id=sid,
            tid=self.tenant_id,
            code=self.sc(tag),
            name=name,
            cat=category,
            url=url,
            deleted=deleted,
        )
        return sid

    async def sku(self, tag: str, style_id: UUID, **values: Any) -> UUID:
        row: dict[str, Any] = {
            "color": "红",
            "size": "M",
            "base_price": Decimal("199.00"),
            "cost_price": Decimal("60.00"),
            "purchase_price": Decimal("55.00"),
            "tag_price": Decimal("299.00"),
            "sourcing_type": "自产",
        }
        row.update(values)
        kid = uuid4()
        await self._exec(
            "INSERT INTO sku (id, tenant_id, style_id, sku_code, color, size, base_price, "
            "cost_price, purchase_price, tag_price, sourcing_type, is_active, is_deleted, "
            "created_at, updated_at) VALUES (:id, :tid, :sid, :code, :color, :size, "
            ":base_price, :cost_price, :purchase_price, :tag_price, :sourcing_type, true, "
            "false, NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')",
            id=kid,
            tid=self.tenant_id,
            sid=style_id,
            code=self.kc(tag),
            **row,
        )
        return kid

    async def brand(self, tag: str, name: str, *, active: bool = True) -> UUID:
        bid = uuid4()
        await self._exec(
            "INSERT INTO brand (id, tenant_id, brand_code, brand_name, is_active, created_at, "
            "updated_at) VALUES (:id, :tid, :code, :name, :active, NOW(), NOW())",
            id=bid,
            tid=self.tenant_id,
            code=f"B{self.suffix}{tag}",
            name=name,
            active=active,
        )
        self.brand_ids.append(bid)
        return bid

    async def goods(
        self,
        code: str,
        members: list[UUID],
        *,
        title: str = "商品全称",
        short_name: str | None = None,
        season: str | None = None,
        brand_id: UUID | None = None,
        is_suit: bool | None = None,
        deleted: bool = False,
        cost: Decimal | None = Decimal("60.00"),
    ) -> UUID:
        gid = uuid4()
        await self._exec(
            "INSERT INTO goods_main (id, tenant_id, goods_code, goods_title, short_name, season, "
            "brand_id, is_suit, is_active, is_deleted, created_at, updated_at) VALUES (:id, "
            ":tid, :code, :title, :short, :season, :brand, :suit, :active, :deleted, "
            "NOW() - INTERVAL '1 day', NOW() - INTERVAL '1 day')",
            id=gid,
            tid=self.tenant_id,
            code=code,
            title=title,
            short=short_name,
            season=season,
            brand=brand_id,
            suit=is_suit if is_suit is not None else len(members) >= 2,
            active=not deleted,
            deleted=deleted,
        )
        for order, style_id in enumerate(members):
            await self._exec(
                "INSERT INTO goods_style_item (id, tenant_id, goods_main_id, style_id, "
                "single_goods_cost, sort_order, is_active, created_at, updated_at) VALUES "
                "(:id, :tid, :gid, :sid, :cost, :ord, true, NOW(), NOW())",
                id=uuid4(),
                tid=self.tenant_id,
                gid=gid,
                sid=style_id,
                cost=cost,
                ord=order,
            )
        return gid

    # -- 导入 --------------------------------------------------------------------
    async def mapping(self, columns: list[tuple[str, str]]) -> int:
        """存一个（不生效的）自定义映射版本，批次按版本号取；版本号取一个不会撞的大数。"""
        version = 900_000 + int(self.suffix[:4], 16)
        mid = uuid4()
        self.mapping_ids.append(mid)
        config = {
            "columns": [{"source_col": s, "target_field": t, "type": "str"} for s, t in columns]
        }
        await self._exec(
            "INSERT INTO field_mapping (id, tenant_id, source, version, mapping_config, "
            "is_active, created_at, updated_at) VALUES (:id, :tid, :src, :v, "
            "CAST(:cfg AS jsonb), false, NOW(), NOW())",
            id=mid,
            tid=self.tenant_id,
            src=SOURCE,
            v=version,
            cfg=json.dumps(config, ensure_ascii=False),
        )
        return version

    async def run(
        self,
        rows: list[dict[str, Any]],
        *,
        header: list[str] | None = None,
        mapping_version: int | None = None,
    ) -> tuple[UUID, dict[str, Any]]:
        batch_id = uuid4()
        self.batch_ids.append(batch_id)
        key = f"imports/{self.tenant_id}/{batch_id}/goods.xlsx"
        self.files[key] = _xlsx(header or JST_42, rows)
        await self._exec(
            "INSERT INTO import_batch (id, tenant_id, source, file_hash, original_filename, "
            "file_r2_key, file_bucket, status, total_rows, imported, failed, retry_count, "
            "mapping_version, created_by, created_at, updated_at) VALUES (:id, :tid, :src, :h, "
            "'goods.xlsx', :k, 'private', 'processing', 0, 0, 0, 0, :mv, :cb, NOW(), NOW())",
            id=batch_id,
            tid=self.tenant_id,
            src=SOURCE,
            h=f"{self.suffix}-{batch_id}",
            k=key,
            mv=mapping_version,
            cb=self.user_id,
        )
        result = await _run_import_batch(batch_id, only_failed=False)
        return batch_id, result

    def row(self, style_tag: str, sku_tag: str, **cells: Any) -> dict[str, Any]:
        """一行 42 列导出：默认给齐必填列，其余按 cells（中文列名）覆盖；值为 None 的列留空。"""
        base: dict[str, Any] = {
            "款式编码": self.sc(style_tag),
            "商品编码": self.kc(sku_tag),
            "商品名称": "新款名",
            "颜色": "红",
            "规格": "M",
        }
        base.update(cells)
        return base

    # -- 查询 --------------------------------------------------------------------
    async def one(self, sql: str, **params: Any) -> Any:
        async with self.Maker() as s:
            return (await s.execute(text(sql), params)).first()

    async def all(self, sql: str, **params: Any) -> list[Any]:
        async with self.Maker() as s:
            return list((await s.execute(text(sql), params)).fetchall())

    async def style_row(self, tag: str) -> Any:
        return await self.one(
            "SELECT id, style_name, category, short_name, season, brand_id, external_image_url, "
            "owner_id, updated_at FROM style WHERE style_code = :c AND is_deleted = false",
            c=self.sc(tag),
        )

    async def sku_row(self, tag: str) -> Any:
        return await self.one(
            "SELECT id, style_id, color, size, base_price, cost_price, purchase_price, "
            "tag_price, sourcing_type, updated_at FROM sku WHERE sku_code = :c "
            "AND is_deleted = false",
            c=self.kc(tag),
        )

    async def goods_row(self, code: str) -> Any:
        return await self.one(
            "SELECT id, goods_code, goods_title, short_name, season, brand_id, is_suit, "
            "is_deleted, updated_at FROM goods_main WHERE goods_code = :c",
            c=code,
        )

    async def goods_items(self, goods_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT style_id, single_goods_cost, is_active FROM goods_style_item "
            "WHERE goods_main_id = :g ORDER BY sort_order",
            g=goods_id,
        )

    async def goods_codes(self) -> list[tuple[Any, str]]:
        rows = await self.all(
            "SELECT id, goods_code FROM goods_main WHERE goods_code LIKE :p ORDER BY id",
            p=f"%{self.suffix}%",
        )
        return [(r[0], r[1]) for r in rows]

    async def jobs(self, batch_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT row_number, status, notes, error_detail, target_resource_id FROM import_job "
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
            "SELECT id, status, batch_id, kind, fields, row_numbers, message, superseded_by, "
            "object_type, object_label FROM import_conflict WHERE object_id = :o "
            "ORDER BY created_at",
            o=object_id,
        )

    async def audits(self, resource: str, object_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT action, actor_type, user_id, before, after FROM audit_log "
            "WHERE resource = :res AND resource_id = :r ORDER BY created_at",
            res=resource,
            r=str(object_id),
        )

    async def cleanup(self) -> None:
        like = f"%{self.suffix}%"
        async with self.Maker() as c:
            style_ids = [
                r[0]
                for r in (
                    await c.execute(
                        text("SELECT id FROM style WHERE style_code LIKE :p"), {"p": like}
                    )
                ).fetchall()
            ]
            sku_ids = [
                r[0]
                for r in (
                    await c.execute(text("SELECT id FROM sku WHERE sku_code LIKE :p"), {"p": like})
                ).fetchall()
            ]
            goods_ids = [
                r[0]
                for r in (
                    await c.execute(
                        text(
                            "SELECT DISTINCT g.id FROM goods_main g LEFT JOIN goods_style_item gi "
                            "ON gi.goods_main_id = g.id WHERE g.goods_code LIKE :p "
                            "OR gi.style_id = ANY(:s)"
                        ),
                        {"p": like, "s": style_ids},
                    )
                ).fetchall()
            ]
            object_ids = [*style_ids, *sku_ids, *goods_ids]
            if object_ids:
                await c.execute(
                    text("DELETE FROM import_conflict WHERE object_id = ANY(:ids)"),
                    {"ids": object_ids},
                )
                await c.execute(
                    text("DELETE FROM audit_log WHERE resource_id = ANY(:r)"),
                    {"r": [str(i) for i in object_ids]},
                )
            for batch_id in self.batch_ids:
                await c.execute(
                    text("DELETE FROM import_conflict WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
                )
                await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
            if goods_ids:
                await c.execute(
                    text("DELETE FROM goods_style_item WHERE goods_main_id = ANY(:g)"),
                    {"g": goods_ids},
                )
                await c.execute(text("DELETE FROM goods_main WHERE id = ANY(:g)"), {"g": goods_ids})
            await c.execute(text("DELETE FROM sku WHERE sku_code LIKE :p"), {"p": like})
            await c.execute(text("DELETE FROM style WHERE style_code LIKE :p"), {"p": like})
            if self.brand_ids:
                await c.execute(text("DELETE FROM brand WHERE id = ANY(:b)"), {"b": self.brand_ids})
            if self.mapping_ids:
                await c.execute(
                    text("DELETE FROM field_mapping WHERE id = ANY(:m)"), {"m": self.mapping_ids}
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
        await e.cleanup()


def _warnings(job: Any) -> list[str]:
    return list((job.notes or {}).get("warnings") or [])


def _rule(policy: DuplicatePolicy) -> DuplicateRule:
    return DuplicateRule(policy, key="款号；SKU 编码；商品见 §5.3", configurable=True)


@pytest.mark.integration
@pytest.mark.asyncio
class TestNewObjects:
    async def test_full_export_creates_style_sku_and_single_goods(self, env: _Env) -> None:
        """AC 16 / 22 / 23 / 38：42 列真实表头（千分位、- 占位、空格子、多余列、图片列为空）。

        新款式类目为空（文件有「分类」也不写）、款式层简称 / 季节 / 品牌都不写；同款两行只建一个
        单品商品（编码 = 款号、全称 = 商品名称、成员 = 该款），留 goods.create 审计。
        """
        brand_id = await env.brand("L", f"LENNEALAB{env.suffix}")
        common = {
            "商品名称": "法式碎花连衣裙",
            "商品简称": "碎花裙",
            "品牌": f"LENNEALAB{env.suffix}",
            "分类": "连衣裙",
            "虚拟分类": "夏季",
            "基本售价": "1,288.00",
            "成本价": "-",
            "采购价": "",
            "市场|吊牌价": "—",
            "图片": None,
            "备注": "多余列",
            "修改时间": "2026-10-01 10:00:00",
            "创建人": "某人",
        }
        _, result = await env.run(
            [
                env.row("A", "A1", 颜色及规格="红;M", 颜色="红", 规格="M", **common),
                env.row("A", "A2", 颜色及规格="红;L", 颜色="红", 规格="L", **common),
            ]
        )
        assert result["status"] == "completed"
        style = await env.style_row("A")
        assert style.style_name == "法式碎花连衣裙"
        assert (style.category, style.short_name, style.season, style.brand_id) == (
            None,
            None,
            None,
            None,
        )
        assert style.external_image_url is None
        assert style.owner_id == env.user_id
        sku = await env.sku_row("A1")
        assert (sku.base_price, sku.cost_price, sku.purchase_price, sku.tag_price) == (
            Decimal("1288.00"),
            None,
            None,
            None,
        )
        assert sku.sourcing_type == "自产"

        goods = await env.goods_row(env.sc("A"))
        assert goods is not None
        assert (goods.goods_title, goods.short_name, goods.brand_id, goods.is_suit) == (
            "法式碎花连衣裙",
            "碎花裙",
            brand_id,
            False,
        )
        items = await env.goods_items(goods.id)
        assert [i.style_id for i in items] == [style.id]
        assert [c for _, c in await env.goods_codes()] == [env.sc("A")]

        [audit] = [
            a for a in await env.audits("goods_main", goods.id) if a.action == "goods.create"
        ]
        assert (audit.actor_type, audit.user_id) == ("worker", env.user_id)
        assert audit.after["via"] == "import"
        assert audit.after["brand_id"] == str(brand_id)
        assert audit.after["brand_name"] == f"LENNEALAB{env.suffix}"
        assert audit.after["row_number"] == 1

    async def test_slim_export_with_season_and_color_size(self, env: _Env) -> None:
        """AC 38 精简导出（必填 + 商品简称 + 季节）；颜色 / 规格由「颜色及规格」拆分。"""
        _, result = await env.run(
            [
                {
                    "款式编码": env.sc("B"),
                    "商品编码": env.kc("B1"),
                    "商品名称": "针织开衫",
                    "颜色及规格": "黑色;XL",
                    "商品简称": "开衫",
                    "季节": "秋",
                }
            ],
            header=SLIM,
        )
        assert result["status"] == "completed"
        sku = await env.sku_row("B1")
        assert (sku.color, sku.size) == ("黑色", "XL")
        goods = await env.goods_row(env.sc("B"))
        assert (goods.short_name, goods.season) == ("开衫", "秋")
        assert (await env.style_row("B")).season is None

    async def test_custom_mapping_old_target_names_write_goods_layer(self, env: _Env) -> None:
        """AC 40 附：旧目标名 brand_code / season 的自定义映射照常生效、写到商品层；
        旧映射里的 category 被忽略（FR-3.2）。"""
        brand_id = await env.brand("CM", f"牌{env.suffix}")
        version = await env.mapping(
            [
                ("款号", "style_code"),
                ("条码", "sku_code"),
                ("名称", "style_name"),
                ("颜色规格", "color_size"),
                ("牌子", "brand_code"),
                ("季", "season"),
                ("类目", "category"),
            ]
        )
        header = ["款号", "条码", "名称", "颜色规格", "牌子", "季", "类目"]
        _, result = await env.run(
            [
                {
                    "款号": env.sc("CM"),
                    "条码": env.kc("CM1"),
                    "名称": "某款",
                    "颜色规格": "白;S",
                    "牌子": f"牌{env.suffix}",
                    "季": "冬",
                    "类目": "外套",
                }
            ],
            header=header,
            mapping_version=version,
        )
        assert result["status"] == "completed"
        style = await env.style_row("CM")
        assert (style.category, style.season, style.brand_id) == (None, None, None)
        goods = await env.goods_row(env.sc("CM"))
        assert (goods.brand_id, goods.season) == (brand_id, "冬")

    async def test_new_sku_cost_rolls_into_single_goods_cost(self, env: _Env) -> None:
        """新建单品商品的单件成本 = 该款启用 SKU 的最高成本价（与商品页同规则）。"""
        await env.run([env.row("C", "C1", 成本价="60"), env.row("C", "C2", 规格="L", 成本价="65")])
        goods = await env.goods_row(env.sc("C"))
        [item] = await env.goods_items(goods.id)
        assert item.single_goods_cost == Decimal("60.00")  # 建商品时只有第 1 行的 SKU


@pytest.mark.integration
@pytest.mark.asyncio
class TestGoodsTarget:
    async def test_ac24_fill_then_conflict_and_other_single_untouched(self, env: _Env) -> None:
        """AC 24 改写版：简称为空 → 补空；有值且不同 → 冲突不改；同款 <货号>-<千牛ID> 单品不被比较。"""
        sid = await env.style("D")
        await env.sku("D1", sid)
        main = await env.goods(env.sc("D"), [sid], title="主商品")
        other = await env.goods(f"{env.sc('D')}-777", [sid], title="千牛单品", short_name="千牛简")
        b1, r1 = await env.run([env.row("D", "D1", 商品简称="新简")])
        assert r1["status"] == "completed"
        [job] = await env.jobs(b1)
        assert job.status == "filled"
        assert job.notes["filled"] == [
            {"object_type": "goods", "object_label": "主商品", "fields": ["short_name"]}
        ]
        assert _warnings(job) == ["另有单品商品 千牛简 未写入（只写编码与款号相同的那个）"]
        assert all(env.suffix not in w for w in _warnings(job))  # 不出现商品编码
        assert (await env.goods_row(env.sc("D"))).short_name == "新简"
        assert (await env.goods_row(f"{env.sc('D')}-777")).short_name == "千牛简"
        b = await env.batch(b1)
        assert (b.filled, b.filled_objects) == (1, 1)
        [audit] = await env.audits("goods_main", main)
        assert audit.action == "goods.update"
        assert (audit.actor_type, audit.user_id) == ("worker", env.user_id)
        assert audit.before == {"short_name": None}
        assert audit.after["short_name"] == "新简"
        assert audit.after["via"] == "import_fill"
        assert audit.after["import_batch_id"] == str(b1)
        assert audit.after["row_number"] == 1

        b2, _ = await env.run([env.row("D", "D1", 商品简称="另一个简称")])
        [job2] = await env.jobs(b2)
        assert job2.status == "conflict"
        assert (await env.goods_row(env.sc("D"))).short_name == "新简"
        [c] = await env.conflicts(main)
        assert (c.status, c.kind, c.object_type) == ("pending", "fields", "goods")
        assert [(f["field"], f["system"], f["file"]) for f in c.fields] == [
            ("short_name", "新简", "另一个简称")
        ]
        assert await env.conflicts(other) == []

    @pytest.mark.parametrize("occupied", ["deleted", "suit", "members"])
    async def test_ac25_code_occupied_key_conflict(self, env: _Env, occupied: str) -> None:
        """AC 25：本款不属于任何商品、编码 = 款号被不兼容的商品占用 → 不建不改，键冲突。"""
        sid = await env.style("E")
        await env.sku("E1", sid)
        other_a = await env.style("EX")
        other_b = await env.style("EY")
        if occupied == "deleted":
            code_goods = await env.goods(env.sc("E"), [sid], title="已删商品", deleted=True)
        elif occupied == "suit":
            code_goods = await env.goods(env.sc("E"), [other_a, other_b], title="某套装")
        else:
            code_goods = await env.goods(env.sc("E"), [other_a], title="别款单品")
        before = await env.goods_codes()
        snapshot = await env.goods_row(env.sc("E"))
        batch_id, result = await env.run([env.row("E", "E1", 商品简称="简")])
        assert result["status"] == "completed"
        assert await env.goods_codes() == before  # 不新建、编码都不变（AC 28）
        after = await env.goods_row(env.sc("E"))
        assert (after.short_name, after.is_deleted, after.updated_at) == (
            snapshot.short_name,
            snapshot.is_deleted,
            snapshot.updated_at,
        )
        [c] = await env.conflicts(code_goods)
        assert (c.status, c.kind, c.fields) == ("pending", "key", [])
        assert "占用" in c.message and "未建单品商品" in c.message
        [job] = await env.jobs(batch_id)
        assert job.status == "conflict"

    async def test_n12_deleted_code_goods_writes_x2(self, env: _Env) -> None:
        """N12：软删的 X + 在用单品 X-2（成员 X）→ 写 X-2，没有键冲突，有提示。"""
        sid = await env.style("F")
        await env.sku("F1", sid)
        deleted = await env.goods(env.sc("F"), [sid], title="已删", deleted=True)
        x2 = await env.goods(f"{env.sc('F')}-2", [sid], title="在用单品")
        batch_id, _ = await env.run([env.row("F", "F1", 商品简称="新简")])
        [job] = await env.jobs(batch_id)
        assert job.status == "filled"
        assert _warnings(job) == ["款号对应的编码被已删除的商品占用，已写入「在用单品」"]
        assert (await env.goods_row(f"{env.sc('F')}-2")).short_name == "新简"
        assert await env.conflicts(deleted) == []
        assert await env.conflicts(x2) == []

    async def test_ac26_suit_untouched_and_suit_only_style_not_created(self, env: _Env) -> None:
        """AC 26：套装字段 / 成员 / 成本都不变；只在套装里的款不建单品商品（有提示）。"""
        sa = await env.style("G")
        sb = await env.style("H")
        await env.sku("G1", sa)
        await env.sku("H1", sb)
        suit = await env.goods(f"SUIT-{env.suffix}", [sa, sb], title="套装", cost=Decimal("30.00"))
        before_suit = await env.goods_row(f"SUIT-{env.suffix}")
        before_items = await env.goods_items(suit)
        before_codes = await env.goods_codes()
        batch_id, result = await env.run(
            [
                env.row("G", "G1", 商品简称="想改套装", 成本价="99"),
                env.row("H", "H1", 商品简称="想改套装"),
            ]
        )
        assert result["status"] == "completed"
        after_suit = await env.goods_row(f"SUIT-{env.suffix}")
        assert tuple(after_suit) == tuple(before_suit)
        assert await env.goods_items(suit) == before_items
        assert await env.goods_codes() == before_codes
        for job in await env.jobs(batch_id):
            assert job.status != "failed"
            assert "该款只在套装里，未建单品商品" in _warnings(job)
        assert await env.conflicts(suit) == []

    async def test_ac27_brand_not_in_dict(self, env: _Env) -> None:
        """AC 27：品牌在字典里找不到（或已停用）→ 不写品牌、行不失败、有提示。"""
        await env.brand("Z", f"停用{env.suffix}", active=False)
        batch_id, result = await env.run(
            [
                env.row("I", "I1", 品牌="不存在的品牌"),
                env.row("J", "J1", 品牌=f"停用{env.suffix}"),
            ]
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert _warnings(jobs[0]) == ["品牌「不存在的品牌」不在品牌字典里，未写入"]
        assert _warnings(jobs[1]) == [f"品牌「停用{env.suffix}」不在品牌字典里，未写入"]
        assert (await env.goods_row(env.sc("I"))).brand_id is None
        assert (await env.goods_row(env.sc("J"))).brand_id is None

    async def test_brand_code_preferred_over_name(self, env: _Env) -> None:
        """品牌 brand_code 与 brand_name 命中不同品牌时取 brand_code 命中的。"""
        by_code = await env.brand("M", "某名称")
        await env.brand("N", f"B{env.suffix}M")  # 名称恰好等于另一个品牌的编码
        await env.run([env.row("K", "K1", 品牌=f"B{env.suffix}M")])
        assert (await env.goods_row(env.sc("K"))).brand_id == by_code


def _full_row(env: _Env, style_tag: str, sku_tag: str, **cells: Any) -> dict[str, Any]:
    """与 _Env.sku / goods 默认种子全等的一行（60 与 60.00 算相同）。"""
    base = {
        "基本售价": "199",
        "成本价": "60",
        "采购价": "55.0",
        "市场|吊牌价": "299.00",
    }
    base.update(cells)
    return env.row(style_tag, sku_tag, **base)


@pytest.mark.integration
@pytest.mark.asyncio
class TestCompare:
    async def test_ac41_identical_is_skipped(self, env: _Env) -> None:
        """AC 41 / 50：映射字段全等再导 → 重复已跳过、updated_at 不变，全是跳过的批次 completed。"""
        brand_id = await env.brand("Q", f"品牌{env.suffix}")
        sid = await env.style("P", url="https://img.example.invalid/p.jpg")
        await env.sku("P1", sid)
        await env.goods(env.sc("P"), [sid], short_name="简", season="春", brand_id=brand_id)
        before_sku = await env.sku_row("P1")
        before_style = await env.style_row("P")
        batch_id, result = await env.run(
            [
                _full_row(
                    env,
                    "P",
                    "P1",
                    图片=" https://img.example.invalid/p.jpg ",
                    商品简称="简",
                    品牌=f"品牌{env.suffix}",
                )
            ]
        )
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert (job.status, job.notes) == ("skipped", None)
        assert (await env.sku_row("P1")).updated_at == before_sku.updated_at
        assert (await env.style_row("P")).updated_at == before_style.updated_at
        b = await env.batch(batch_id)
        assert (b.status, b.imported, b.skipped, b.conflicted, b.failed) == (
            "completed",
            0,
            1,
            0,
            0,
        )

    async def test_ac42_cost_price_conflict(self, env: _Env) -> None:
        """AC 42 改写版：成本价 60 / 65 → 冲突、不改；60 与 60.00 全等；文件成本价为空不比较。"""
        sid = await env.style("R")
        kid = await env.sku("R1", sid)
        before = await env.sku_row("R1")
        b1, _ = await env.run([_full_row(env, "R", "R1", 成本价="65")])
        [job] = await env.jobs(b1)
        assert job.status == "conflict"
        after = await env.sku_row("R1")
        assert (after.cost_price, after.updated_at) == (Decimal("60.00"), before.updated_at)
        [c] = await env.conflicts(kid)
        assert c.status == "pending"
        assert [(f["field"], f["system"], f["file"], f["sensitive"]) for f in c.fields] == [
            ("cost_price", "60.00", "65.00", ["sku", "cost_price"])
        ]
        # 成本价格子为空：不参与比较，旧冲突不被取代（N5a 同口径）
        b2, _ = await env.run([_full_row(env, "R", "R1", 成本价=None)])
        [job2] = await env.jobs(b2)
        assert job2.status == "skipped"
        assert [r.status for r in await env.conflicts(kid)] == ["pending"]

    async def test_ac43_six_rows_same_url_one_conflict(self, env: _Env) -> None:
        """AC 43 再改版：同批 6 行同款、同一个图片链接且与库里不同 → 只一条冲突，row_numbers 6 个。"""
        sid = await env.style("T", url="https://img.example.invalid/old.jpg")
        rows = [
            env.row("T", f"T{i}", 规格=f"S{i}", 图片="https://img.example.invalid/new.jpg")
            for i in range(6)
        ]
        _, result = await env.run(rows)
        assert result["status"] == "completed"
        [c] = await env.conflicts(sid)
        assert c.row_numbers == [1, 2, 3, 4, 5, 6]
        assert [(f["field"], f["file"]) for f in c.fields] == [
            ("external_image_url", "https://img.example.invalid/new.jpg")
        ]
        assert (
            await env.style_row("T")
        ).external_image_url == "https://img.example.invalid/old.jpg"

    async def test_n5_partial_columns_keep_or_carry_old_conflicts(self, env: _Env) -> None:
        """N5：a 不含成本价列的批次不取代成本价冲突；b 新冲突并入旧字段带来源批次；
        c 图片列为空不取代外部链接冲突。"""
        sid = await env.style("U", url="https://img.example.invalid/u0.jpg")
        kid = await env.sku("U1", sid)
        b1, _ = await env.run(
            [_full_row(env, "U", "U1", 成本价="65", 图片="https://img.example.invalid/u1.jpg")]
        )
        slim = ["款式编码", "商品编码", "商品名称", "颜色", "规格"]
        # a）不含成本价列、其余一致 → 冲突仍待处理
        await env.run(
            [
                {
                    "款式编码": env.sc("U"),
                    "商品编码": env.kc("U1"),
                    "商品名称": "x",
                    "颜色": "红",
                    "规格": "M",
                }
            ],
            header=slim,
        )
        assert [r.status for r in await env.conflicts(kid)] == ["pending"]
        # c）42 列、图片为空（成本价也空）→ 款式冲突与成本价冲突都仍待处理
        await env.run([_full_row(env, "U", "U1", 成本价=None)])
        assert [r.status for r in await env.conflicts(kid)] == ["pending"]
        assert [r.status for r in await env.conflicts(sid)] == ["pending"]
        # b）不含成本价列、颜色不同 → 新冲突 = 颜色 + 成本价（来源批次 = 批次 1）
        b3, _ = await env.run(
            [
                {
                    "款式编码": env.sc("U"),
                    "商品编码": env.kc("U1"),
                    "商品名称": "x",
                    "颜色": "蓝",
                    "规格": "M",
                }
            ],
            header=slim,
        )
        old, new = await env.conflicts(kid)
        assert (old.status, old.superseded_by, new.status, new.batch_id) == (
            "superseded",
            new.id,
            "pending",
            b3,
        )
        fields = {f["field"]: f for f in new.fields}
        assert set(fields) == {"color", "cost_price"}
        assert fields["color"]["file"] == "蓝"
        assert fields["cost_price"]["from_batch_id"] == str(b1)
        assert (await env.sku_row("U1")).color == "红"

    async def test_n1_fill_counts_and_audit(self, env: _Env) -> None:
        """N1：补空计数（filled / filled_objects）与审计 via import_fill；N8 后半：自产、没有成本价的
        SKU 补空基本售价成功（不调 validate_sku_sourcing_price）。"""
        sid = await env.style("V")
        kid = await env.sku("V1", sid, cost_price=None, base_price=None)
        await env.goods(env.sc("V"), [sid])  # 已有单品：本行不新建商品
        batch_id, result = await env.run(
            [env.row("V", "V1", 图片="https://img.example.invalid/v.jpg", 基本售价="99")]
        )
        assert result["status"] == "completed"
        [job] = await env.jobs(batch_id)
        assert job.status == "filled"
        assert sorted(f["object_type"] for f in job.notes["filled"]) == ["sku", "style"]
        b = await env.batch(batch_id)
        assert (b.filled, b.filled_objects, b.imported) == (1, 2, 0)
        assert (await env.style_row("V")).external_image_url == "https://img.example.invalid/v.jpg"
        assert (await env.sku_row("V1")).base_price == Decimal("99.00")
        [sa] = await env.audits("style", sid)
        assert (sa.action, sa.actor_type, sa.user_id) == ("style.update", "worker", env.user_id)
        assert sa.before == {"external_image_url": None}
        assert sa.after["external_image_url"] == "https://img.example.invalid/v.jpg"
        assert (sa.after["via"], sa.after["import_batch_id"], sa.after["row_number"]) == (
            "import_fill",
            str(batch_id),
            1,
        )
        [ka] = await env.audits("sku", kid)
        assert ka.after["base_price"] == "99.00"

    async def test_n2_style_name_only_on_create(self, env: _Env) -> None:
        """N2：「商品名称」与已有款名不同 → 不比较、不覆盖、不进冲突。"""
        sid = await env.style("W", name="旧款名")
        await env.sku("W1", sid)
        await env.goods(env.sc("W"), [sid], title="旧全称")
        batch_id, _ = await env.run([env.row("W", "W1", 商品名称="完全不同的新名")])
        [job] = await env.jobs(batch_id)
        assert job.status == "skipped"
        assert (await env.style_row("W")).style_name == "旧款名"
        assert await env.conflicts(sid) == []


@pytest.mark.integration
@pytest.mark.asyncio
class TestSameBatch:
    async def test_n15a_b_existing_style_two_urls(self, env: _Env) -> None:
        """N15 a / b：同款两行图片 A / B → 补空 A、第 2 行提示；再导内容相同的文件 → 无冲突。"""
        sid = await env.style("X")
        rows = [
            env.row("X", "X1", 图片="https://img.example.invalid/a.jpg"),
            env.row("X", "X2", 规格="L", 图片="https://img.example.invalid/b.jpg"),
        ]
        b1, _ = await env.run(rows)
        jobs = await env.jobs(b1)
        assert _warnings(jobs[1]) == ["第 2 行的图片与第 1 行不一致，按第 1 行处理"]
        assert (await env.style_row("X")).external_image_url == "https://img.example.invalid/a.jpg"
        assert await env.conflicts(sid) == []
        rows[1]["备注"] = "无关列不同"
        b2, _ = await env.run(rows)
        jobs2 = await env.jobs(b2)
        assert _warnings(jobs2[1]) == ["第 2 行的图片与第 1 行不一致，按第 1 行处理"]
        assert await env.conflicts(sid) == []

    async def test_n15c_d_new_style_and_new_goods_first_row_wins(self, env: _Env) -> None:
        """N15 c / d：新建款式两行图片 A / B → A；新建单品两行简称 S1 / S2 → S1；都没有冲突。"""
        _, result = await env.run(
            [
                env.row("Y", "Y1", 图片="https://img.example.invalid/a.jpg", 商品简称="S1"),
                env.row(
                    "Y", "Y2", 规格="L", 图片="https://img.example.invalid/b.jpg", 商品简称="S2"
                ),
            ]
        )
        assert result["status"] == "completed"
        style = await env.style_row("Y")
        assert style.external_image_url == "https://img.example.invalid/a.jpg"
        goods = await env.goods_row(env.sc("Y"))
        assert goods.short_name == "S1"
        assert await env.conflicts(style.id) == []
        assert await env.conflicts(goods.id) == []

    async def test_n15e_registered_row_failed(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N15 e：第 1 行登记后写入抛 DataError → 第 2 行的 B 正常补空、没有「按第 1 行处理」。"""
        await env.style("Z")
        real: Callable[..., Any] = tasks._upsert_job

        async def flaky(session: Any, **kw: Any) -> None:
            if kw["row_number"] == 1 and kw["status"] != "failed":
                raise DataError("INSERT INTO sku ...", {}, Exception("value too long"))
            await real(session, **kw)

        monkeypatch.setattr(tasks, "_upsert_job", flaky)
        batch_id, result = await env.run(
            [
                env.row("Z", "Z1", 图片="https://img.example.invalid/a.jpg"),
                env.row("Z", "Z2", 规格="L", 图片="https://img.example.invalid/b.jpg"),
            ]
        )
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert jobs[0].status == "failed"
        assert jobs[1].status == "success"
        assert _warnings(jobs[1]) == []
        assert (await env.style_row("Z")).external_image_url == "https://img.example.invalid/b.jpg"


@pytest.mark.integration
@pytest.mark.asyncio
class TestRuleSwitch:
    async def test_ac53_overwrite(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC 53：声明换成 OVERWRITE → 覆盖（via import_overwrite）；SKU 属于别的款式仍记键冲突、
        SKU 不改；「商品名称」不写已有款名。"""
        monkeypatch.setitem(DUPLICATE_RULES, SOURCE, _rule(DuplicatePolicy.OVERWRITE))
        sid = await env.style("AA", name="旧款名", url="https://img.example.invalid/0.jpg")
        kid = await env.sku("AA1", sid)
        other = await env.style("AB")
        foreign = await env.sku("AB1", other)
        batch_id, result = await env.run(
            [
                _full_row(env, "AA", "AA1", 成本价="65", 图片="https://img.example.invalid/1.jpg"),
                env.row("AA", "AB1"),
            ]
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        assert jobs[0].status == "success"
        assert jobs[1].status == "conflict"
        style = await env.style_row("AA")
        assert (style.style_name, style.external_image_url) == (
            "旧款名",
            "https://img.example.invalid/1.jpg",
        )
        assert (await env.sku_row("AA1")).cost_price == Decimal("65.00")
        [sa] = await env.audits("style", sid)
        assert sa.after["via"] == "import_overwrite"
        [ka] = await env.audits("sku", kid)
        assert ka.after["cost_price_changed"] is True
        assert "cost_price" not in ka.before and "cost_price" not in ka.after
        assert (await env.sku_row("AB1")).style_id == other
        [c] = await env.conflicts(foreign)
        assert (c.kind, c.status) == ("key", "pending")

    async def test_ac53_keep(self, env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
        """KEEP：已有对象不比较、不补空；SKU 属于别的款式仍记键冲突（J55）。"""
        monkeypatch.setitem(DUPLICATE_RULES, SOURCE, _rule(DuplicatePolicy.KEEP))
        sid = await env.style("AC", url=None)
        kid = await env.sku("AC1", sid)
        await env.goods(env.sc("AC"), [sid])
        other = await env.style("AD")
        foreign = await env.sku("AD1", other)
        batch_id, _ = await env.run(
            [
                _full_row(env, "AC", "AC1", 成本价="65", 图片="https://img.example.invalid/1.jpg"),
                env.row("AC", "AD1"),
            ]
        )
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["skipped", "conflict"]
        assert (await env.sku_row("AC1")).cost_price == Decimal("60.00")
        assert (await env.style_row("AC")).external_image_url is None
        assert await env.conflicts(kid) == []
        [c] = await env.conflicts(foreign)
        assert c.kind == "key"
        assert (await env.sku_row("AD1")).style_id == other


@pytest.mark.integration
@pytest.mark.asyncio
class TestStepZero:
    async def test_n16_sku_owned_by_other_style(self, env: _Env) -> None:
        """N16：款式要新建而 SKU 已属于别的款式 → 不建款式与单品商品，SKU 上一条键冲突。"""
        sid = await env.style("N1")
        kid = await env.sku("NK", sid)
        batch_id, result = await env.run([env.row("N9", "NK", 商品简称="简")])
        assert result["status"] == "completed"
        assert await env.style_row("N9") is None
        assert await env.goods_row(env.sc("N9")) is None
        assert await env.goods_codes() == []
        [job] = await env.jobs(batch_id)
        assert job.status == "conflict"
        assert job.target_resource_id == kid
        assert _warnings(job) == [f"SKU 编码已属于款式 {env.sc('N1')}，本行未建款式与单品商品"]
        [c] = await env.conflicts(kid)
        assert (c.kind, c.object_type) == ("key", "sku")
        assert env.sc("N1") in c.message and env.sc("N9") in c.message
        assert (await env.sku_row("NK")).style_id == sid

    async def test_n16b_owner_style_deleted_same_code(self, env: _Env) -> None:
        """NIT 2：SKU 所属款式已删除、且款号与文件里的相同（不区分大小写）→ 提示先恢复款式。"""
        sid = await env.style("O1", deleted=True)
        kid = await env.sku("OK", sid)
        batch_id, _ = await env.run([env.row("O1", "OK") | {"款式编码": env.sc("O1").lower()}])
        expected = (
            f"款式 {env.sc('O1')} 已删除，但其下仍有 SKU {env.kc('OK')}；"
            f"如需继续使用，请先恢复款式 {env.sc('O1')}（联系管理员）"
        )
        [job] = await env.jobs(batch_id)
        assert job.status == "conflict"
        assert _warnings(job) == [expected]
        [c] = await env.conflicts(kid)
        assert c.message == expected
        assert await env.goods_codes() == []
        styles = await env.all(
            "SELECT is_deleted FROM style WHERE lower(style_code) = lower(:c)", c=env.sc("O1")
        )
        assert [s[0] for s in styles] == [True]


@pytest.mark.integration
@pytest.mark.asyncio
class TestOptionalAndValidation:
    async def test_optional_items_dropped_with_warnings(self, env: _Env) -> None:
        """§5.4：简称 > 32、季节 > 64、图片 javascript: / ftp:// / 超 1024 → 丢弃并提示，行不失败。"""
        header = [*JST_42, "季节"]
        batch_id, result = await env.run(
            [
                env.row("Q1", "Q1", 商品简称="长" * 33, 季节="季" * 65, 图片="javascript:alert(1)"),
                env.row("Q2", "Q2", 图片="ftp://img.example.invalid/a.jpg"),
                env.row("Q3", "Q3", 图片="https://img.example.invalid/" + "a" * 1100),
            ],
            header=header,
        )
        assert result["status"] == "completed"
        jobs = await env.jobs(batch_id)
        image_warning = "图片链接不是 http/https 地址或超过 1024 字符，未保存"
        assert _warnings(jobs[0]) == [
            "商品简称超过 32 字，未写入",
            "季节超过 64 字，未写入",
            image_warning,
        ]
        assert _warnings(jobs[1]) == [image_warning]
        assert _warnings(jobs[2]) == [image_warning]
        goods = await env.goods_row(env.sc("Q1"))
        assert (goods.short_name, goods.season) == (None, None)
        for tag in ("Q1", "Q2", "Q3"):
            assert (await env.style_row(tag)).external_image_url is None

    async def test_price_over_limit_or_malformed_fails_row(self, env: _Env) -> None:
        """价格 ≥ 1 亿或格式错 → 整行失败，文案不含值；占位符不是格式错。"""
        batch_id, result = await env.run(
            [
                env.row("PA", "PA1", 成本价="100000000"),
                env.row("PB", "PB1", 基本售价="abc"),
                env.row("PC", "PC1", 成本价="--", 基本售价="1,288.00"),
            ]
        )
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["failed", "failed", "success"]
        assert jobs[0].error_detail.endswith("成本价必须为非负数字且小于 1 亿")
        assert "100000000" not in jobs[0].error_detail
        assert jobs[1].error_detail.endswith("基本售价必须为非负数字且小于 1 亿")
        assert await env.style_row("PA") is None
