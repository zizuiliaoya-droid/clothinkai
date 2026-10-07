"""``build_ip_diagnostics``：用手搭的 Starlette Request 验证组装逻辑（不起应用、不连库）。"""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.requests import Request

from app.modules.security.service import FORWARDING_HEADERS, build_ip_diagnostics, split_xff


def _request(
    headers: list[tuple[str, str]] | None = None,
    client: tuple[str, int] | None = None,
) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/security/ip-diagnostics",
        "query_string": b"",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or [])
        ],
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


class TestClientHost:
    def test_without_client(self) -> None:
        result = build_ip_diagnostics(_request())
        assert result.client_host is None
        assert result.client_host_is_public is False

    def test_private_peer(self) -> None:
        result = build_ip_diagnostics(_request(client=("10.42.0.1", 12345)))
        assert result.client_host == "10.42.0.1"
        assert result.client_host_is_public is False

    def test_public_peer(self) -> None:
        result = build_ip_diagnostics(_request(client=("8.8.8.8", 443)))
        assert result.client_host == "8.8.8.8"
        assert result.client_host_is_public is True


class TestHeaders:
    def test_fixed_order_and_null_when_absent(self) -> None:
        result = build_ip_diagnostics(_request(client=("10.42.0.1", 1)))
        assert [h.name for h in result.headers] == list(FORWARDING_HEADERS)
        assert all(h.value is None for h in result.headers)
        assert result.xff_chain == []

    def test_lookup_is_case_insensitive_and_verbatim(self) -> None:
        result = build_ip_diagnostics(
            _request([("x-real-ip", " 203.0.113.9 "), ("VIA", "1.1 fake-proxy")])
        )
        values = {h.name: h.value for h in result.headers}
        # Starlette 原样保留值（含空白），诊断不做任何清洗
        assert values["X-Real-IP"] == " 203.0.113.9 "
        assert values["Via"] == "1.1 fake-proxy"

    def test_repeated_header_lines_are_joined_in_order(self) -> None:
        result = build_ip_diagnostics(
            _request([("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "8.8.8.8")])
        )
        values = {h.name: h.value for h in result.headers}
        assert values["X-Forwarded-For"] == "1.1.1.1, 8.8.8.8"
        assert [hop.ip for hop in result.xff_chain] == ["1.1.1.1", "8.8.8.8"]


class TestXffSplit:
    def test_segments(self) -> None:
        result = build_ip_diagnostics(
            _request([("X-Forwarded-For", " 8.8.8.8 ,10.42.0.1,, ::ffff:1.1.1.1 , 1.2.3.4:5678")])
        )
        hops = [(h.raw, h.ip, h.is_public) for h in result.xff_chain]
        assert hops == [
            ("8.8.8.8", "8.8.8.8", True),
            ("10.42.0.1", "10.42.0.1", False),
            ("::ffff:1.1.1.1", "1.1.1.1", True),  # IPv4-mapped 归一
            ("1.2.3.4:5678", None, False),  # 带端口解析不了：原样保留 raw
        ]

    @pytest.mark.parametrize("value", [None, "", " , ,"])
    def test_empty(self, value: str | None) -> None:
        assert split_xff(value) == []


class TestEnvironment:
    def test_forwarded_allow_ips_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.42.0.0/16")
        result = build_ip_diagnostics(_request())
        assert result.forwarded_allow_ips_set is True
        assert result.forwarded_allow_ips == "10.42.0.0/16"

    def test_forwarded_allow_ips_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
        result = build_ip_diagnostics(_request())
        assert result.forwarded_allow_ips_set is False
        assert result.forwarded_allow_ips is None

    def test_uvicorn_version_and_server_time(self) -> None:
        before = datetime.now(UTC)
        result = build_ip_diagnostics(_request())
        after = datetime.now(UTC)
        assert result.uvicorn_version == importlib.metadata.version("uvicorn")
        assert result.server_time.tzinfo is not None
        assert before <= result.server_time <= after
