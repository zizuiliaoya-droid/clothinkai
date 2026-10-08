"""聚水潭商品资料导出里的 WPS 单元格内嵌图（8a 补充，纯解析：无 DB、无网络）。

结构（WPS 私有）：「图片」格是 ``_xlfn.DISPIMG("ID_…",1)``（runner 用 data_only 读到缓存值
``=DISPIMG("ID_…",1)``）→ ``xl/cellimages.xml`` 里 ``cNvPr@name="ID_…"`` 的 ``blip@r:embed`` →
``xl/_rels/cellimages.xml.rels`` 的 ``Target`` → ``xl/media/*``。

文件不可信：
- 解压前按 ``ZipInfo.file_size`` 拦超大条目；实际读取也有上限（声明值可以造假）
- XML 严格 UTF-8 解码，含 DOCTYPE / ENTITY 即拒，不解析 DTD
- rels 里 ``TargetMode="External"``、带协议、跳出 ``xl/`` 的目标一律忽略；从不访问外部链接
- 图片照款式主图规则（``validate_style_main_image``）校验；单张 ≥ 300KB 不打开
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

from app.modules.product.style_image_service import (
    BATCH_REJECT_REASONS,
    STYLE_MAIN_IMAGE_MAX_BYTES,
    validate_style_main_image,
)

CELLIMAGES_PATH = "xl/cellimages.xml"
CELLIMAGES_RELS_PATH = "xl/_rels/cellimages.xml.rels"
CELLIMAGES_MAX_BYTES = 8 * 1024 * 1024
RELS_MAX_BYTES = 4 * 1024 * 1024
TOTAL_IMAGE_READ_LIMIT = 64 * 1024 * 1024

_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_IMAGE_EXTENSION_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

MISSING_REASON = "文件里没有内嵌图片数据（请用 WPS 打开原导出文件直接导入）"
INDEX_INVALID_REASON = "内嵌图片数据无法读取"
IMAGE_NOT_FOUND_REASON = "文件里找不到这张内嵌图片"
IMAGE_UNREADABLE_REASON = "内嵌图片无法读取"
TOTAL_LIMIT_REASON = "内嵌图片合计过大，未读取"

_DISPIMG_RE = re.compile(
    r'^\s*=?\s*(?:_xlfn\.)?DISPIMG\(\s*"([A-Za-z0-9_]{1,64})"\s*,\s*\d+\s*\)\s*$',
    re.IGNORECASE,
)
_FORBIDDEN_XML_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
# 读取时可能抛出的异常：坏压缩流、加密条目、不支持的压缩方式等
_READ_ERRORS = (
    zipfile.BadZipFile,
    RuntimeError,
    OSError,
    EOFError,
    NotImplementedError,
    ValueError,
)


def parse_dispimg_id(value: object) -> str | None:
    """「图片」格的值是 DISPIMG 公式（缓存值或公式本身）时返回图片 ID，否则 None。"""
    if not isinstance(value, str):
        return None
    match = _DISPIMG_RE.match(value)
    return match.group(1) if match else None


@dataclass(frozen=True)
class ImageBlob:
    mime_type: str
    data: bytes


@dataclass(frozen=True)
class ImageInvalid:
    reason: str


class _IndexInvalid(Exception):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_member(zf: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    """读一个条目：不存在返回 None；声明或实际超过 ``limit`` 视为无效。"""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise _IndexInvalid
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except _READ_ERRORS as exc:
        raise _IndexInvalid from exc
    if len(data) > limit:
        raise _IndexInvalid
    return data


def _parse_xml(data: bytes) -> ElementTree.Element:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _IndexInvalid from exc
    if _FORBIDDEN_XML_RE.search(text):
        raise _IndexInvalid
    try:
        # 传 str：expat 按 UTF-8 解析，忽略声明里的 encoding；上面已拒 DOCTYPE / ENTITY，
        # 没有 DTD 就没有实体展开与外部实体（不加 defusedxml 依赖）
        return ElementTree.fromstring(text)  # noqa: S314
    except ElementTree.ParseError as exc:
        raise _IndexInvalid from exc


def _resolve_target(target: str) -> str | None:
    """rels 的 Target → 包内路径；带协议、反斜杠、``..``、不在 ``xl/`` 下的返回 None。"""
    if not target or "://" in target or "\\" in target or ":" in target:
        return None
    if ".." in target.split("/"):
        return None
    path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
    path = posixpath.normpath(path)
    if not path.startswith("xl/") or path == "xl":
        return None
    return path


class CellImageIndex:
    """图片 ID → 包内图片路径；``get`` 时才读图片（每张有上限、合计有上限）。"""

    def __init__(
        self,
        zf: zipfile.ZipFile | None = None,
        targets: dict[str, str | None] | None = None,
        missing_reason: str | None = None,
    ) -> None:
        self._zf = zf
        self._targets = targets or {}
        self.missing_reason = missing_reason
        self._read_total = 0

    def __len__(self) -> int:
        return len(self._targets)

    def get(self, image_id: str) -> ImageBlob | ImageInvalid:
        if self.missing_reason is not None or self._zf is None:
            return ImageInvalid(self.missing_reason or MISSING_REASON)
        path = self._targets.get(image_id)
        if path is None:
            return ImageInvalid(IMAGE_NOT_FOUND_REASON)
        try:
            info = self._zf.getinfo(path)
        except KeyError:
            return ImageInvalid(IMAGE_NOT_FOUND_REASON)
        extension = posixpath.splitext(path)[1].lstrip(".").lower()
        mime_type = _IMAGE_EXTENSION_MIME.get(extension)
        if mime_type is None:
            return ImageInvalid(BATCH_REJECT_REASONS["type"])
        if info.file_size >= STYLE_MAIN_IMAGE_MAX_BYTES:  # 不打开
            return ImageInvalid(BATCH_REJECT_REASONS["too_large"])
        if self._read_total + info.file_size > TOTAL_IMAGE_READ_LIMIT:
            return ImageInvalid(TOTAL_LIMIT_REASON)
        try:
            with self._zf.open(info) as fh:
                data = fh.read(STYLE_MAIN_IMAGE_MAX_BYTES + 1)
        except _READ_ERRORS:
            return ImageInvalid(IMAGE_UNREADABLE_REASON)
        self._read_total += len(data)
        code = validate_style_main_image(mime_type, data)
        if code is not None:
            return ImageInvalid(BATCH_REJECT_REASONS[code])
        return ImageBlob(mime_type=mime_type, data=data)


def load_cell_images(raw: bytes) -> CellImageIndex:
    """解析一次 zip，建「图片 ID → 包内路径」索引。没有内嵌图数据或数据无效时返回空索引带原因。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile, ValueError, OSError):
        return CellImageIndex(missing_reason=MISSING_REASON)
    try:
        cellimages = _read_member(zf, CELLIMAGES_PATH, CELLIMAGES_MAX_BYTES)
        if cellimages is None:
            return CellImageIndex(missing_reason=MISSING_REASON)
        rels_data = _read_member(zf, CELLIMAGES_RELS_PATH, RELS_MAX_BYTES)
        if rels_data is None:
            return CellImageIndex(missing_reason=MISSING_REASON)
        cell_root = _parse_xml(cellimages)
        rels_root = _parse_xml(rels_data)
    except _IndexInvalid:
        return CellImageIndex(missing_reason=INDEX_INVALID_REASON)

    rels: dict[str, str | None] = {}
    for rel in rels_root.iter():
        if _local(rel.tag) != "Relationship":
            continue
        rel_id = rel.get("Id")
        if not rel_id or rel_id in rels:  # 同名取第一个
            continue
        if (rel.get("TargetMode") or "").lower() == "external":
            rels[rel_id] = None
            continue
        rels[rel_id] = _resolve_target(rel.get("Target") or "")

    targets: dict[str, str | None] = {}
    for cell_image in cell_root.iter():
        if _local(cell_image.tag) != "cellImage":
            continue
        name: str | None = None
        embed: str | None = None
        for node in cell_image.iter():
            local = _local(node.tag)
            if local == "cNvPr" and name is None:
                name = node.get("name")
            elif local == "blip" and embed is None:
                embed = node.get(_R_EMBED)
        if not name or name in targets:  # 同名取第一个
            continue
        targets[name] = rels.get(embed) if embed else None
    return CellImageIndex(zf=zf, targets=targets)


__all__ = [
    "CELLIMAGES_MAX_BYTES",
    "IMAGE_NOT_FOUND_REASON",
    "INDEX_INVALID_REASON",
    "MISSING_REASON",
    "RELS_MAX_BYTES",
    "TOTAL_IMAGE_READ_LIMIT",
    "CellImageIndex",
    "ImageBlob",
    "ImageInvalid",
    "load_cell_images",
    "parse_dispimg_id",
]
