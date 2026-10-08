"""8a 补充：导入聚水潭商品资料时读 WPS 单元格内嵌图，给还没有主图的款式补主图。

走真实 runner（``_run_import_batch``）：提交种子数据 → monkeypatch ``get_object_bytes`` 返回现造的
xlsx → 断言 → 清理。文件照业务方的 15 列 WPS 导出造：openpyxl 写表头与数据，「图片」格换成
``_xlfn.DISPIMG`` 公式带缓存值，再用 zipfile 加 ``xl/cellimages.xml``、rels、``xl/media/*``、
Content_Types 与 workbook rels（结构照真实样例，图片 = 魔数 + 随机字节，不用样例里任何值）。
R2 一律用假 client，不向任何真实存储发请求。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.exc import DataError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters import style_sku_images
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.embedded_images import IMAGE_NOT_FOUND_REASON, MISSING_REASON
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.product.style_image_service import BATCH_REJECT_REASONS, StyleMainImageStore
from app.tasks.import_tasks import _run_import_batch

SOURCE = "manual_style_sku"

# 业务方实际用的 15 列导出（WPS 生成）
JST_15 = [
    "图片", "款式编码", "商品编码", "商品名称", "商品简称", "颜色及规格", "颜色", "规格",
    "成本价", "采购价", "基本售价", "市场|吊牌价", "国标码", "品牌", "季节",
]  # fmt: skip

PNG = b"\x89PNG\r\n\x1a\n"
JPG = b"\xff\xd8\xff\xe0"


def png(size: int = 64) -> bytes:
    return PNG + os.urandom(size)


def jpg(size: int = 64) -> bytes:
    return JPG + os.urandom(size)


def webp(size: int = 64) -> bytes:
    return b"RIFF\x00\x00\x00\x00WEBP" + os.urandom(size)


@dataclass(frozen=True)
class Pic:
    """「图片」格是 WPS 内嵌图（DISPIMG 公式 + 缓存值）。"""

    image_id: str


# ---------------------------------------------------------------------------
# 造 WPS 导出
# ---------------------------------------------------------------------------

_SHEET_XML = "xl/worksheets/sheet1.xml"
_WPS_IMAGE_CELL = (
    '<c r="{ref}" t="str"><f>_xlfn.DISPIMG(&quot;{id}&quot;,1)</f>'
    "<v>=DISPIMG(&quot;{id}&quot;,1)</v></c>"
)
_CELLIMAGES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<etc:cellImages xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    ' xmlns:etc="http://www.wps.cn/officeDocument/2017/etCustomData">'
)
_CELL_IMAGE = (
    '<etc:cellImage><xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{n}" name="{name}" descr="Picture"/>'
    '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
    '<xdr:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
    '<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="923925" cy="1397000"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr></xdr:pic></etc:cellImage>'
)
_RELS_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
)
_REL = (
    '<Relationship Id="{rid}" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
    'Target="{target}"{mode}/>'
)
_CELLIMAGE_OVERRIDE = (
    '<Override PartName="/xl/cellimages.xml" '
    'ContentType="application/vnd.wps-officedocument.cellimage+xml"/>'
)
_WORKBOOK_CELLIMAGE_REL = (
    '<Relationship Id="rId99" Type="http://www.wps.cn/officeDocument/2020/cellImage" '
    'Target="cellimages.xml"/>'
)
_MEDIA_DEFAULTS = (
    '<Default Extension="png" ContentType="image/png"/>'
    '<Default Extension="jpeg" ContentType="image/jpeg"/>'
    '<Default Extension="webp" ContentType="image/webp"/>'
)


def wps_xlsx(
    rows: list[dict[str, Any]],
    images: dict[str, tuple[str, bytes]],
    *,
    external: dict[str, str] | None = None,
    with_cellimages: bool = True,
) -> bytes:
    """rows 的「图片」为 ``Pic`` 的格写成 DISPIMG；images：图片 ID →（xl/media 下的文件名, 字节）；
    external：图片 ID → 外部链接（rels 里 TargetMode="External"）。"""
    wb = Workbook()
    ws = wb.active
    ws.append(JST_15)
    ids: list[str] = []
    for row in rows:
        unknown = set(row) - set(JST_15)
        assert not unknown, f"表头里没有这些列：{unknown}"
        cells = []
        for h in JST_15:
            value = row.get(h)
            if isinstance(value, Pic):
                value = f"__IMG{len(ids)}__"
                ids.append(row[h].image_id)
            cells.append(value)
        ws.append(cells)
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    sheet, count = re.subn(
        r'<c r="([A-Z]+[0-9]+)" t="inlineStr"><is><t>__IMG([0-9]+)__</t></is></c>',
        lambda m: _WPS_IMAGE_CELL.format(ref=m.group(1), id=ids[int(m.group(2))]),
        src.read(_SHEET_XML).decode(),
    )
    assert count == len(ids), "占位没换成（openpyxl 输出变了）"

    cell_entries: list[tuple[str, str]] = []
    rels: list[str] = []
    for n, (image_id, (name, _data)) in enumerate(images.items(), start=1):
        cell_entries.append((image_id, f"rId{n}"))
        rels.append(_REL.format(rid=f"rId{n}", target=f"media/{name}", mode=""))
    for n, (image_id, url) in enumerate((external or {}).items(), start=len(images) + 1):
        cell_entries.append((image_id, f"rId{n}"))
        rels.append(_REL.format(rid=f"rId{n}", target=url, mode=' TargetMode="External"'))
    cellimages = (
        _CELLIMAGES_HEAD
        + "".join(
            _CELL_IMAGE.format(n=i + 2, name=name, rid=rid)
            for i, (name, rid) in enumerate(cell_entries)
        )
        + "</etc:cellImages>"
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == _SHEET_XML:
                data = sheet.encode()
            elif with_cellimages and item.filename == "[Content_Types].xml":
                data = data.replace(
                    b"</Types>", (_MEDIA_DEFAULTS + _CELLIMAGE_OVERRIDE + "</Types>").encode()
                )
            elif with_cellimages and item.filename == "xl/_rels/workbook.xml.rels":
                data = data.replace(
                    b"</Relationships>", (_WORKBOOK_CELLIMAGE_REL + "</Relationships>").encode()
                )
            dst.writestr(item, data)
        if with_cellimages:
            dst.writestr("xl/cellimages.xml", cellimages)
            dst.writestr(
                "xl/_rels/cellimages.xml.rels", _RELS_HEAD + "".join(rels) + "</Relationships>"
            )
            for name, data in images.values():
                dst.writestr(f"xl/media/{name}", data)
    return out.getvalue()


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------


class _FakeS3:
    """只替掉要联网的调用；记录写入（key → 字节）与删除的 key。"""

    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}
        self.deletes: list[str] = []
        self.fail_put = False

    def put_object(self, **kw: Any) -> dict[str, Any]:
        if self.fail_put:
            raise RuntimeError("fake r2 down")
        self.puts[kw["Key"]] = kw["Body"]
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}


@dataclass
class _Env:
    Maker: Any
    suffix: str
    tenant_id: Any
    user_id: UUID
    s3: _FakeS3
    files: dict[str, bytes] = field(default_factory=dict)
    file_reads: list[str] = field(default_factory=list)
    batch_ids: list[UUID] = field(default_factory=list)

    def sc(self, tag: str) -> str:
        return f"S{self.suffix}{tag}"

    def kc(self, tag: str) -> str:
        return f"K{self.suffix}{tag}"

    def row(self, tag: str, sku_tag: str | None = None, **cells: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "款式编码": self.sc(tag),
            "商品编码": self.kc(sku_tag or f"{tag}1"),
            "商品名称": f"款{tag}",
            "颜色": "红",
            "规格": "M",
        }
        base.update(cells)
        return base

    async def _exec(self, sql: str, **params: Any) -> None:
        async with self.Maker() as s:
            await s.execute(text(sql), params)
            await s.commit()

    async def style(
        self,
        tag: str,
        *,
        key: str | None = None,
        url: str | None = None,
        deleted: bool = False,
    ) -> UUID:
        sid = uuid4()
        await self._exec(
            "INSERT INTO style (id, tenant_id, style_code, style_name, external_image_url, "
            "main_image_key, tags, tag_color, design_status, is_active, is_deleted, created_at, "
            "updated_at) VALUES (:id, :tid, :code, :name, :url, :key, '[]'::jsonb, '[]'::jsonb, "
            "'大货', true, :deleted, NOW(), NOW())",
            id=sid,
            tid=self.tenant_id,
            code=self.sc(tag),
            name=f"款{tag}",
            url=url,
            key=key,
            deleted=deleted,
        )
        return sid

    async def sku(self, tag: str, style_id: UUID, *, base_price: str = "199.00") -> UUID:
        kid = uuid4()
        await self._exec(
            "INSERT INTO sku (id, tenant_id, style_id, sku_code, color, size, base_price, "
            "sourcing_type, is_active, is_deleted, created_at, updated_at) VALUES (:id, :tid, "
            ":sid, :code, '红', 'M', CAST(:price AS numeric), '自产', true, false, NOW(), NOW())",
            id=kid,
            tid=self.tenant_id,
            sid=style_id,
            code=self.kc(tag),
            price=base_price,
        )
        return kid

    async def new_batch(self, raw: bytes) -> UUID:
        batch_id = uuid4()
        self.batch_ids.append(batch_id)
        key = f"imports/{self.tenant_id}/{batch_id}/goods.xlsx"
        self.files[key] = raw
        await self._exec(
            "INSERT INTO import_batch (id, tenant_id, source, file_hash, original_filename, "
            "file_r2_key, file_bucket, status, total_rows, imported, failed, retry_count, "
            "created_by, created_at, updated_at) VALUES (:id, :tid, :src, :h, 'goods.xlsx', :k, "
            "'private', 'processing', 0, 0, 0, 0, :cb, NOW(), NOW())",
            id=batch_id,
            tid=self.tenant_id,
            src=SOURCE,
            h=f"{self.suffix}-{batch_id}",
            k=key,
            cb=self.user_id,
        )
        return batch_id

    async def run(self, raw: bytes) -> tuple[UUID, dict[str, Any]]:
        batch_id = await self.new_batch(raw)
        return batch_id, await _run_import_batch(batch_id, only_failed=False)

    async def one(self, sql: str, **params: Any) -> Any:
        async with self.Maker() as s:
            return (await s.execute(text(sql), params)).first()

    async def all(self, sql: str, **params: Any) -> list[Any]:
        async with self.Maker() as s:
            return list((await s.execute(text(sql), params)).fetchall())

    async def key_of(self, tag: str) -> str | None:
        row = await self.one(
            "SELECT main_image_key FROM style WHERE style_code = :c ORDER BY is_deleted LIMIT 1",
            c=self.sc(tag),
        )
        return row[0] if row else None

    async def style_id(self, tag: str) -> UUID:
        row = await self.one(
            "SELECT id FROM style WHERE style_code = :c AND is_deleted = false", c=self.sc(tag)
        )
        return row[0]

    async def jobs(self, batch_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT row_number, status, notes FROM import_job WHERE batch_id = :b "
            "ORDER BY row_number",
            b=batch_id,
        )

    async def images(self, batch_id: UUID) -> dict[int, dict[str, Any] | None]:
        """行号 → notes.image（没有则 None）。"""
        return {j.row_number: (j.notes or {}).get("image") for j in await self.jobs(batch_id)}

    async def batch(self, batch_id: UUID) -> Any:
        return await self.one(
            "SELECT status, total_rows, imported, failed, skipped, conflicted, warning_count "
            "FROM import_batch WHERE id = :b",
            b=batch_id,
        )

    async def audits(self, style_id: UUID) -> list[Any]:
        return await self.all(
            "SELECT actor_type, user_id, tenant_id, before, after FROM audit_log "
            "WHERE action = 'style.main_image.update' AND resource_id = :r ORDER BY created_at",
            r=str(style_id),
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
                        text("SELECT id FROM goods_main WHERE goods_code LIKE :p"), {"p": like}
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
            {"id": user_id, "tid": tenant_id, "u": f"img_{suffix}"},
        )
        await s.commit()

    import app.core.attachment as att_mod

    s3 = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", s3, raising=False)
    e = _Env(Maker=Maker, suffix=suffix, tenant_id=tenant_id, user_id=user_id, s3=s3)

    def get_object_bytes(_bucket: str, key: str) -> bytes:
        e.file_reads.append(key)
        return e.files[key]

    monkeypatch.setattr(att_mod.attachment_service, "get_object_bytes", get_object_bytes)
    try:
        yield e
    finally:
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry._adapters.update(saved)
        await e.cleanup()


def _set(code: str) -> dict[str, Any]:
    return {"status": "set", "style_code": code, "reason": None}


def _kept(code: str) -> dict[str, Any]:
    return {"status": "kept", "style_code": code, "reason": None}


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestSetMainImage:
    async def test_first_image_per_style_and_audit(self, env: _Env) -> None:
        """每款取第一张（其余行的图忽略、不提示）；已有款与新建款都补；审计带导入批次与行号。"""
        sa = await env.style("A")
        first_a, second_a, first_b = png(), png(90), jpg()
        raw = wps_xlsx(
            [
                env.row("A", "A1", 图片=Pic("ID_A1")),
                env.row("A", "A2", 图片=Pic("ID_A2")),
                env.row("B", "B1", 图片=Pic("ID_B1")),
                env.row("B", "B2", 图片=Pic("ID_B2")),
            ],
            {
                "ID_A1": ("image1.png", first_a),
                "ID_A2": ("image2.png", second_a),
                "ID_B1": ("image3.jpeg", first_b),
                "ID_B2": ("image4.webp", webp()),
            },
        )
        batch_id, result = await env.run(raw)
        assert result == {"status": "completed", "imported": 4, "failed": 0}

        key_a, key_b = await env.key_of("A"), await env.key_of("B")
        assert key_a is not None and key_b is not None
        assert env.s3.puts == {key_a: first_a, key_b: first_b}
        assert key_a.endswith(".png") and key_b.endswith(".jpg")
        assert env.s3.deletes == []
        assert await env.images(batch_id) == {
            1: _set(env.sc("A")),
            2: None,
            3: _set(env.sc("B")),
            4: None,
        }
        jobs = await env.jobs(batch_id)
        assert all(not (j.notes or {}).get("warnings") for j in jobs)
        assert (await env.batch(batch_id)).warning_count == 0

        [audit] = await env.audits(sa)
        assert (audit.actor_type, audit.user_id, audit.tenant_id) == (
            "worker",
            env.user_id,
            env.tenant_id,
        )
        assert audit.before == {"main_image_changed": False}
        assert audit.after == {
            "main_image_changed": True,
            "via": "import_embedded",
            "filename": None,
            "import_batch_id": str(batch_id),
            "row_number": 1,
        }
        [audit_b] = await env.audits(await env.style_id("B"))
        assert audit_b.after["row_number"] == 3

    async def test_existing_main_image_kept_and_external_url_filled(self, env: _Env) -> None:
        """已有主图（批量上传的）→ 不覆盖、不写 R2；只有外部链接的照样补。"""
        old = f"{env.tenant_id}/styles/x/main/old_main.png"
        await env.style("A", key=old)
        await env.style("B", url="https://img.example.invalid/b.jpg")
        raw = wps_xlsx(
            [env.row("A", 图片=Pic("ID_A")), env.row("B", 图片=Pic("ID_B"))],
            {"ID_A": ("image1.png", png()), "ID_B": ("image2.png", png())},
        )
        batch_id, result = await env.run(raw)
        assert result["status"] == "completed"
        assert await env.key_of("A") == old
        key_b = await env.key_of("B")
        assert list(env.s3.puts) == [key_b]
        assert env.s3.deletes == []
        assert await env.images(batch_id) == {1: _kept(env.sc("A")), 2: _set(env.sc("B"))}

    async def test_rerun_new_file_is_idempotent(self, env: _Env) -> None:
        """补过主图后再导一份（换了哈希的）同样文件：全部「已有主图」，不写 R2。"""
        rows = [env.row("A", 图片=Pic("ID_A")), env.row("B", 图片=Pic("ID_B"))]
        images = {"ID_A": ("image1.png", png()), "ID_B": ("image2.png", png())}
        await env.run(wps_xlsx(rows, images))
        keys = (await env.key_of("A"), await env.key_of("B"))
        assert len(env.s3.puts) == 2
        batch_id, _ = await env.run(wps_xlsx(rows, images))
        assert (await env.key_of("A"), await env.key_of("B")) == keys
        assert len(env.s3.puts) == 2
        assert await env.images(batch_id) == {1: _kept(env.sc("A")), 2: _kept(env.sc("B"))}


@pytest.mark.integration
@pytest.mark.asyncio
class TestWhichRows:
    async def test_skipped_rows_still_fill(self, env: _Env) -> None:
        """先不带图导过一次；再导同一批数据（行全是「内容一致跳过」）→ 照样补主图。"""
        await env.run(wps_xlsx([env.row("A"), env.row("B")], {}))
        assert (await env.key_of("A"), await env.key_of("B")) == (None, None)
        raw = wps_xlsx(
            [env.row("A", 图片=Pic("ID_A")), env.row("B", 图片=Pic("ID_B"))],
            {"ID_A": ("image1.png", png()), "ID_B": ("image2.png", png())},
        )
        batch_id, _ = await env.run(raw)
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["skipped", "skipped"]
        assert await env.images(batch_id) == {1: _set(env.sc("A")), 2: _set(env.sc("B"))}
        assert await env.key_of("A") is not None and await env.key_of("B") is not None

    async def test_conflict_failed_missing_and_deleted(self, env: _Env) -> None:
        """冲突行、失败但款式存在的行都补；款式不存在 / 已删除 → 跳过并记原因。"""
        sc = await env.style("C")
        await env.sku("C1", sc, base_price="199.00")
        await env.style("D")
        await env.style("F", deleted=True)
        raw = wps_xlsx(
            [
                env.row("C", "C1", 图片=Pic("ID_C"), 基本售价="100"),  # 售价不同 → 冲突
                env.row("D", 图片=Pic("ID_D"), 成本价="abc"),  # 价格不合法 → 失败
                env.row("E", 图片=Pic("ID_E"), 商品名称=None),  # 失败且款式不存在
                env.row("F", 图片=Pic("ID_F"), 商品名称=None),  # 失败且款式已删除
            ],
            {
                "ID_C": ("image1.png", png()),
                "ID_D": ("image2.png", png()),
                "ID_E": ("image3.png", png()),
                "ID_F": ("image4.png", png()),
            },
        )
        batch_id, result = await env.run(raw)
        assert result["status"] == "partial"
        jobs = await env.jobs(batch_id)
        assert [j.status for j in jobs] == ["conflict", "failed", "failed", "failed"]
        assert await env.images(batch_id) == {
            1: _set(env.sc("C")),
            2: _set(env.sc("D")),
            3: {"status": "skipped", "style_code": env.sc("E"), "reason": "未找到款式"},
            4: {"status": "skipped", "style_code": env.sc("F"), "reason": "款式已删除"},
        }
        assert await env.key_of("C") is not None and await env.key_of("D") is not None
        assert await env.key_of("F") is None
        assert len(env.s3.puts) == 2
        # 失败行的 import_job 只多了 notes.image，原因照旧
        assert (await env.batch(batch_id)).failed == 3

    async def test_only_failed_rerun_looks_at_rerun_rows(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只重跑失败行：图片段只看重跑的行，原文件另取一次；首跑已写的结果不动。"""
        real = tasks._upsert_job
        fail_row = {2}

        async def flaky(session: Any, **kw: Any) -> None:
            if kw["row_number"] in fail_row and kw["status"] != "failed":
                raise DataError("INSERT INTO import_job ...", {}, Exception("boom"))
            await real(session, **kw)

        monkeypatch.setattr(tasks, "_upsert_job", flaky)
        raw = wps_xlsx(
            [env.row("A", 图片=Pic("ID_A")), env.row("B", 图片=Pic("ID_B"))],
            {"ID_A": ("image1.png", png()), "ID_B": ("image2.png", png())},
        )
        batch_id, result = await env.run(raw)
        assert result["status"] == "partial"
        # 行 2 失败、款式 B 没建出来 → 未找到款式；A 补了
        assert await env.images(batch_id) == {
            1: _set(env.sc("A")),
            2: {"status": "skipped", "style_code": env.sc("B"), "reason": "未找到款式"},
        }
        assert len(env.file_reads) == 1

        fail_row.clear()
        await env._exec("UPDATE import_batch SET status = 'processing' WHERE id = :b", b=batch_id)
        result = await _run_import_batch(batch_id, only_failed=True)
        assert result["status"] == "completed"
        assert len(env.file_reads) == 2  # 重跑失败行另取一次原文件
        assert await env.images(batch_id) == {1: _set(env.sc("A")), 2: _set(env.sc("B"))}
        assert len(env.s3.puts) == 2

    async def test_only_failed_file_unreadable(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """只重跑失败行时原文件取不到 → 这些款计「保存失败」原因「读取原文件失败」，批次照常。"""
        real = tasks._upsert_job
        fail_row = {1}

        async def flaky(session: Any, **kw: Any) -> None:
            if kw["row_number"] in fail_row and kw["status"] != "failed":
                raise DataError("INSERT INTO import_job ...", {}, Exception("boom"))
            await real(session, **kw)

        monkeypatch.setattr(tasks, "_upsert_job", flaky)
        raw = wps_xlsx([env.row("A", 图片=Pic("ID_A"))], {"ID_A": ("image1.png", png())})
        batch_id, _ = await env.run(raw)
        fail_row.clear()
        env.files.clear()  # 原文件取不到（KeyError）
        await env._exec("UPDATE import_batch SET status = 'processing' WHERE id = :b", b=batch_id)
        result = await _run_import_batch(batch_id, only_failed=True)
        assert result["status"] == "completed"
        assert await env.images(batch_id) == {
            1: {"status": "failed", "style_code": env.sc("A"), "reason": "读取原文件失败"}
        }
        assert env.s3.puts == {}


@pytest.mark.integration
@pytest.mark.asyncio
class TestInvalidAndFailures:
    async def test_invalid_images_rows_still_imported(self, env: _Env) -> None:
        """图无效（魔数不符、≥ 300KB、不支持的类型、External、找不到）→ 该款跳过计原因，行照常导入。"""
        raw = wps_xlsx(
            [
                env.row("A", 图片=Pic("ID_A")),
                env.row("B", 图片=Pic("ID_B")),
                env.row("C", 图片=Pic("ID_C")),
                env.row("D", 图片=Pic("ID_D")),
                env.row("E", 图片=Pic("ID_NOPE")),
                env.row("A", "A2", 图片=Pic("ID_OK")),  # 第一张无效不往后找
            ],
            {
                "ID_A": ("image1.png", jpg()),
                "ID_B": ("image2.png", png(300 * 1024)),
                "ID_C": ("image3.gif", b"GIF89a" + os.urandom(32)),
                "ID_OK": ("image4.png", png()),
            },
            external={"ID_D": "https://img.example.invalid/d.png"},
        )
        batch_id, result = await env.run(raw)
        assert result == {"status": "completed", "imported": 6, "failed": 0}
        assert await env.images(batch_id) == {
            1: {
                "status": "invalid",
                "style_code": env.sc("A"),
                "reason": BATCH_REJECT_REASONS["signature"],
            },
            2: {
                "status": "invalid",
                "style_code": env.sc("B"),
                "reason": BATCH_REJECT_REASONS["too_large"],
            },
            3: {
                "status": "invalid",
                "style_code": env.sc("C"),
                "reason": BATCH_REJECT_REASONS["type"],
            },
            4: {"status": "invalid", "style_code": env.sc("D"), "reason": IMAGE_NOT_FOUND_REASON},
            5: {"status": "invalid", "style_code": env.sc("E"), "reason": IMAGE_NOT_FOUND_REASON},
            6: None,
        }
        assert env.s3.puts == {}
        for tag in "ABCDE":
            assert await env.key_of(tag) is None

    async def test_no_cellimages_part(self, env: _Env) -> None:
        """有 DISPIMG 但文件里没有 cellimages（Excel 另存过之类）→ 计「图片无效」带原因。"""
        raw = wps_xlsx([env.row("A", 图片=Pic("ID_A"))], {}, with_cellimages=False)
        batch_id, result = await env.run(raw)
        assert result["status"] == "completed"
        assert await env.images(batch_id) == {
            1: {"status": "invalid", "style_code": env.sc("A"), "reason": MISSING_REASON}
        }

    async def test_no_dispimg_no_image_notes(self, env: _Env) -> None:
        """没有 DISPIMG 引用（图片列空 / 链接）→ 图片段什么都不做，notes 维持 NULL。"""
        raw = wps_xlsx([env.row("A"), env.row("B", 图片="https://img.example.invalid/b.jpg")], {})
        batch_id, _ = await env.run(raw)
        assert [j.notes for j in await env.jobs(batch_id)] == [None, None]
        assert env.s3.puts == {}

    async def test_lock_recheck_kept_and_compensated(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """R2 写完、写库前有人传了主图 → 不覆盖、补偿删除刚写的对象，计「已有主图」。"""
        await env.style("A")
        other = f"{env.tenant_id}/styles/x/main/someone_main.png"
        real_lock = StyleMainImageStore._lock_style

        async def racing_lock(self: StyleMainImageStore, sid: UUID) -> Any:
            assert env.s3.puts, "应在 R2 写完之后才加锁"
            await env._exec("UPDATE style SET main_image_key = :k WHERE id = :i", k=other, i=sid)
            return await real_lock(self, sid)

        monkeypatch.setattr(StyleMainImageStore, "_lock_style", racing_lock)
        batch_id, _ = await env.run(
            wps_xlsx([env.row("A", 图片=Pic("ID_A"))], {"ID_A": ("image1.png", png())})
        )
        assert await env.key_of("A") == other
        assert env.s3.deletes == list(env.s3.puts)
        assert await env.images(batch_id) == {1: _kept(env.sc("A"))}

    async def test_storage_failure(self, env: _Env) -> None:
        """R2 写失败 → 计「保存失败」原因「存储失败」，库不变，行数据不受影响。"""
        env.s3.fail_put = True
        batch_id, result = await env.run(
            wps_xlsx([env.row("A", 图片=Pic("ID_A"))], {"ID_A": ("image1.png", png())})
        )
        assert result == {"status": "completed", "imported": 1, "failed": 0}
        assert await env.key_of("A") is None
        assert await env.images(batch_id) == {
            1: {"status": "failed", "style_code": env.sc("A"), "reason": "存储失败"}
        }

    async def test_image_phase_error_keeps_rows(
        self, env: _Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """图片段抛异常 → 已导入的行不回滚，批次照常 completed、计数照常。"""

        def boom(_raw: bytes) -> Any:
            raise RuntimeError("cellimages boom")

        monkeypatch.setattr(style_sku_images, "load_cell_images", boom)
        batch_id, result = await env.run(
            wps_xlsx([env.row("A", 图片=Pic("ID_A"))], {"ID_A": ("image1.png", png())})
        )
        assert result == {"status": "completed", "imported": 1, "failed": 0}
        b = await env.batch(batch_id)
        assert (b.status, b.total_rows, b.imported) == ("completed", 1, 1)
        assert await env.style_id("A") is not None
        assert await env.images(batch_id) == {1: None}
