"""商品资料导入的图片段：读 WPS 单元格内嵌图，给还没有主图的款式补主图（8a 补充）。

runner 在行循环之后、批次汇总之前调用（``StyleSkuImportAdapter.after_rows``），整段异常隔离、
不回滚已导入的行。业务方已定：缩略图可以当主图（不设尺寸下限、不缩放）；一款只取一张；
已有主图的永远不覆盖。

- 参与的行 = 本次执行处理的所有行（不看行结果：内容一致跳过、冲突、失败但款式存在的行都算）；
  款式编码精确匹配未删除款式，找不到 / 只有已删除的 → 跳过并记原因
- 每款按行号取第一个有 DISPIMG 引用的行；它的图无效就计「图片无效」，不往后找；其余行的图忽略
- 每款每批最多一条结果：只重跑失败行时，本批次已有 set / kept / invalid 结果的款不再取图；
  failed / skipped 的款把原取图行（取自 ``import_job.raw_data``）放回候选重新尝试，结果落回同一行、取代旧的
- 只看 ``main_image_key``：只有外部链接的款式照样补。写 R2 前先查一次（已有 → 不写 R2），
  写库时 ``StyleMainImageStore.replace(only_if_empty=True)`` 加锁复判，判不过就补偿删除
- 每款一个事务（``replace`` 自己提交）；结果写在取图那一行 ``import_job.notes["image"]``：
  ``{status, style_code, reason}``，``status`` ∈ set / kept / invalid / skipped / failed。
  不进 ``notes.warnings``，``warning_count`` 口径不变
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import text

from app.modules.importer.embedded_images import ImageInvalid, load_cell_images, parse_dispimg_id
from app.modules.product.style_image_service import StyleMainImageStore

if TYPE_CHECKING:
    from app.modules.importer.adapter import BatchRunContext
    from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

ImageStatus = Literal["set", "kept", "invalid", "skipped", "failed"]
IMAGE_STATUSES: tuple[ImageStatus, ...] = ("set", "kept", "invalid", "skipped", "failed")

VIA = "import_embedded"
STYLE_NOT_FOUND_REASON = "未找到款式"
STYLE_DELETED_REASON = "款式已删除"
FILE_UNREADABLE_REASON = "读取原文件失败"

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tid, true)")
_LOAD_STYLES = text(
    "SELECT id, style_code, is_deleted, main_image_key FROM style "
    "WHERE tenant_id = CAST(:tid AS uuid) AND style_code = ANY(CAST(:codes AS text[]))"
)
_LOAD_PRIOR = text(
    "SELECT row_number, raw_data, notes -> 'image' FROM import_job "
    "WHERE batch_id = CAST(:bid AS uuid) AND notes -> 'image' IS NOT NULL"
)
# 这些结果是定论：重跑时不再取图（与「第一张无效不往后找」一致）；failed / skipped 照常重新尝试
_FINAL_STATUSES = frozenset({"set", "kept", "invalid"})
_WRITE_NOTE = text(
    "UPDATE import_job SET notes = COALESCE(notes, '{}'::jsonb) "
    "|| jsonb_build_object('image', CAST(:img AS jsonb)) "
    "WHERE batch_id = CAST(:bid AS uuid) AND row_number = CAST(:rn AS integer)"
)


@dataclass(frozen=True)
class ImagePick:
    row_number: int
    image_id: str


@dataclass(frozen=True)
class ImageResult:
    row_number: int
    style_code: str
    status: ImageStatus
    reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "style_code": self.style_code, "reason": self.reason}


@dataclass(frozen=True)
class _StyleRef:
    id: UUID
    has_image: bool


def pick_first_images(
    adapter: StyleSkuImportAdapter,
    rows: Sequence[tuple[int, dict[str, Any]]],
    mapping: FieldMapping | None,
) -> dict[str, ImagePick]:
    """款式编码 → 按行号第一个有 DISPIMG 引用的行（「图片」列原值，按映射取）。"""
    picks: dict[str, ImagePick] = {}
    for row_number, row in sorted(rows, key=lambda item: item[0]):
        try:
            parsed: dict[str, Any] | None = adapter.parse_row(row, mapping)
        except Exception:  # 解析不了的行没有可用的款式编码
            parsed = None
        if parsed is None:
            continue
        code = parsed.get("style_code")
        image_id = parse_dispimg_id(parsed.get("external_image_url"))
        if not code or image_id is None or code in picks:
            continue
        picks[str(code)] = ImagePick(row_number, image_id)
    return picks


async def _load_prior(
    run: BatchRunContext,
) -> tuple[set[str], list[tuple[int, dict[str, Any]]]]:
    """本批次之前执行留下的每款结果（重跑改写行时 runner 保留 ``notes.image``）。

    返回（已有定论的款式编码，结果是 failed / skipped 的取图行 ``(行号, 原始行)``）。首跑时都为空。
    """
    async with run.bypass_session() as s:
        rows = (await s.execute(_LOAD_PRIOR, {"bid": str(run.batch_id)})).all()
    final: set[str] = set()
    retry_rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, raw_data, image in rows:
        if image.get("status") in _FINAL_STATUSES:
            final.add(str(image.get("style_code")))
        else:
            retry_rows.append((int(row_number), dict(raw_data or {})))
    return final, retry_rows


async def _load_styles(run: BatchRunContext, codes: list[str]) -> dict[str, _StyleRef | None]:
    """款式编码 → 未删除款式（None = 只有已删除的）；查不到的不出现。"""
    async with run.app_session() as s:
        await s.execute(_SET_TENANT, {"tid": str(run.tenant_id)})
        rows = (await s.execute(_LOAD_STYLES, {"tid": str(run.tenant_id), "codes": codes})).all()
    found: dict[str, _StyleRef | None] = {}
    for style_id, code, is_deleted, key in rows:
        if is_deleted:
            found.setdefault(str(code), None)
        else:
            found[str(code)] = _StyleRef(style_id, bool(key))
    return found


async def _write_one(
    run: BatchRunContext, style_id: UUID, pick: ImagePick, mime_type: str, data: bytes
) -> tuple[ImageStatus, str | None]:
    async with run.app_session() as s:
        await s.execute(_SET_TENANT, {"tid": str(run.tenant_id)})
        written = await StyleMainImageStore(s).replace(
            style_id=style_id,
            tenant_id=run.tenant_id,
            user_id=run.actor_id,
            mime_type=mime_type,
            data=data,
            via=VIA,
            only_if_empty=True,
            actor_type="worker",
            audit_extra={"import_batch_id": str(run.batch_id), "row_number": pick.row_number},
        )
    if written.status == "kept":
        return "kept", None
    if written.status == "failed":
        return "failed", written.reason
    return "set", None


async def _write_notes(run: BatchRunContext, results: Sequence[ImageResult]) -> None:
    if not results:
        return
    async with run.bypass_session() as s:
        await s.execute(
            _WRITE_NOTE,
            [
                {
                    "img": json.dumps(r.to_json(), ensure_ascii=False),
                    "bid": str(run.batch_id),
                    "rn": r.row_number,
                }
                for r in results
            ],
        )
        await s.commit()


async def apply_embedded_main_images(
    adapter: StyleSkuImportAdapter, run: BatchRunContext
) -> list[ImageResult]:
    """读内嵌图补主图；返回每款的结果（已写回 ``import_job.notes["image"]``）。

    没有任何 DISPIMG 引用 → 什么都不做（不取文件）。中途出错时已得出的结果照样写回，再把异常
    抛给 runner（runner 只记日志）。

    重跑（只重跑失败行 / 整文件重跑）时每款在本批次最多一条结果：之前已是 set / kept / invalid 的款
    不再取图；之前是 failed / skipped 的，把当时的取图行放回候选一起按行号取，结果落回那一行、取代旧的。
    """
    final, retry_rows = await _load_prior(run)
    in_run = {row_number for row_number, _ in run.rows}
    candidates = [*run.rows, *(r for r in retry_rows if r[0] not in in_run)]
    picks = {
        code: pick
        for code, pick in pick_first_images(adapter, candidates, run.mapping).items()
        if code not in final
    }
    if not picks:
        return []
    results: list[ImageResult] = []
    try:
        try:
            raw = run.load_file()
        except Exception as exc:
            log.warning(
                "import_embedded_images_file_unreadable",
                extra={"batch_id": str(run.batch_id), "error_type": type(exc).__name__},
            )
            results = [
                ImageResult(p.row_number, code, "failed", FILE_UNREADABLE_REASON)
                for code, p in picks.items()
            ]
            return results
        index = load_cell_images(raw)
        styles = await _load_styles(run, list(picks))
        for code, pick in picks.items():
            if code not in styles:
                results.append(
                    ImageResult(pick.row_number, code, "skipped", STYLE_NOT_FOUND_REASON)
                )
                continue
            style = styles[code]
            if style is None:
                results.append(ImageResult(pick.row_number, code, "skipped", STYLE_DELETED_REASON))
                continue
            if style.has_image:  # 写 R2 前的预查；写库时还会加锁复判
                results.append(ImageResult(pick.row_number, code, "kept"))
                continue
            blob = index.get(pick.image_id)
            if isinstance(blob, ImageInvalid):
                results.append(ImageResult(pick.row_number, code, "invalid", blob.reason))
                continue
            status, reason = await _write_one(run, style.id, pick, blob.mime_type, blob.data)
            results.append(ImageResult(pick.row_number, code, status, reason))
        return results
    finally:
        await _write_notes(run, results)
        summary = Counter(r.status for r in results)
        log.info(
            "import_embedded_images",
            extra={
                "batch_id": str(run.batch_id),
                "summary": {status: summary[status] for status in IMAGE_STATUSES},
            },
        )


__all__ = [
    "FILE_UNREADABLE_REASON",
    "IMAGE_STATUSES",
    "STYLE_DELETED_REASON",
    "STYLE_NOT_FOUND_REASON",
    "VIA",
    "ImagePick",
    "ImageResult",
    "ImageStatus",
    "apply_embedded_main_images",
    "pick_first_images",
]
