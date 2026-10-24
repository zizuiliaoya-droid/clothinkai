"""Fixed-origin TLS/status-only observer. No cookies, auth or redirect following."""

import http.client
import ssl
import time

from contracts import require
from maintenance import PROBES


def observe_gate():
    context = ssl.create_default_context()
    started = time.monotonic_ns()
    statuses, expiry = {}, None
    for path in PROBES:
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
