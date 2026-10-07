"""客户端 IP 的解析、归一与判定（登录白名单 / 采集 Worker 白名单 / 网络诊断共用）。

四个函数：

- ``parse_ip``：解析成 ``IPv4Address`` / ``IPv6Address``；先去首尾空白，空值、非法值
  （带端口 ``1.2.3.4:80``、带方括号 ``[2001:db8::1]``、网段 ``10.0.0.0/8``）一律返回 None。
  IPv4-mapped IPv6（``::ffff:1.2.3.4``）还原成 IPv4，双栈套接字上同一个客户端才不会有两种写法。
- ``normalize_ip``：``parse_ip`` 的字符串形式（IPv6 小写压缩），解析不了返回 None。
- ``is_public``：是不是公网地址。私网、100.64.0.0/10（运营商级 NAT）、回环、链路本地、
  保留段、文档段、未指定地址、组播都**不算**公网；解析不了也不算。
- ``ip_is_allowed``：按单 IP / CIDR 白名单匹配，从 ``collect/worker_token_service.py``
  原样搬来，行为不变：不去空白、**不做** IPv4-mapped 归一、网段带主机位按 ``strict=False``
  放宽、非法条目跳过、空名单拒绝。

为什么 ``ip_is_allowed`` 不归一：把归一塞进去会让 Worker 白名单对 ``::ffff:x.x.x.x`` 由拒变放，
违反「Worker 行为不变」。生产 uvicorn 绑 ``0.0.0.0``（纯 IPv4 套接字），``client.host``
不会出现 mapped 形态。7f 的登录白名单要先 ``normalize_ip`` 再调 ``ip_is_allowed``。
"""

from __future__ import annotations

from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network


def parse_ip(value: str | None) -> IPv4Address | IPv6Address | None:
    """解析单个 IP；空值 / 非法值返回 None，IPv4-mapped 还原成 IPv4。"""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        addr = ip_address(text)
    except ValueError:
        return None
    if isinstance(addr, IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def normalize_ip(value: str | None) -> str | None:
    """归一后的字符串形式；解析不了返回 None。"""
    addr = parse_ip(value)
    return str(addr) if addr is not None else None


def is_public(value: str | None) -> bool:
    """是否公网地址（先归一再判）。

    ``is_global`` 已排除私网、100.64.0.0/10、回环、链路本地、保留段、文档段；
    组播（224.0.0.0/4、ff00::/8）的 ``is_global`` 可能为 True，但不可能是请求的来源地址，显式排除。
    """
    addr = parse_ip(value)
    if addr is None:
        return False
    return addr.is_global and not addr.is_multicast


def ip_is_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """按单 IP/CIDR 白名单匹配；无效或空白名单一律拒绝。"""
    try:
        address = ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if address in ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


__all__ = ["ip_is_allowed", "is_public", "normalize_ip", "parse_ip"]
