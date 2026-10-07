"""款式主图：校验、写入（单张与批量共用）与按款号批量上传（8a-2，设计 §7.3，J11）。

- ``read_image_batch_form``：批量接口的请求体解析。不调 ``request.form()``、不新建 ``Request``
  （Sentry 预读过的小请求上新实例会卡死，§1.4），在公开的 ``request.stream()`` 外面边读边计数，
  直接喂给 Starlette 的 ``MultiPartParser``；所有拒绝都在任何 R2 写入之前
- ``validate_style_main_image``：JPG / PNG / WebP 白名单、非空、严格小于 300KB、魔数与声明类型一致；
  返回固定的原因码，单张上传与批量各自映射成自己的文案
- ``StyleMainImageStore.replace``：服务端生成 key → 代传私有桶 → 加锁重读款式 → 写 key → 审计 →
  提交；提交失败回滚并删掉刚写的对象（补偿），成功后尽力删旧对象
- ``StyleImageBatchService.upload``：全部预检完才开始写；一条查询按款号（不区分大小写）匹配；
  逐张写入、逐张提交，单张失败不影响其他张
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect, Request

from app.core.attachment import attachment_service
from app.core.audit import AuditService
from app.core.exceptions import AppException, ValidationError
from app.modules.auth.models import User
from app.modules.product.images import image_stem
from app.modules.product.models import Style
from app.modules.product.schemas import (
    StyleImageBatchItem,
    StyleImageBatchResponse,
    StyleImageBatchSummary,
)

log = logging.getLogger(__name__)

MAX_FILES = 20
MAX_BODY_BYTES = 6_815_744  # 6.5 MiB：20 × 300KB 加表单开销

STYLE_MAIN_IMAGE_MAX_BYTES = 300 * 1024
STYLE_IMAGE_MIME_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
FILENAME_MAX_LEN = 255


# ---------------------------------------------------------------------------
# 请求体解析（设计 §7.3 完整代码）
# ---------------------------------------------------------------------------


class _BodyTooLarge(Exception):
    pass


async def _limited_stream(request: Request, limit: int) -> AsyncGenerator[bytes, None]:
    seen = 0
    async for chunk in request.stream():  # 预读过就是缓存的 _body，否则逐块读 receive
        seen += len(chunk)
        if seen > limit:
            raise _BodyTooLarge
        yield chunk


def _too_large() -> AppException:
    return AppException(
        "一次上传的图片合计超过 6.5MB，请减少张数", code="IMAGE_BATCH_TOO_LARGE", status_code=413
    )


async def read_image_batch_form(request: Request) -> FormData:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()  # ① 声明已超限：不读请求体
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise ValidationError("上传表单格式不正确", code="IMAGE_BATCH_FORM_INVALID")
    parser = MultiPartParser(
        request.headers,
        _limited_stream(request, MAX_BODY_BYTES),
        max_files=MAX_FILES,
        max_fields=5,
    )
    try:
        return await parser.parse()  # ② 边读边计数 ③ 解析中截断文件数
    except _BodyTooLarge as exc:
        raise _too_large() from exc
    except MultiPartException as exc:  # 文件 / 字段超限、缺 boundary、缺 name
        if exc.message.startswith("Too many files"):
            raise ValidationError(
                f"一次最多上传 {MAX_FILES} 张", code="IMAGE_BATCH_COUNT_INVALID"
            ) from exc
        raise ValidationError("上传表单格式不正确", code="IMAGE_BATCH_FORM_INVALID") from exc
    except ValueError as exc:  # python-multipart 0.0.12 的 MultipartParseError、解码错误
        raise ValidationError("上传表单格式不正确", code="IMAGE_BATCH_FORM_INVALID") from exc
    except ClientDisconnect as exc:  # 用户中途关了页面：响应没人收，只为不落成 500 + Sentry
        raise ValidationError("上传中断", code="IMAGE_BATCH_FORM_INVALID") from exc


def batch_count_invalid() -> ValidationError:
    """解析之后再数一遍：0 个文件（只有非文件字段、或 ``files`` 不是文件）也拒（§7.3 ④）。"""
    return ValidationError(f"请选择 1 ~ {MAX_FILES} 张图片上传", code="IMAGE_BATCH_COUNT_INVALID")


# ---------------------------------------------------------------------------
# 单张校验（单张上传与批量共用）
# ---------------------------------------------------------------------------

ImageRejectCode = Literal["type", "empty", "too_large", "signature"]

BATCH_REJECT_REASONS: dict[str, str] = {
    "type": "类型不支持",
    "empty": "文件为空",
    "too_large": "不小于 300KB",
    "signature": "内容与类型不符",
}


def _signature_matches(mime_type: str, data: bytes) -> bool:
    return (
        (mime_type == "image/jpeg" and data.startswith(b"\xff\xd8\xff"))
        or (mime_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (
            mime_type == "image/webp"
            and len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
        )
    )


def validate_style_main_image(mime_type: str | None, data: bytes) -> ImageRejectCode | None:
    """款式主图规则：类型白名单 → 非空 → 严格小于 300KB → 魔数与声明类型一致。

    合格返回 None，否则返回第一个不满足的原因码（按上面的顺序判断）。
    """
    if mime_type not in STYLE_IMAGE_MIME_EXTENSIONS:
        return "type"
    if not data:
        return "empty"
    if len(data) >= STYLE_MAIN_IMAGE_MAX_BYTES:
        return "too_large"
    if not _signature_matches(mime_type, data):
        return "signature"
    return None


# ---------------------------------------------------------------------------
# 写入（单张与批量共用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MainImageWriteResult:
    status: Literal["created", "replaced", "failed"]
    reason: str | None = None
    """失败原因（「存储失败」「款式已删除」「保存失败」）。"""
    error: BaseException | None = None
    """失败时的原始异常：单张上传据此原样抛出，保持对外行为不变。"""
    style: Style | None = None


class StyleMainImageStore:
    """一张主图一个事务：写 R2 → 加锁写库 → 提交；提交失败补偿删除刚写的对象。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _lock_style(self, style_id: UUID) -> Style | None:
        stmt = (
            select(Style)
            .where(Style.id == style_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        style = (await self._session.execute(stmt)).scalar_one_or_none()
        if style is None or style.is_deleted:
            return None
        return style

    async def _delete_quietly(self, style_id: UUID, key: str, event: str) -> None:
        try:
            await run_in_threadpool(attachment_service.delete, "private", key)
        except Exception:
            log.warning(event, extra={"style_id": str(style_id), "key": key})

    async def replace(
        self,
        *,
        style_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        mime_type: str,
        data: bytes,
        via: str | None = None,
        filename: str | None = None,
    ) -> MainImageWriteResult:
        """``mime_type`` / ``data`` 须已通过 ``validate_style_main_image``。

        ``via`` 不为 None 时（批量上传）审计 after 里带 ``via`` 与 ``filename``；单张上传不带，
        审计内容与原来一致。
        """
        extension = STYLE_IMAGE_MIME_EXTENSIONS[mime_type]
        # 文件名只用来匹配款号，不进 key
        new_key = attachment_service.make_tenant_key(
            tenant_id, f"styles/{style_id}/main", filename=f"main.{extension}"
        )
        try:
            await run_in_threadpool(
                attachment_service.upload_bytes,
                data,
                bucket="private",
                key=new_key,
                content_type=mime_type,
            )
        except Exception as exc:
            log.warning(
                "style_main_image_upload_failed",
                extra={"style_id": str(style_id), "error_type": type(exc).__name__},
            )
            return MainImageWriteResult("failed", "存储失败", error=exc)

        old_key: str | None = None
        try:
            style = await self._lock_style(style_id)
            if style is None:
                await self._session.rollback()
                await self._delete_quietly(
                    style_id, new_key, "style_main_image_orphan_delete_failed"
                )
                return MainImageWriteResult("failed", "款式已删除")
            old_key = style.main_image_key
            style.main_image_key = new_key
            after: dict[str, object] = {"main_image_changed": True}
            if via is not None:
                after.update({"via": via, "filename": filename})
            await AuditService(self._session).log(
                action="style.main_image.update",
                resource="style",
                resource_id=style_id,
                before={"main_image_changed": old_key is not None},
                after=after,
                user_id=user_id,
            )
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            log.warning(
                "style_main_image_save_failed",
                extra={"style_id": str(style_id), "error_type": type(exc).__name__},
            )
            await self._delete_quietly(
                style_id, new_key, "style_main_image_compensation_delete_failed"
            )
            return MainImageWriteResult("failed", "保存失败", error=exc)

        if old_key and old_key != new_key:
            await self._delete_quietly(
                style_id, old_key, "style_main_image_old_object_delete_failed"
            )
        return MainImageWriteResult("replaced" if old_key else "created", style=style)


# ---------------------------------------------------------------------------
# 批量上传
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    index: int
    filename: str
    stem: str
    mime_type: str | None = None
    data: bytes = b""
    result: StyleImageBatchItem | None = None


class StyleImageBatchService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._store = StyleMainImageStore(session)

    async def upload(self, files: Sequence[UploadFile], user: User) -> StyleImageBatchResponse:
        # 提前取出：单张提交失败会回滚、让会话里的对象过期
        tenant_id: UUID = user.tenant_id
        user_id: UUID = user.id

        entries = [
            _Entry(index=i, filename=f.filename or "", stem=image_stem(f.filename or ""))
            for i, f in enumerate(files)
        ]

        def reject(entry: _Entry, reason: str) -> None:
            entry.result = StyleImageBatchItem(
                filename=entry.filename, stem=entry.stem, status="rejected", reason=reason
            )

        # 第 5 步：全部预检完才开始写
        for entry in entries:
            if not entry.stem or len(entry.filename) > FILENAME_MAX_LEN:
                reject(entry, "文件名无效")
        counts = Counter(e.stem.lower() for e in entries if e.result is None)
        for entry in entries:
            if entry.result is None and counts[entry.stem.lower()] > 1:
                reject(entry, "同批重名")
        for entry, upload in zip(entries, files, strict=True):
            if entry.result is not None:
                continue
            entry.mime_type = upload.content_type
            entry.data = await upload.read(STYLE_MAIN_IMAGE_MAX_BYTES + 1)
            code = validate_style_main_image(entry.mime_type, entry.data)
            if code is not None:
                reject(entry, BATCH_REJECT_REASONS[code])

        # 第 6 步：一条查询匹配（不区分大小写；停用的照样匹配，已删除的不匹配）
        pending = [e for e in entries if e.result is None]
        matches: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
        if pending:
            rows = await self._session.execute(
                select(Style.id, Style.style_code).where(
                    Style.tenant_id == tenant_id,
                    Style.is_deleted.is_(False),
                    func.lower(Style.style_code).in_({e.stem.lower() for e in pending}),
                )
            )
            for sid, matched_code in rows.all():
                matches[str(matched_code).lower()].append((sid, str(matched_code)))

        # 第 7 步：逐张写入，每张一个事务
        for entry in pending:
            found = sorted(matches.get(entry.stem.lower(), []), key=lambda m: m[1])
            if not found:
                entry.result = StyleImageBatchItem(
                    filename=entry.filename, stem=entry.stem, status="unmatched"
                )
                continue
            if len(found) > 1:
                codes = "、".join(code for _sid, code in found)
                reject(entry, f"款号大小写不唯一（{codes}），请按准确款号命名")
                continue
            style_id, style_code = found[0]
            written = await self._store.replace(
                style_id=style_id,
                tenant_id=tenant_id,
                user_id=user_id,
                mime_type=entry.mime_type or "",  # 已过 validate_style_main_image，必在白名单里
                data=entry.data,
                via="batch_upload",
                filename=entry.filename,
            )
            entry.result = StyleImageBatchItem(
                filename=entry.filename,
                stem=entry.stem,
                status=written.status,
                style_id=style_id,
                style_code=style_code,
                reason=written.reason,
            )

        results = [e.result for e in entries if e.result is not None]
        status_counts = Counter(r.status for r in results)
        summary = StyleImageBatchSummary(
            created=status_counts["created"],
            replaced=status_counts["replaced"],
            unmatched=status_counts["unmatched"],
            rejected=status_counts["rejected"],
            failed=status_counts["failed"],
        )
        # 计数放在 summary 键下：created 是 LogRecord 的保留属性，直接展开会抛 KeyError
        log.info("style_image_batch", extra={"summary": summary.model_dump()})
        return StyleImageBatchResponse(results=results, summary=summary)


__all__ = [
    "BATCH_REJECT_REASONS",
    "MAX_BODY_BYTES",
    "MAX_FILES",
    "STYLE_IMAGE_MIME_EXTENSIONS",
    "STYLE_MAIN_IMAGE_MAX_BYTES",
    "MainImageWriteResult",
    "StyleImageBatchService",
    "StyleMainImageStore",
    "batch_count_invalid",
    "read_image_batch_form",
    "validate_style_main_image",
]
