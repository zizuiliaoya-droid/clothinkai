from copy import deepcopy
import unittest
from contracts import Rejected
import open_release


def worker():
    return {
        "mode": "synthetic",
        "count": 1,
        "registered_tasks_sha256": "a" * 64,
        "expected_tasks_sha256": "a" * 64,
        "queues": ["default", "backup", "crawler", "report"],
        "active_old_workers": 0,
        "unexpected_restarts": 0,
        "oom": False,
        "stable_ticks": [400, 430],
        "intent_persisted": True,
        "image_bound": True,
    }


def beat():
    return {
        "mode": "synthetic",
        "count": 1,
        "pid": 21,
        "expected_pid": 21,
        "format": "dbm.gnu",
        "schedule_sha256": "b" * 64,
        "read_only_opened": True,
        "structure_verified": True,
        "task_names": ["fixture-task-" + str(i) for i in range(7)],
        "proof_sha256": "c" * 64,
        "same_original_store": True,
        "duplicate_jobs": 0,
        "intent_persisted": True,
        "created_files": [],
        "unknown_fields": [],
    }


def opening():
    return {
        "mode": "synthetic",
        "intent_tick": 500,
        "reload_tick": 501,
        "external_ticks": [503, 533],
        "http_statuses": [200, 200],
        "version_matches": [True, True],
        "tls_matches": [True, True],
        "syntax_ok": True,
        "reload_ok": True,
        "new_config_sha256": "d" * 64,
        "observed_config_sha256": "d" * 64,
        "old_workers": 0,
    }


class OpenTests(unittest.TestCase):
    def test_worker_requires_unique_registration_all_queues_and_stability(self):
        for key, value in (
            ("count", 2),
            ("queues", ["default"]),
            ("active_old_workers", 1),
            ("intent_persisted", False),
            ("stable_ticks", [400, 410]),
        ):
            bad = worker()
            bad[key] = value
            with self.assertRaises(Rejected):
                open_release.verify_worker(bad)

    def test_any_worker_execution_is_possible_writes(self):
        result = open_release.verify_worker(worker())
        self.assertIs(result.get("possible_new_writes"), True)

    def test_beat_prefix_file_or_unknown_format_never_sufficient(self):
        good = beat()
        for key, value in (
            ("read_only_opened", False),
            ("format", "unknown"),
            ("created_files", ["schedule.bak"]),
            ("pid", 22),
            ("structure_verified", False),
            ("task_names", ["x"]),
            ("count", 2),
        ):
            bad = deepcopy(good)
            bad[key] = value
            with self.assertRaises(Rejected):
                open_release.verify_beat(bad, good)

    def test_reload_is_not_external_open_proof(self):
        for key, value in (
            ("http_statuses", [503, 200]),
            ("version_matches", [True, False]),
            ("external_ticks", [499, 533]),
            ("observed_config_sha256", "e" * 64),
        ):
            bad = opening()
            bad[key] = value
            with self.assertRaises(Rejected):
                open_release.verify_open(bad)

    def test_open_time_is_observation_interval(self):
        self.assertEqual(
            open_release.verify_open(opening()).get("open_interval"), [503, 533]
        )

    def test_timer_restore_requires_uploader_and_original_certbot_dependencies(self):
        original = {
            "restart": {r: "unless-stopped" for r in ("api", "web", "worker", "beat")},
            "timers": {
                t: {"active": True, "enabled": True, "masked": False}
                for t in ("backup", "renew")
            },
            "certbot_image": "sha256:" + "e" * 64,
            "ops_hash": "f" * 64,
        }
        observed = {
            **deepcopy(original),
            "uploader_import": True,
            "uploader_mount": True,
            "renew_web_path": "/opt/production/nginx-active.conf",
            "acme_ok": True,
            "stable_ticks": [600, 630],
            "running_timer_jobs": 0,
        }
        for key, value in (
            ("uploader_import", False),
            ("uploader_mount", False),
            ("certbot_image", "sha256:" + "9" * 64),
            ("running_timer_jobs", 1),
        ):
            bad = deepcopy(observed)
            bad[key] = value
            with self.assertRaises(Rejected):
                open_release.restore_operations(original, bad)
