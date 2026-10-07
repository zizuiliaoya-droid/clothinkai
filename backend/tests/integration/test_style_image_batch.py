"""8a-2：款式主图按款号批量上传（HTTP 级；设计 §7.3、§13、§15.1）。

``POST /api/styles/main-images/batch``：文件名 = 款号（不区分大小写）匹配，后端代传 R2，逐张提交、
提交失败补偿删除。权限用迁移 seed 出来的真实角色（``load_effective_permissions``），override
``get_current_user_active`` / ``get_current_perms`` / ``get_session``。R2 一律用假 client
（``attachment_service._client``），记录 put / delete，不向任何真实存储发请求。

覆盖：AC 6 ~ 9、N7（文件数、损坏 multipart、非 multipart）、N10（Content-Length 声明超限、
chunked 超限 / 未超限），以及单张上传原有行为不变。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.product.models import Style
from app.modules.product.style_image_service import MAX_BODY_BYTES

URL = "/api/styles/main-images/batch"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
BIG_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * (300 * 1024)  # ≥ 300KB


class _FakeS3:
    """只替掉要联网的三个调用；记录写入与删除的 key。"""

    def __init__(self) -> None:
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_put = False

    def put_object(self, **kw: Any) -> dict[str, Any]:
        if self.fail_put:
            raise RuntimeError("fake r2 down")
        self.puts.append(kw["Key"])
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}

    def generate_presigned_url(self, _op: str, **kw: Any) -> str:
        return f"https://fake-r2.local/{(kw.get('Params') or {}).get('Key', '')}"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


def _app() -> Any:
    from app.main import app

    return app


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.fixture
def as_role(
    session: AsyncSession, factory: Any, tenant_a: Any, tenant_ctx: None
) -> Iterator[Callable[[str], Any]]:
    """``await as_role("merchandiser")`` → 之后的请求以该角色的新用户身份发出。"""
    app = _app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override

    async def apply(role_code: str) -> Any:
        role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
        user = await factory.user(tenant_a, roles=[role])
        perms = await AuthService(session).load_effective_permissions(user.id)
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        return user

    yield apply
    # app 是模块级单例，不清理会污染后面的用例
    for dep in (get_session, get_current_user_active, get_current_perms):
        app.dependency_overrides.pop(dep, None)


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test")


def _files(*items: tuple[str, bytes, str]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", item) for item in items]


BOUNDARY = "8aBoundary"


def _multipart(parts: list[tuple[str, bytes]]) -> bytes:
    """手工拼 multipart：parts = [(头部若干行, 内容)]。"""
    out = b""
    for headers, body in parts:
        out += f"--{BOUNDARY}\r\n{headers}\r\n\r\n".encode() + body + b"\r\n"
    return out + f"--{BOUNDARY}--\r\n".encode()


def _file_part(name: str, filename: str, ctype: str, body: bytes) -> tuple[str, bytes]:
    return (
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}",
        body,
    )


MP_HEADERS = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


async def _style(session: AsyncSession, style_id: Any) -> Style:
    return (
        await session.execute(
            select(Style).where(Style.id == style_id).execution_options(populate_existing=True)
        )
    ).scalar_one()


@pytest.mark.integration
@pytest.mark.asyncio
class TestBatchResults:
    async def test_ac6_mixed_batch(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        """AC 6：只有 2025946 成功；两张 2025945 同批重名；不存在.jpg 未匹配；伪装与超大被拒。"""
        await as_role("merchandiser")
        ok = await product_factory.style(style_code="2025946")
        dup = await product_factory.style(style_code="2025945")
        fake = await product_factory.style(style_code="FAKE01")
        big = await product_factory.style(style_code="BIG01")
        async with _client() as client:
            resp = await client.post(
                URL,
                files=_files(
                    ("2025946.PNG", PNG, "image/png"),
                    ("2025945.jpg", JPG, "image/jpeg"),
                    ("2025945.webp", WEBP, "image/webp"),
                    ("不存在.jpg", JPG, "image/jpeg"),
                    ("FAKE01.jpg", b"just some text, not an image", "image/jpeg"),
                    ("BIG01.png", BIG_PNG, "image/png"),
                ),
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_name = {r["filename"]: r for r in body["results"]}
        assert [r["filename"] for r in body["results"]] == [
            "2025946.PNG",
            "2025945.jpg",
            "2025945.webp",
            "不存在.jpg",
            "FAKE01.jpg",
            "BIG01.png",
        ]
        assert by_name["2025946.PNG"]["status"] == "created"
        assert by_name["2025946.PNG"]["style_code"] == "2025946"
        assert by_name["2025946.PNG"]["stem"] == "2025946"
        for name in ("2025945.jpg", "2025945.webp"):
            assert (by_name[name]["status"], by_name[name]["reason"]) == ("rejected", "同批重名")
        assert by_name["不存在.jpg"]["status"] == "unmatched"
        assert (by_name["FAKE01.jpg"]["status"], by_name["FAKE01.jpg"]["reason"]) == (
            "rejected",
            "内容与类型不符",
        )
        assert (by_name["BIG01.png"]["status"], by_name["BIG01.png"]["reason"]) == (
            "rejected",
            "不小于 300KB",
        )
        assert body["summary"] == {
            "created": 1,
            "replaced": 0,
            "unmatched": 1,
            "rejected": 4,
            "failed": 0,
        }
        # 只有成功的那一张产生 R2 对象；被拒与未匹配的款式库里不变
        assert len(s3.puts) == 1
        key = (await _style(session, ok.id)).main_image_key
        assert key is not None and key == s3.puts[0]
        assert key.startswith(f"{ok.tenant_id}/styles/{ok.id}/main/")
        assert key.endswith("_main.png")  # 文件名不进 key
        for other in (dup, fake, big):
            assert (await _style(session, other.id)).main_image_key is None

    async def test_case_insensitive_match_and_inactive_style(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        """不区分大小写匹配；停用的款式照样匹配，已删除的不匹配；目录与首尾空白被去掉。"""
        await as_role("operations")
        style = await product_factory.style(style_code="AbC-01", is_active=False)
        await product_factory.style(style_code="GONE01", is_deleted=True)
        async with _client() as client:
            resp = await client.post(
                URL,
                files=_files(
                    ("dir/abc-01 .webp", WEBP, "image/webp"),
                    ("GONE01.png", PNG, "image/png"),
                ),
            )
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert results[0]["status"] == "created"
        assert (results[0]["stem"], results[0]["style_code"]) == ("abc-01", "AbC-01")
        assert results[1]["status"] == "unmatched"
        assert (await _style(session, style.id)).main_image_key == s3.puts[0]

    async def test_ambiguous_case_rejected(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        """款号仅大小写不同的两个款式 → 被拒「款号大小写不唯一（X、Y）…」，零写入。"""
        await as_role("admin")
        await product_factory.style(style_code="ab1")
        await product_factory.style(style_code="AB1")
        async with _client() as client:
            resp = await client.post(URL, files=_files(("Ab1.png", PNG, "image/png")))
        [result] = resp.json()["results"]
        assert result["status"] == "rejected"
        assert result["reason"] == "款号大小写不唯一（AB1、ab1），请按准确款号命名"
        assert s3.puts == []

    async def test_type_and_empty_rejected(
        self,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        await as_role("admin")
        await product_factory.style(style_code="T01")
        await product_factory.style(style_code="E01")
        async with _client() as client:
            resp = await client.post(
                URL,
                files=_files(
                    ("T01.gif", b"GIF89a" + b"\x00" * 10, "image/gif"),
                    ("E01.png", b"", "image/png"),
                ),
            )
        reasons = [(r["status"], r["reason"]) for r in resp.json()["results"]]
        assert reasons == [("rejected", "类型不支持"), ("rejected", "文件为空")]
        assert s3.puts == []

    async def test_ac8_replace_deletes_old_and_audits(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        """AC 8：已有主图 → 新 key 生效、旧对象被删、style.main_image.update 审计、结果 replaced。"""
        user = await as_role("merchandiser")
        old_key = f"{uuid4()}/styles/old/main/x_main.png"
        style = await product_factory.style(style_code="R01", main_image_key=old_key)
        async with _client() as client:
            resp = await client.post(URL, files=_files(("R01.jpg", JPG, "image/jpeg")))
        [result] = resp.json()["results"]
        assert result["status"] == "replaced"
        assert resp.json()["summary"]["replaced"] == 1
        new_key = (await _style(session, style.id)).main_image_key
        assert new_key == s3.puts[0] and new_key != old_key
        assert s3.deletes == [old_key]
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "style.main_image.update",
                    AuditLog.resource_id == str(style.id),
                )
            )
        ).scalar_one()
        assert audit.resource == "style"
        assert audit.user_id == user.id
        assert audit.before == {"main_image_changed": True}
        assert audit.after == {
            "main_image_changed": True,
            "via": "batch_upload",
            "filename": "R01.jpg",
        }

    async def test_ac7_commit_failure_compensates(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC 7：R2 写成功、提交失败 → 刚写的对象被删除、主图不变、结果「保存失败」；下一张照常。"""
        await as_role("merchandiser")
        old_key = "keep/old_main.png"
        first = await product_factory.style(style_code="C01", main_image_key=old_key)
        second = await product_factory.style(style_code="C02")
        # 回滚会让会话里的对象过期，先取出 id
        first_id, second_id = first.id, second.id
        # 种子先提交（外层事务里只释放 savepoint），回滚只撤销失败那一张
        await session.commit()

        real_commit = session.commit
        calls = {"n": 0}

        async def flaky_commit() -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("commit boom")
            await real_commit()

        monkeypatch.setattr(session, "commit", flaky_commit)
        async with _client() as client:
            resp = await client.post(
                URL,
                files=_files(("C01.png", PNG, "image/png"), ("C02.png", PNG, "image/png")),
            )
        assert resp.status_code == 200, resp.text
        r1, r2 = resp.json()["results"]
        assert (r1["status"], r1["reason"]) == ("failed", "保存失败")
        assert r2["status"] == "created"
        assert resp.json()["summary"]["failed"] == 1
        failed_key = s3.puts[0]
        assert failed_key in s3.deletes  # 补偿删除
        assert old_key not in s3.deletes  # 旧对象没被误删
        assert (await _style(session, first_id)).main_image_key == old_key
        assert (await _style(session, second_id)).main_image_key == s3.puts[1]
        audits = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "style.main_image.update",
                        AuditLog.resource_id == str(first_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audits == []

    async def test_storage_failure(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        """R2 写失败 → failed「存储失败」，库不变。"""
        await as_role("merchandiser")
        style = await product_factory.style(style_code="S01")
        s3.fail_put = True
        async with _client() as client:
            resp = await client.post(URL, files=_files(("S01.png", PNG, "image/png")))
        [result] = resp.json()["results"]
        assert (result["status"], result["reason"]) == ("failed", "存储失败")
        assert (await _style(session, style.id)).main_image_key is None


@pytest.mark.integration
@pytest.mark.asyncio
class TestPermissions:
    """AC 9：与单张上传同权限 product:write。"""

    @pytest.mark.parametrize("role_code", ["pr", "pr_manager", "designer", "finance", "warehouse"])
    async def test_forbidden(
        self, as_role: Callable[[str], Any], s3: _FakeS3, role_code: str
    ) -> None:
        await as_role(role_code)
        async with _client() as client:
            resp = await client.post(URL, files=_files(("X1.png", PNG, "image/png")))
        assert resp.status_code == 403
        assert s3.puts == []

    @pytest.mark.parametrize("role_code", ["admin", "merchandiser", "operations"])
    async def test_allowed(
        self, product_factory: Any, as_role: Callable[[str], Any], s3: _FakeS3, role_code: str
    ) -> None:
        await as_role(role_code)
        await product_factory.style(style_code="P01")
        async with _client() as client:
            resp = await client.post(URL, files=_files(("P01.png", PNG, "image/png")))
        assert resp.status_code == 200, resp.text
        assert resp.json()["summary"]["created"] == 1


@pytest.mark.integration
@pytest.mark.asyncio
class TestRequestLimits:
    """N7、N10：这些拒绝都在任何 R2 写入之前。"""

    @pytest.fixture(autouse=True)
    async def _admin(self, as_role: Callable[[str], Any]) -> None:
        await as_role("admin")

    async def test_21_files_count_invalid(self, product_factory: Any, s3: _FakeS3) -> None:
        await product_factory.style(style_code="N00")
        files = [(f"N{i:02d}.png", PNG, "image/png") for i in range(21)]
        async with _client() as client:
            resp = await client.post(URL, files=_files(*files))
        assert resp.status_code == 422
        assert resp.json()["code"] == "IMAGE_BATCH_COUNT_INVALID"
        assert s3.puts == []

    async def test_20_files_ok(self, s3: _FakeS3) -> None:
        files = [(f"M{i:02d}.png", PNG, "image/png") for i in range(20)]
        async with _client() as client:
            resp = await client.post(URL, files=_files(*files))
        assert resp.status_code == 200
        assert resp.json()["summary"]["unmatched"] == 20

    async def test_only_non_file_fields(self, s3: _FakeS3) -> None:
        body = _multipart(
            [
                ('Content-Disposition: form-data; name="note"', b"hello"),
                ('Content-Disposition: form-data; name="files"', b"not-a-file"),
            ]
        )
        async with _client() as client:
            resp = await client.post(URL, content=body, headers=MP_HEADERS)
        assert resp.status_code == 422
        assert resp.json()["code"] == "IMAGE_BATCH_COUNT_INVALID"

    async def test_zero_files(self, s3: _FakeS3) -> None:
        async with _client() as client:
            resp = await client.post(
                URL, content=f"--{BOUNDARY}--\r\n".encode(), headers=MP_HEADERS
            )
        # 只有结尾分隔符的空表单：python-multipart 0.0.12 当作格式错；两种都是写入前的 422
        assert resp.status_code == 422
        assert resp.json()["code"] in {"IMAGE_BATCH_COUNT_INVALID", "IMAGE_BATCH_FORM_INVALID"}
        assert s3.puts == []

    async def test_json_body(self, s3: _FakeS3) -> None:
        async with _client() as client:
            resp = await client.post(URL, json={"files": []})
        assert resp.status_code == 422
        assert resp.json()["code"] == "IMAGE_BATCH_FORM_INVALID"

    async def test_missing_name(self, product_factory: Any, s3: _FakeS3) -> None:
        """缺 name → MultiPartException 路径。"""
        await product_factory.style(style_code="Q01")
        body = _multipart(
            [('Content-Disposition: form-data; filename="Q01.png"\r\nContent-Type: image/png', PNG)]
        )
        async with _client() as client:
            resp = await client.post(URL, content=body, headers=MP_HEADERS)
        assert resp.status_code == 422
        assert resp.json()["code"] == "IMAGE_BATCH_FORM_INVALID"
        assert s3.puts == []

    async def test_broken_delimiter(self, s3: _FakeS3) -> None:
        """分隔行格式错 → python-multipart MultipartParseError（ValueError）路径。"""
        async with _client() as client:
            resp = await client.post(URL, content=f"--{BOUNDARY}X\r\n".encode(), headers=MP_HEADERS)
        assert resp.status_code == 422
        assert resp.json()["code"] == "IMAGE_BATCH_FORM_INVALID"
        assert s3.puts == []

    async def test_declared_too_large(self, product_factory: Any, s3: _FakeS3) -> None:
        """Content-Length 声明超 6.5MB → 413、零写入。"""
        await product_factory.style(style_code="L01")
        payload = PNG + b"\x00" * (MAX_BODY_BYTES + 1)
        async with _client() as client:
            resp = await client.post(URL, files=_files(("L01.png", payload, "image/png")))
        assert resp.status_code == 413
        assert resp.json()["code"] == "IMAGE_BATCH_TOO_LARGE"
        assert s3.puts == []

    @staticmethod
    def _chunked(body: bytes) -> Any:
        async def gen() -> AsyncIterator[bytes]:
            for i in range(0, len(body), 64 * 1024):
                yield body[i : i + 64 * 1024]

        return gen()

    async def test_chunked_too_large(self, product_factory: Any, s3: _FakeS3) -> None:
        """不带 Content-Length 的 chunked 请求体超 6.5MB → 413、零写入（按实际字节计）。"""
        await product_factory.style(style_code="K01")
        body = _multipart(
            [_file_part("files", "K01.png", "image/png", PNG + b"\x00" * MAX_BODY_BYTES)]
        )
        async with _client() as client:
            resp = await client.post(URL, content=self._chunked(body), headers=MP_HEADERS)
        assert resp.status_code == 413
        assert resp.json()["code"] == "IMAGE_BATCH_TOO_LARGE"
        assert s3.puts == []

    async def test_chunked_within_limit(
        self, session: AsyncSession, product_factory: Any, s3: _FakeS3
    ) -> None:
        style = await product_factory.style(style_code="K02")
        body = _multipart([_file_part("files", "K02.png", "image/png", PNG)])
        async with _client() as client:
            resp = await client.post(URL, content=self._chunked(body), headers=MP_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["summary"]["created"] == 1
        assert (await _style(session, style.id)).main_image_key == s3.puts[0]


@pytest.mark.integration
@pytest.mark.asyncio
class TestSingleUploadUnchanged:
    """单张上传改用同一套校验与存储，对外行为与错误文案不变。"""

    @staticmethod
    def _url(style_id: Any) -> str:
        return f"/api/styles/{style_id}/main-image"

    async def test_success_replaces_and_signs(
        self,
        session: AsyncSession,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
    ) -> None:
        await as_role("merchandiser")
        style = await product_factory.style(style_code="U01", main_image_key="old/u01.png")
        async with _client() as client:
            resp = await client.post(
                self._url(style.id), files={"image": ("anything.png", PNG, "image/png")}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["main_image_key"] == s3.puts[0]
        assert body["main_image_url"] == f"https://fake-r2.local/{s3.puts[0]}"
        assert (body["image_url"], body["image_source"]) == (body["main_image_url"], "upload")
        assert s3.deletes == ["old/u01.png"]
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "style.main_image.update",
                    AuditLog.resource_id == str(style.id),
                )
            )
        ).scalar_one()
        assert (audit.before, audit.after) == (
            {"main_image_changed": True},
            {"main_image_changed": True},
        )

    @pytest.mark.parametrize(
        ("filename", "data", "ctype", "message"),
        [
            ("a.gif", b"GIF89a", "image/gif", "主图仅支持 JPG、PNG、WebP 图片"),
            ("a.png", b"", "image/png", "主图文件不能为空"),
            ("a.png", BIG_PNG, "image/png", "主图文件必须小于 300KB"),
            ("a" * 252 + ".png", PNG, "image/png", "主图文件名不能超过 255 个字符"),
            ("a.jpg", b"plain text", "image/jpeg", "主图内容与声明格式不一致"),
        ],
    )
    async def test_validation_messages(
        self,
        product_factory: Any,
        as_role: Callable[[str], Any],
        s3: _FakeS3,
        filename: str,
        data: bytes,
        ctype: str,
        message: str,
    ) -> None:
        await as_role("merchandiser")
        style = await product_factory.style(style_code="U02")
        async with _client() as client:
            resp = await client.post(self._url(style.id), files={"image": (filename, data, ctype)})
        assert resp.status_code == 422
        assert (resp.json()["code"], resp.json()["message"]) == ("INVALID_ATTACHMENT", message)
        assert s3.puts == []
