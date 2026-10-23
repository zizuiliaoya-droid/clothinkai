"""Local internal acceptance for normalized synthetic projections."""

from contracts import AFTER, TARGET, digest, exact, require, sha


def stable_ticks(ticks):
    require(
        type(ticks) is list
        and len(ticks) == 2
        and all(type(v) is int for v in ticks)
        and 0 <= ticks[0] <= ticks[1] - 30,
        "stable_window",
    )


def verify(observed, expected):
    fields = (
        "mode",
        "head",
        "commit",
        "frontend_commit",
        "backend_image",
        "frontend_sha256",
        "db_id",
        "redis_id",
        "network_ids",
        "public_ports",
        "ready",
        "origin",
        "tls_valid",
        "tls_remaining_seconds",
        "app_superuser",
        "app_bypassrls",
        "app_rls_checked",
        "role_access",
        "cache_ttl_seconds",
        "migration_tick",
        "observed_tick",
        "stable_ticks",
        "worker_count",
        "beat_count",
        "gate_closed",
        "k7_errors",
        "k8_amount_delta_minor",
        "new_retro_samples",
        "r33_samples",
        "unknown_fields",
    )
    exact(observed, fields)
    exact(expected, fields)
    for key in ("commit", "frontend_commit"):
        sha(observed[key], 40)
        require(observed[key] == expected["commit"], "source_mismatch")
    for key in ("frontend_sha256", "db_id", "redis_id"):
        sha(observed[key])
        require(observed[key] == expected[key], "identity_mismatch")
    require(observed["backend_image"].startswith("sha256:"), "backend_image")
    sha(observed["backend_image"][7:])
    require(
        observed["backend_image"] == expected["backend_image"]
        and observed["network_ids"] == expected["network_ids"],
        "runtime_identity",
    )
    require(
        observed["mode"] == "synthetic"
        and observed["head"] == AFTER
        and observed["public_ports"] == [80, 443]
        and observed["origin"] == TARGET["origin"],
        "runtime_boundary",
    )
    require(
        observed["ready"] == {"database": "ok", "redis": "ok", "status": "ready"},
        "ready_projection",
    )
    require(
        observed["tls_valid"] is True
        and type(observed["tls_remaining_seconds"]) is int
        and observed["tls_remaining_seconds"] >= 172800,
        "certificate",
    )
    require(
        observed["app_superuser"] is False
        and observed["app_bypassrls"] is False
        and observed["app_rls_checked"] is True,
        "app_privilege",
    )
    require(
        type(expected["role_access"]) is dict
        and expected["role_access"]
        and all(type(v) is bool for v in expected["role_access"].values())
        and observed["role_access"] == expected["role_access"],
        "role_access",
    )
    for key in (
        "cache_ttl_seconds",
        "migration_tick",
        "observed_tick",
        "new_retro_samples",
        "r33_samples",
    ):
        require(type(observed[key]) is int and observed[key] >= 0, "integer_evidence")
    require(
        observed["cache_ttl_seconds"] == expected["cache_ttl_seconds"] >= 300
        and observed["observed_tick"] - observed["migration_tick"]
        >= observed["cache_ttl_seconds"],
        "permission_cache",
    )
    stable_ticks(observed["stable_ticks"])
    require(observed["stable_ticks"][-1] <= observed["observed_tick"], "future_window")
    for key in ("worker_count", "beat_count", "k7_errors", "k8_amount_delta_minor"):
        require(
            type(observed[key]) is int and observed[key] == 0, "internal_acceptance"
        )
    require(
        observed["gate_closed"] is True and observed["unknown_fields"] == [],
        "internal_gate",
    )
    pending = [
        label
        for key, label in (("new_retro_samples", "K7-4"), ("r33_samples", "R33"))
        if observed[key] == 0
    ]
    return {
        "mode": "synthetic",
        "live_verified": False,
        "acceptance_sha256": digest(observed),
        "pending_business_samples": pending,
    }
