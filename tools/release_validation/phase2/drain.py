"""Complete bounded observations, not ready=0 heuristics or queue mutation."""

from contracts import digest, exact, require, sha

QUEUES = ("default", "backup", "crawler", "report")


def verify(data):
    exact(
        data,
        (
            "transport",
            "producers_frozen",
            "unknown_producers",
            "delay_bound_seconds",
            "intervening_publish_count",
            "windows",
        ),
    )
    transport = data["transport"]
    exact(
        transport,
        ("priority_steps", "unacked_observed", "settings_sha256", "versions_sha256"),
    )
    require(
        transport["priority_steps"] == [0, 3, 6, 9]
        and transport["unacked_observed"] is True,
        "transport_unknown",
    )
    sha(transport["settings_sha256"])
    sha(transport["versions_sha256"])
    require(
        data["producers_frozen"] == ["api", "beat", "callbacks", "manual"]
        and data["unknown_producers"] == 0
        and data["intervening_publish_count"] == 0,
        "producer_not_frozen",
    )
    require(type(data["windows"]) is list and len(data["windows"]) == 2, "two_windows")
    for window in data["windows"]:
        exact(
            window,
            (
                "tick",
                "inspect_complete",
                "workers",
                "beat_processes",
                "api_requests",
                "business_sessions",
                "callbacks",
                "queues",
            ),
        )
        require(
            type(window["tick"]) is int
            and window["tick"] >= 0
            and window["inspect_complete"] is True
            and window["workers"] == 1,
            "worker_unknown",
        )
        require(
            all(
                type(window[k]) is int and window[k] == 0
                for k in (
                    "beat_processes",
                    "api_requests",
                    "business_sessions",
                    "callbacks",
                )
            ),
            "inflight_write",
        )
        exact(window["queues"], QUEUES)
        for queue in window["queues"].values():
            exact(
                queue, ("ready", "active", "reserved", "scheduled", "retry", "unacked")
            )
            require(
                queue["ready"] == [0, 0, 0, 0]
                and all(type(v) is int for v in queue["ready"]),
                "ready_shards",
            )
            require(
                all(
                    type(queue[k]) is int and queue[k] == 0
                    for k in ("active", "reserved", "scheduled", "retry", "unacked")
                ),
                "queued_work",
            )
    first, last = data["windows"]
    require(
        type(data["delay_bound_seconds"]) is int
        and 0 <= data["delay_bound_seconds"] <= 600
        and last["tick"] - first["tick"] >= max(30, data["delay_bound_seconds"]),
        "unstable_window",
    )
    return {
        "last_empty_tick": last["tick"],
        "observation_sha256": digest(data),
        "mode": "synthetic",
    }


def verify_stopped(data, api_id):
    exact(
        data,
        (
            "graceful",
            "exit_confirmed",
            "children",
            "business_sessions",
            "api_running",
            "api_id",
        ),
    )
    require(
        data["graceful"] is True
        and data["exit_confirmed"] is True
        and data["children"] == 0
        and data["business_sessions"] == 0
        and data["api_running"] is True
        and data["api_id"] == api_id,
        "worker_exit_or_backup_api_unknown",
    )
    return {"stopped": True, "backup_api_preserved": True, "mode": "synthetic"}
