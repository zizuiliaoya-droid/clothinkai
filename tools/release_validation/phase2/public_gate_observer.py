"""Fixed-origin TLS/status-only observer. No cookies, auth or redirect following."""

import http.client
import ssl
import time

from contracts import require
from maintenance import PROBES


def observe_gate():
    return _observe_gate(preflight=False)


def observe_preflight_gate():
    """Read the three public health paths while the existing ERP is still open."""
    return _observe_gate(preflight=True)


def _observe_gate(*, preflight):
    context = ssl.create_default_context()
    started = time.monotonic_ns()
    statuses, expiry = {}, None
    for path in ("/", "/health", "/ready") if preflight else PROBES:
        connection = http.client.HTTPSConnection(
            "47.110.76.9", port=443, timeout=5, context=context
        )
        try:
            connection.connect()
            certificate = connection.sock.getpeercert()
            current_expiry = ssl.cert_time_to_seconds(certificate["notAfter"])
            require(current_expiry - time.time() >= 172800, "tls_expiry")
            require(expiry in (None, current_expiry), "tls_changed_during_probe")
            expiry = current_expiry
            connection.request(
                "GET", path, headers={"Host": "47.110.76.9", "Connection": "close"}
            )
            response = connection.getresponse()
            require(not 300 <= response.status < 400, "http_redirect")
            require(len(response.read(8193)) <= 8192, "http_body_limit")
            statuses[path] = response.status
        finally:
            connection.close()
    return {
        "public_status": statuses,
        "tls_verified": True,
        "certificate_not_after": expiry,
        "started_tick": started,
        "finished_tick": time.monotonic_ns(),
        "gate_verified": False,
        "pending": [
            "acme_challenge_readback",
            "old_nginx_workers_and_connections",
            "external_vantage",
            "open_acceptance_and_ttl",
        ],
    }


def observe_dashboard():
    """Anonymous fixed probes, separate from ERP's all-503 maintenance contract."""
    from contracts import exact, parse_json

    context = ssl.create_default_context()
    statuses = {}
    for path in ("/progress", "/progress/", "/progress/health"):
        connection = http.client.HTTPSConnection(
            "47.110.76.9", port=443, timeout=5, context=context
        )
        try:
            connection.connect()
            expiry = ssl.cert_time_to_seconds(connection.sock.getpeercert()["notAfter"])
            require(expiry - time.time() >= 172800, "tls_expiry")
            connection.request(
                "GET", path, headers={"Host": "47.110.76.9", "Connection": "close"}
            )
            response = connection.getresponse()
            body = response.read(8193)
            require(len(body) <= 8192, "dashboard_body_limit")
            if path == "/progress/health":
                require(
                    response.status == 200
                    and response.getheader("Content-Type", "").split(";", 1)[0]
                    == "application/json",
                    "dashboard_health_type",
                )
                payload = parse_json(body)
                exact(payload, ("service", "ok", "snapshotAvailable"))
                require(
                    payload["service"] == "progress-dashboard"
                    and payload["ok"] is True
                    and payload["snapshotAvailable"] is True,
                    "dashboard_health_identity",
                )
                statuses[path] = {
                    "status": 200,
                    "service": "progress-dashboard",
                    "snapshot_available": True,
                }
            else:
                target = "/progress/" if path == "/progress" else "/progress/login"
                require(
                    response.status == 303 and response.getheader("Location") == target,
                    "dashboard_prefix_redirect",
                )
                statuses[path] = {"status": 303, "location": target}
        finally:
            connection.close()
    return {
        "probes": statuses,
        "tls_verified": True,
        "dashboard_routes_verified": True,
        "full_acceptance": False,
        "pending": ["authenticated_sync_freshness", "external_vantage"],
    }
