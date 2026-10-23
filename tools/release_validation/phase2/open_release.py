"""Worker/beat/open/operations acceptance, no real command implementation."""

from contracts import digest, exact, require, sha
from acceptance import stable_ticks


def verify_worker(facts):
    exact(
        facts,
        (
            "mode",
            "count",
            "registered_tasks_sha256",
            "expected_tasks_sha256",
            "queues",
            "active_old_workers",
            "unexpected_restarts",
            "oom",
            "stable_ticks",
            "intent_persisted",
            "image_bound",
        ),
    )
    sha(facts["registered_tasks_sha256"])
    require(
        facts["mode"] == "synthetic"
        and type(facts["count"]) is int
        and facts["count"] == 1
        and facts["registered_tasks_sha256"] == facts["expected_tasks_sha256"]
        and facts["queues"] == ["default", "backup", "crawler", "report"],
        "worker_registration",
    )
    for key in ("active_old_workers", "unexpected_restarts"):
        require(
            type(facts[key]) is int and facts[key] == 0, "worker_duplicates_or_restarts"
        )
    require(
        facts["oom"] is False
        and facts["intent_persisted"] is True
        and facts["image_bound"] is True,
        "worker_evidence",
    )
    stable_ticks(facts["stable_ticks"])
    return {
        "mode": "synthetic",
        "possible_new_writes": True,
        "receipt_sha256": digest(facts),
        "live_verified": False,
    }


def verify_beat(facts, expected):
    exact(
        facts,
        (
            "mode",
            "count",
            "pid",
            "expected_pid",
            "format",
            "schedule_sha256",
            "read_only_opened",
            "structure_verified",
            "task_names",
            "proof_sha256",
            "same_original_store",
            "duplicate_jobs",
            "intent_persisted",
            "created_files",
            "unknown_fields",
        ),
    )
    require(
        facts["mode"] == "synthetic"
        and type(facts["count"]) is int
        and facts["count"] == 1
        and type(facts["pid"]) is int
        and 1 < facts["pid"] == facts["expected_pid"],
        "beat_process",
    )
    for key in ("schedule_sha256", "proof_sha256"):
        sha(facts[key])
        require(facts[key] == expected[key], "beat_store_binding")
    require(
        facts["format"] in {"dbm.gnu", "dbm.dumb"}
        and facts["format"] == expected["format"],
        "beat_format",
    )
    require(
        type(facts["task_names"]) is list
        and len(facts["task_names"]) == 7
        and len(set(facts["task_names"])) == 7
        and facts["task_names"] == expected["task_names"],
        "beat_tasks",
    )
    for key in (
        "read_only_opened",
        "structure_verified",
        "same_original_store",
        "intent_persisted",
    ):
        require(facts[key] is True, "beat_unverified")
    require(
        type(facts["duplicate_jobs"]) is int
        and facts["duplicate_jobs"] == 0
        and facts["created_files"] == []
        and facts["unknown_fields"] == [],
        "beat_unknown_or_recreated",
    )
    return {
        "mode": "synthetic",
        "receipt_sha256": digest(facts),
        "live_verified": False,
    }


def verify_open(facts):
    exact(
        facts,
        (
            "mode",
            "intent_tick",
            "reload_tick",
            "external_ticks",
            "http_statuses",
            "version_matches",
            "tls_matches",
            "syntax_ok",
            "reload_ok",
            "new_config_sha256",
            "observed_config_sha256",
            "old_workers",
        ),
    )
    sha(facts["new_config_sha256"])
    stable_ticks(facts["external_ticks"])
    require(
        facts["mode"] == "synthetic"
        and facts["syntax_ok"] is True
        and facts["reload_ok"] is True
        and facts["new_config_sha256"] == facts["observed_config_sha256"]
        and type(facts["old_workers"]) is int
        and facts["old_workers"] == 0,
        "open_config",
    )
    require(
        all(type(facts[k]) is int for k in ("intent_tick", "reload_tick"))
        and 0
        <= facts["intent_tick"]
        <= facts["reload_tick"]
        <= facts["external_ticks"][0],
        "open_interval",
    )
    require(
        facts["http_statuses"] == [200, 200]
        and facts["version_matches"] == [True, True]
        and facts["tls_matches"] == [True, True],
        "external_open_unknown",
    )
    return {
        "mode": "synthetic",
        "open_interval": facts["external_ticks"],
        "live_verified": False,
    }


def restore_operations(original, observed):
    exact(original, ("restart", "timers", "certbot_image", "ops_hash"))
    exact(
        observed,
        (
            *original,
            "uploader_import",
            "uploader_mount",
            "renew_web_path",
            "acme_ok",
            "stable_ticks",
            "running_timer_jobs",
        ),
    )
    require(
        all(observed[k] == original[k] for k in original), "operation_baseline_changed"
    )
    require(
        set(original["restart"]) == {"api", "web", "worker", "beat"}
        and all(
            v in {"no", "always", "unless-stopped", "on-failure"}
            for v in original["restart"].values()
        ),
        "restart_contract",
    )
    require(set(original["timers"]) == {"backup", "renew"}, "timer_contract")
    for timer in original["timers"].values():
        exact(timer, ("active", "enabled", "masked"))
        require(all(type(v) is bool for v in timer.values()), "timer_flags")
    require(
        observed["uploader_import"] is True
        and observed["uploader_mount"] is True
        and observed["renew_web_path"] == "/opt/production/nginx-active.conf"
        and observed["acme_ok"] is True
        and type(observed["running_timer_jobs"]) is int
        and observed["running_timer_jobs"] == 0,
        "operation_dependencies",
    )
    stable_ticks(observed["stable_ticks"])
    return {
        "mode": "synthetic",
        "live_verified": False,
        "future_backup_verified": False,
        "future_renew_verified": False,
    }
