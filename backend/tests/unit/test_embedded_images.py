"""8a 补充：WPS 单元格内嵌图解析（纯解析，无 DB）。

包结构照聚水潭商品资料的 WPS 导出：``xl/cellimages.xml``（etc 命名空间）→
``xl/_rels/cellimages.xml.rels`` → ``xl/media/*``；图片 = 魔数 + 随机字节，现造。
"""

from __future__ import annotations

import io
import os
import struct
import zipfile

import pytest
from openpyxl import Workbook

from app.modules.importer import embedded_images as ei
from app.modules.importer.embedded_images import (
    IMAGE_NOT_FOUND_REASON,
    INDEX_INVALID_REASON,
    MISSING_REASON,
    ImageBlob,
    ImageInvalid,
    load_cell_images,
    parse_dispimg_id,
)
from app.modules.product.style_image_service import BATCH_REJECT_REASONS

PNG = b"\x89PNG\r\n\x1a\n"
JPG = b"\xff\xd8\xff\xe0"
WEBP_HEAD = b"RIFF\x00\x00\x00\x00WEBP"

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


def _png(size: int = 64) -> bytes:
    return PNG + os.urandom(size)


def cellimages_xml(images: list[tuple[str, str]]) -> str:
    """images: [(图片 ID, rId)]"""
    body = "".join(
        _CELL_IMAGE.format(n=i + 2, name=name, rid=rid) for i, (name, rid) in enumerate(images)
    )
    return _CELLIMAGES_HEAD + body + "</etc:cellImages>"


def rels_xml(rels: list[tuple[str, str, bool]]) -> str:
    """rels: [(rId, Target, 是否 External)]"""
    body = "".join(
        _REL.format(rid=rid, target=target, mode=' TargetMode="External"' if ext else "")
        for rid, target, ext in rels
    )
    return _RELS_HEAD + body + "</Relationships>"


def build_package(
    *,
    cellimages: str | bytes | None,
    rels: str | bytes | None,
    media: dict[str, bytes] | None = None,
    base: bytes | None = None,
) -> bytes:
    """在 ``base``（默认空 zip）上加 WPS 内嵌图的几个部件。"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        if base is not None:
            with zipfile.ZipFile(io.BytesIO(base)) as zin:
                for info in zin.infolist():
                    zout.writestr(info, zin.read(info))
        if cellimages is not None:
            zout.writestr(ei.CELLIMAGES_PATH, cellimages)
        if rels is not None:
            zout.writestr(ei.CELLIMAGES_RELS_PATH, rels)
        for name, data in (media or {}).items():
            zout.writestr(name, data)
    return out.getvalue()


def simple_package(**media_overrides: bytes) -> tuple[bytes, dict[str, bytes]]:
    media = {"xl/media/image1.png": _png(), "xl/media/image2.png": _png(80)}
    media.update(media_overrides)
    raw = build_package(
        cellimages=cellimages_xml([("ID_AAA1", "rId1"), ("ID_BBB2", "rId2")]),
        rels=rels_xml([("rId2", "media/image2.png", False), ("rId1", "media/image1.png", False)]),
        media=media,
    )
    return raw, media


# ---------------------------------------------------------------------------
# parse_dispimg_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=DISPIMG("ID_760E0C52",1)', "ID_760E0C52"),  # WPS 缓存值
        ('=_xlfn.DISPIMG("ID_760E0C52",1)', "ID_760E0C52"),  # 公式本身
        ('_xlfn.DISPIMG("ID_X",1)', "ID_X"),
        ('=dispimg("id_lower_1",1)', "id_lower_1"),  # 不区分大小写
        (' = DISPIMG( "ID_SP" , 1 ) ', "ID_SP"),
    ],
)
def test_parse_dispimg_id_accepts(value: str, expected: str) -> None:
    assert parse_dispimg_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        "#NAME?",
        "#VALUE!",
        "https://img.example.invalid/a.jpg",
        "条纹衬衫",
        '=DISPIMG("ID-有中文",1)',  # ID 字符不合法
        '=DISPIMG("../x",1)',
        '=DISPIMG("' + "A" * 65 + '",1)',  # 超长
        '=DISPIMG("",1)',
        '=DISPIMG("ID_A",1) + 1',
        '=IMAGE("ID_A",1)',
    ],
)
def test_parse_dispimg_id_rejects(value: object) -> None:
    assert parse_dispimg_id(value) is None


def test_parse_dispimg_id_max_length_ok() -> None:
    assert parse_dispimg_id('=DISPIMG("' + "A" * 64 + '",1)') == "A" * 64


# ---------------------------------------------------------------------------
# load_cell_images：正常
# ---------------------------------------------------------------------------


def test_load_and_get_images() -> None:
    raw, media = simple_package()
    index = load_cell_images(raw)
    assert index.missing_reason is None
    assert len(index) == 2
    first = index.get("ID_AAA1")
    assert first == ImageBlob(mime_type="image/png", data=media["xl/media/image1.png"])
    second = index.get("ID_BBB2")
    assert isinstance(second, ImageBlob)
    assert second.data == media["xl/media/image2.png"]


def test_openpyxl_workbook_with_wps_parts() -> None:
    """从 openpyxl 造的真实 xlsx 出发加 WPS 部件，sheet 等其他部件不影响解析。"""
    wb = Workbook()
    wb.active.append(["图片", "款式编码"])
    wb.active.append(['=DISPIMG("ID_AAA1",1)', "TEST-STYLE-1"])
    buf = io.BytesIO()
    wb.save(buf)
    img = JPG + os.urandom(32)
    raw = build_package(
        cellimages=cellimages_xml([("ID_AAA1", "rId1")]),
        rels=rels_xml([("rId1", "media/image1.jpeg", False)]),
        media={"xl/media/image1.jpeg": img},
        base=buf.getvalue(),
    )
    assert load_cell_images(raw).get("ID_AAA1") == ImageBlob("image/jpeg", img)


@pytest.mark.parametrize(
    ("name", "head", "mime"),
    [
        ("a.jpg", JPG, "image/jpeg"),
        ("a.JPEG", JPG, "image/jpeg"),
        ("a.webp", WEBP_HEAD, "image/webp"),
        ("a.png", PNG, "image/png"),
    ],
)
def test_mime_by_extension(name: str, head: bytes, mime: str) -> None:
    data = head + os.urandom(16)
    raw = build_package(
        cellimages=cellimages_xml([("ID_A", "rId1")]),
        rels=rels_xml([("rId1", f"media/{name}", False)]),
        media={f"xl/media/{name}": data},
    )
    assert load_cell_images(raw).get("ID_A") == ImageBlob(mime, data)


def test_absolute_target_from_package_root_accepted() -> None:
    data = _png()
    raw = build_package(
        cellimages=cellimages_xml([("ID_A", "rId1")]),
        rels=rels_xml([("rId1", "/xl/media/image1.png", False)]),
        media={"xl/media/image1.png": data},
    )
    assert load_cell_images(raw).get("ID_A") == ImageBlob("image/png", data)


def test_duplicate_names_and_rel_ids_take_first() -> None:
    first, second = _png(), _png(90)
    raw = build_package(
        cellimages=cellimages_xml([("ID_A", "rId1"), ("ID_A", "rId2")]),
        rels=rels_xml(
            [
                ("rId1", "media/image1.png", False),
                ("rId1", "media/image2.png", False),
                ("rId2", "media/image2.png", False),
            ]
        ),
        media={"xl/media/image1.png": first, "xl/media/image2.png": second},
    )
    assert load_cell_images(raw).get("ID_A") == ImageBlob("image/png", first)


# ---------------------------------------------------------------------------
# 缺失 / 非 zip
# ---------------------------------------------------------------------------


def test_not_zip_is_missing() -> None:
    index = load_cell_images("款式编码,商品编码\nA,B\n".encode())
    assert index.missing_reason == MISSING_REASON
    assert index.get("ID_A") == ImageInvalid(MISSING_REASON)


def test_no_cellimages_is_missing() -> None:
    wb = Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    index = load_cell_images(buf.getvalue())
    assert index.missing_reason == MISSING_REASON
    assert len(index) == 0
    assert index.get("ID_A") == ImageInvalid(MISSING_REASON)


def test_no_rels_is_missing() -> None:
    raw = build_package(cellimages=cellimages_xml([("ID_A", "rId1")]), rels=None)
    assert load_cell_images(raw).missing_reason == MISSING_REASON


@pytest.mark.parametrize(
    ("image_id", "cell", "rels", "media"),
    [
        ("ID_OTHER", [("ID_A", "rId1")], [("rId1", "media/a.png", False)], ["xl/media/a.png"]),
        ("ID_A", [("ID_A", "rId9")], [("rId1", "media/a.png", False)], ["xl/media/a.png"]),
        ("ID_A", [("ID_A", "rId1")], [("rId1", "media/a.png", False)], []),
    ],
    ids=["unknown-id", "unknown-rid", "missing-media"],
)
def test_missing_links_invalid(
    image_id: str, cell: list[tuple[str, str]], rels: list[tuple[str, str, bool]], media: list[str]
) -> None:
    raw = build_package(
        cellimages=cellimages_xml(cell),
        rels=rels_xml(rels),
        media={m: _png() for m in media},
    )
    assert load_cell_images(raw).get(image_id) == ImageInvalid(IMAGE_NOT_FOUND_REASON)


# ---------------------------------------------------------------------------
# rels 安全：External / 越界 / 协议
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "external"),
    [
        ("media/image1.png", True),  # External
        ("https://img.example.invalid/a.png", False),
        ("http://img.example.invalid/a.png", False),
        ("file:///etc/passwd", False),
        ("../../x.png", False),
        ("../docProps/x.png", False),
        ("media/../../x.png", False),
        ("/docProps/x.png", False),  # 包根下但不在 xl/
        ("media\\image1.png", False),
        ("C:/x.png", False),
        ("", False),
    ],
)
def test_unsafe_targets_ignored(target: str, external: bool) -> None:
    raw = build_package(
        cellimages=cellimages_xml([("ID_A", "rId1")]),
        rels=rels_xml([("rId1", target, external)]),
        # 每个可能的落点都放一张合格的图：被接受就会读出来
        media={
            "xl/media/image1.png": _png(),
            "x.png": _png(),
            "docProps/x.png": _png(),
        },
    )
    assert load_cell_images(raw).get("ID_A") == ImageInvalid(IMAGE_NOT_FOUND_REASON)


# ---------------------------------------------------------------------------
# XML 安全：DOCTYPE / ENTITY / 坏 XML / 非 UTF-8
# ---------------------------------------------------------------------------

_DOCTYPE = '<!DOCTYPE r [<!ENTITY x "ID_A">]>'


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.replace("?>\n", "?>\n" + _DOCTYPE, 1),
        lambda s: s.replace("?>\n", "?>\n<!doctype r>", 1),
        lambda s: s.replace("?>\n", '?>\n<! ENTITY x "y">', 1),
        lambda s: s[:-10],  # 截断的 XML
    ],
    ids=["doctype-entity", "doctype-lower", "entity", "broken"],
)
@pytest.mark.parametrize("part", ["cellimages", "rels"])
def test_unsafe_or_broken_xml_rejected(mutate, part: str) -> None:  # type: ignore[no-untyped-def]
    cell = cellimages_xml([("ID_A", "rId1")])
    rels = rels_xml([("rId1", "media/image1.png", False)])
    if part == "cellimages":
        cell = mutate(cell)
    else:
        rels = mutate(rels)
    raw = build_package(cellimages=cell, rels=rels, media={"xl/media/image1.png": _png()})
    index = load_cell_images(raw)
    assert index.missing_reason == INDEX_INVALID_REASON
    assert index.get("ID_A") == ImageInvalid(INDEX_INVALID_REASON)


def test_non_utf8_xml_rejected() -> None:
    cell = cellimages_xml([("ID_A", "rId1")]).encode("utf-16")
    raw = build_package(
        cellimages=cell,
        rels=rels_xml([("rId1", "media/image1.png", False)]),
        media={"xl/media/image1.png": _png()},
    )
    assert load_cell_images(raw).missing_reason == INDEX_INVALID_REASON


# ---------------------------------------------------------------------------
# 大小：cellimages / rels 超限不读；单张 ≥ 300KB 不打开；合计上限
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit_name", ["CELLIMAGES_MAX_BYTES", "RELS_MAX_BYTES"])
def test_oversized_index_parts_rejected(monkeypatch: pytest.MonkeyPatch, limit_name: str) -> None:
    raw, _ = simple_package()
    monkeypatch.setattr(ei, limit_name, 200)
    index = load_cell_images(raw)
    assert index.missing_reason == INDEX_INVALID_REASON


def test_oversized_cellimages_not_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, _ = simple_package()
    monkeypatch.setattr(ei, "CELLIMAGES_MAX_BYTES", 200)
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(getattr(name, "filename", name))
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    assert load_cell_images(raw).missing_reason == INDEX_INVALID_REASON
    assert ei.CELLIMAGES_PATH not in opened


def test_image_too_large_not_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    big = PNG + b"\x00" * (300 * 1024)  # 压缩后很小，解压后 ≥ 300KB
    raw, _ = simple_package(**{"xl/media/image1.png": big})
    index = load_cell_images(raw)
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *args, **kwargs):  # type: ignore[no-untyped-def]
        opened.append(getattr(name, "filename", name))
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)
    assert index.get("ID_AAA1") == ImageInvalid(BATCH_REJECT_REASONS["too_large"])
    assert opened == []
    assert isinstance(index.get("ID_BBB2"), ImageBlob)  # 其他图照常
    assert opened == ["xl/media/image2.png"]


def test_image_just_under_limit_ok() -> None:
    data = PNG + os.urandom(300 * 1024 - 1 - len(PNG))
    raw, _ = simple_package(**{"xl/media/image1.png": data})
    assert load_cell_images(raw).get("ID_AAA1") == ImageBlob("image/png", data)


def test_total_read_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, media = simple_package()
    monkeypatch.setattr(ei, "TOTAL_IMAGE_READ_LIMIT", len(media["xl/media/image1.png"]) + 10)
    index = load_cell_images(raw)
    assert isinstance(index.get("ID_AAA1"), ImageBlob)
    assert index.get("ID_BBB2") == ImageInvalid(ei.TOTAL_LIMIT_REASON)


# ---------------------------------------------------------------------------
# 图片内容：魔数 / 类型 / 空
# ---------------------------------------------------------------------------


def test_signature_mismatch_invalid() -> None:
    raw, _ = simple_package(**{"xl/media/image1.png": JPG + os.urandom(16)})
    assert load_cell_images(raw).get("ID_AAA1") == ImageInvalid(BATCH_REJECT_REASONS["signature"])


def test_empty_image_invalid() -> None:
    raw, _ = simple_package(**{"xl/media/image1.png": b""})
    assert load_cell_images(raw).get("ID_AAA1") == ImageInvalid(BATCH_REJECT_REASONS["empty"])


@pytest.mark.parametrize("name", ["image1.gif", "image1.bmp", "image1"])
def test_unsupported_type_invalid(name: str) -> None:
    raw = build_package(
        cellimages=cellimages_xml([("ID_A", "rId1")]),
        rels=rels_xml([("rId1", f"media/{name}", False)]),
        media={f"xl/media/{name}": b"GIF89a" + os.urandom(16)},
    )
    assert load_cell_images(raw).get("ID_A") == ImageInvalid(BATCH_REJECT_REASONS["type"])


# ---------------------------------------------------------------------------
# 压缩数据损坏：只影响那一个条目
# ---------------------------------------------------------------------------


def damage_member(raw: bytes, name: str, method: int = zipfile.ZIP_DEFLATED) -> bytes:
    """把 ``name`` 按 ``method`` 重新压缩，再把它的压缩数据段改成 0xFF（LZMA 保留 9 字节头）。"""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            compress = method if info.filename == name else zipfile.ZIP_DEFLATED
            zout.writestr(info.filename, zin.read(info), compress_type=compress)
    data = bytearray(out.getvalue())
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo(name)
    offset = info.header_offset
    name_len, extra_len = struct.unpack("<HH", data[offset + 26 : offset + 30])
    start = offset + 30 + name_len + extra_len
    keep = 9 if method == zipfile.ZIP_LZMA else 0
    data[start + keep : start + info.compress_size] = b"\xff" * (info.compress_size - keep)
    return bytes(data)


@pytest.mark.parametrize(
    "method",
    [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2, zipfile.ZIP_STORED],
    ids=["deflate", "lzma", "bzip2", "stored"],
)
def test_damaged_image_entry_invalid_others_ok(method: int) -> None:
    """单张图的压缩数据坏了（zlib.error / LZMAError / OSError / CRC 错）→ 只这张无效，别的照常。"""
    raw, media = simple_package()
    index = load_cell_images(damage_member(raw, "xl/media/image1.png", method))
    assert index.get("ID_AAA1") == ImageInvalid(ei.IMAGE_UNREADABLE_REASON)
    assert index.get("ID_BBB2") == ImageBlob("image/png", media["xl/media/image2.png"])


@pytest.mark.parametrize(
    "method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA], ids=["deflate", "lzma"]
)
@pytest.mark.parametrize("part", [ei.CELLIMAGES_PATH, ei.CELLIMAGES_RELS_PATH])
def test_damaged_index_entry_invalid(part: str, method: int) -> None:
    """cellimages / rels 的压缩数据坏了 → 整份索引「内嵌图片数据无法读取」，不抛异常。"""
    raw, _ = simple_package()
    index = load_cell_images(damage_member(raw, part, method))
    assert index.missing_reason == INDEX_INVALID_REASON
    assert index.get("ID_AAA1") == ImageInvalid(INDEX_INVALID_REASON)
