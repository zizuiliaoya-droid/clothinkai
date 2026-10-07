"""``app.core.security.client_ip``：IP 归一、公网判定、Worker 白名单行为不变（纯函数，不连库）。

刻意不用 Python 3.12 各补丁版本里定义变过的边缘地址（如 192.0.0.9、2001:1::1），
免得换个镜像补丁号用例就翻。
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

import pytest

from app.core.security import client_ip
from app.core.security.client_ip import ip_is_allowed, is_public, normalize_ip, parse_ip


def _original_ip_is_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """冻结的对照实现，勿改。

    a0f1410 时 ``app/modules/collect/worker_token_service.py:42-54`` 的逐字副本：
    共享实现若被「顺手」改了语义（去空白、归一、strict 网段），对照表会立刻对不上。
    """
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


class TestNormalize:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.2.3.4", "1.2.3.4"),
            (" 1.2.3.4 ", "1.2.3.4"),
            ("::ffff:1.2.3.4", "1.2.3.4"),
            ("::FFFF:8.8.8.8", "8.8.8.8"),
            ("2001:DB8::1", "2001:db8::1"),
        ],
        ids=["ipv4", "strip", "ipv4-mapped", "ipv4-mapped-upper", "ipv6-lower"],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_ip(raw) == expected

    def test_mapped_parses_to_ipv4_object(self) -> None:
        addr = parse_ip("::ffff:10.0.0.1")
        assert addr is not None
        assert addr.version == 4

    @pytest.mark.parametrize(
        "raw",
        [None, "", "  ", "garbage", "1.2.3.4:5678", "[2001:db8::1]", "10.0.0.0/8"],
        ids=["none", "empty", "blank", "garbage", "with-port", "bracketed", "cidr"],
    )
    def test_invalid_returns_none(self, raw: str | None) -> None:
        assert parse_ip(raw) is None
        assert normalize_ip(raw) is None


class TestIsPublic:
    @pytest.mark.parametrize(
        "raw",
        ["8.8.8.8", "1.1.1.1", "114.114.114.114", "2001:4860:4860::8888", "::ffff:8.8.8.8"],
    )
    def test_public(self, raw: str) -> None:
        assert is_public(raw) is True

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("10.42.0.1", id="private-10"),
            pytest.param("172.16.5.4", id="private-172"),
            pytest.param("192.168.1.1", id="private-192"),
            pytest.param("fd00::1", id="private-ula"),
            pytest.param("100.64.0.1", id="cgnat-low"),
            pytest.param("100.127.255.254", id="cgnat-high"),
            pytest.param("127.0.0.1", id="loopback-v4"),
            pytest.param("::1", id="loopback-v6"),
            pytest.param("169.254.10.20", id="link-local-v4"),
            pytest.param("fe80::1", id="link-local-v6"),
            pytest.param("0.0.0.0", id="reserved-this-network"),
            pytest.param("240.0.0.1", id="reserved-240"),
            pytest.param("255.255.255.255", id="reserved-broadcast"),
            pytest.param("::", id="reserved-unspecified-v6"),
            pytest.param("192.0.2.1", id="doc-test-net-1"),
            pytest.param("198.51.100.7", id="doc-test-net-2"),
            pytest.param("203.0.113.9", id="doc-test-net-3"),
            pytest.param("2001:db8::1", id="doc-v6"),
            pytest.param("224.0.0.1", id="multicast-v4"),
            pytest.param("ff02::1", id="multicast-v6"),
            pytest.param("::ffff:10.0.0.1", id="mapped-private"),
            pytest.param("::ffff:100.64.0.1", id="mapped-cgnat"),
            pytest.param(None, id="invalid-none"),
            pytest.param("", id="invalid-empty"),
            pytest.param("garbage", id="invalid-garbage"),
            pytest.param("1.2.3.4:80", id="invalid-with-port"),
        ],
    )
    def test_not_public(self, raw: str | None) -> None:
        assert is_public(raw) is False


class TestWorkerAllowlistUnchanged:
    """Worker 白名单改引用共享实现后，行为与 a0f1410 逐条一致。"""

    @pytest.mark.parametrize(
        ("client", "allowlist", "expected"),
        [
            ("10.0.0.10", ["10.0.0.10"], True),
            ("10.0.1.99", ["10.0.1.0/24"], True),
            ("10.0.1.99", ["10.0.1.7/24"], True),  # 网段带主机位：strict=False 放宽
            ("10.0.2.1", ["10.0.0.10", "10.0.1.0/24"], False),
            ("2001:db8::5", ["2001:db8::/64"], True),
            ("2001:db8:1::5", ["2001:db8::/64"], False),
            ("10.0.0.10", ["2001:db8::/64"], False),
            ("::ffff:10.0.0.10", ["10.0.0.10"], False),  # 不归一，保持原样
            ("", ["10.0.0.10"], False),
            (" 10.0.0.10", ["10.0.0.10"], False),  # 原实现不去空白
            ("not-an-ip", ["10.0.0.10"], False),
            ("10.0.0.10", [], False),
            ("10.0.0.10", ["bad", "10.0.0.1/99", "10.0.0.10"], True),  # 非法条目跳过
            ("10.0.0.10", ["bad"], False),
        ],
    )
    def test_matches_frozen_original(
        self, client: str, allowlist: list[str], expected: bool
    ) -> None:
        assert ip_is_allowed(client, allowlist) == _original_ip_is_allowed(client, allowlist)
        assert ip_is_allowed(client, allowlist) is expected

    def test_worker_service_uses_shared_matcher(self) -> None:
        from app.modules.collect import worker_token_service

        assert worker_token_service.ip_is_allowed is client_ip.ip_is_allowed
