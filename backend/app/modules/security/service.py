"""网络诊断：把「系统看到的客户端 IP」和转发链原样摊开（只读，不碰库、不写审计）。"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from starlette.requests import Request

from app.core.security.client_ip import is_public, normalize_ip
from app.modules.security.schemas import ForwardedHeader, IpDiagnosticsResponse, XffHop

# 回显顺序固定：前端按这个顺序渲染，复制给开发的文本也按这个顺序
FORWARDING_HEADERS: tuple[str, ...] = (
    "X-Forwarded-For",
    "X-Real-IP",
    "Forwarded",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
    "Via",
    "CF-Connecting-IP",
    "True-Client-IP",
)


def _header_value(request: Request, name: str) -> str | None:
    """同名头出现多行时按出现顺序用 ``", "`` 拼接（RFC 7230 列表型头的合并语义）。

    代理在原请求后面另起一行追加 XFF 的情况并不少见，只取第一行会把真实 IP 漏掉。
    """
    values = request.headers.getlist(name)
    if not values:
        return None
    return ", ".join(values)


def split_xff(value: str | None) -> list[XffHop]:
    """按逗号拆 X-Forwarded-For：去首尾空白、跳过空段，每段给出归一结果与是否公网。"""
    if not value:
        return []
    hops: list[XffHop] = []
    for part in value.split(","):
        seg = part.strip()
        if not seg:
            continue
        hops.append(XffHop(raw=seg, ip=normalize_ip(seg), is_public=is_public(seg)))
    return hops


def _uvicorn_version() -> str | None:
    try:
        return version("uvicorn")
    except PackageNotFoundError:
        return None


def build_ip_diagnostics(request: Request) -> IpDiagnosticsResponse:
    """组装诊断结果。

    ``client_host`` 是 uvicorn ``ProxyHeadersMiddleware`` 处理之后的值：只有 TCP 对端在信任
    名单里（默认只有 127.0.0.1）才会被 XFF 改写；请求头部分一律原样回显、不做任何信任判断。
    ``FORWARDED_ALLOW_IPS`` 每次请求现读进程环境变量。
    """
    client_host = request.client.host if request.client else None
    xff = _header_value(request, "X-Forwarded-For")
    allow_ips = os.environ.get("FORWARDED_ALLOW_IPS")
    return IpDiagnosticsResponse(
        client_host=client_host,
        client_host_is_public=is_public(client_host),
        headers=[
            ForwardedHeader(name=name, value=_header_value(request, name))
            for name in FORWARDING_HEADERS
        ],
        xff_chain=split_xff(xff),
        forwarded_allow_ips_set=allow_ips is not None,
        forwarded_allow_ips=allow_ips,
        uvicorn_version=_uvicorn_version(),
        server_time=datetime.now(UTC),
    )


__all__ = ["FORWARDING_HEADERS", "build_ip_diagnostics", "split_xff"]
