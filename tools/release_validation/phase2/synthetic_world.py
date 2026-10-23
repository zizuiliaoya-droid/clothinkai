"""Stateful synthetic fixture; never a live execution adapter."""

from copy import deepcopy
import hashlib
import os

from contracts import (
    AFTER,
    BEFORE,
    STEPS,
    canonical,
    digest,
    exact,
    parse_json,
    require,
)
from journal import regular, sandbox_root, sync_parent
from state_machine import ActionSpec, fixture_receipt
from adapters import SyntheticAdapter


def initial(spec):
    apps = {
        role: {
            "id": "fixture-old-" + role,
            "version": "old066",
            "running": True,
            "restart": "unless-stopped",
            "count": 1,
        }
        for role in ("api", "web", "worker", "beat")
    }
    return {
        "mode": "synthetic",
        "live_enabled": False,
        "deployable": False,
        "history": [],
        "tick": 0,
        "source_commit": spec.data["source_commit"],
        "schema": BEFORE,
        "gate": "open-old",
        "apps": apps,
        "infrastructure": {
            "db_id": "fixture-db-original",
            "redis_id": "fixture-redis-original",
            "db_volume": "fixture-original-pg18",
            "network_id": "fixture-original-internal",
        },
        "timers": {
            t: {"active": True, "enabled": True, "masked": False, "jobs": 0}
            for t in ("backup", "renew")
        },
        "producers": ["api", "callbacks", "manual"],
        "requests": 2,
        "sessions": 0,
        "queue": {"default": 3, "backup": 2, "crawler": 1, "report": 4},
        "unacked": 0,
        "database": {
            "old_write_count": 0,
            "new_write_count": 0,
            "rows": [
                {
                    "id": "fixture-normal",
                    "kind": "normal",
                    "retro": "已完成",
                    "completed": None,
                },
                {
                    "id": "fixture-recall",
                    "kind": "recall",
                    "retro": "未开始",
                    "completed": None,
                },
                {
                    "id": "fixture-draft",
                    "kind": "draft",
                    "retro": "待确认",
                    "completed": None,
                },
                {
                    "id": "fixture-published",
                    "kind": "published",
                    "retro": "未开始",
                    "completed": None,
                },
            ],
            "legacy_confirmed": None,
            "scope_roles": [],
            "events": [],
        },
        "scheduler": {
            "identity": "fixture-original-scheduler",
            "format": "dbm.gnu",
            "readable": True,
            "task_names": ["fixture-task-" + str(i) for i in range(7)],
            "created_files": [],
        },
        "images": {},
        "prepared": False,
        "preflight": False,
        "freeze_tick": None,
        "backup": None,
        "restore": None,
        "migration_tick": None,
        "migration_count": 0,
        "schema_verified": False,
        "internal_accepted": False,
        "worker_verified": False,
        "jobs_verified": False,
        "open_verified": False,
        "possible_writes": False,
        "ops_dependencies": {
            "uploader_mount": True,
            "uploader_import": True,
            "certbot_image": "fixture-original-certbot",
            "renew_path": "/opt/production/nginx-active.conf",
        },
        "business_pending": ["K7-4", "R33"],
        "future_timer_runs_verified": False,
    }


class World:
    def __init__(self, root, spec):
        self.root, self.spec = sandbox_root(root), spec
        self.path = self.root / "world.json"
        regular(self.path)
        require(not self.path.exists(), "world_already_exists_reconcile_only")
        self.data = initial(spec)
        self.original = deepcopy(self.data)
        self.last = None

    def _write_new(self, name, raw):
        target = self.root / name
        regular(target)
        require(not target.exists(), "fixture_artifact_exists")
        with target.open("xb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        sync_parent(self.root)
        return hashlib.sha256(raw).hexdigest()

    def _persist(self):
        pending = self.root / "world.pending"
        self._write_new("world.pending", canonical(self.data))
        regular(self.path)
        os.replace(pending, self.path)
        sync_parent(self.root)

    def _frozen(self):
        d = self.data
        return (
            d["gate"] == "closed"
            and not d["producers"]
            and d["requests"] == d["sessions"] == d["unacked"] == 0
            and not any(d["queue"].values())
            and not d["apps"]["worker"]["running"]
            and not d["apps"]["beat"]["running"]
        )

    def _invariants(self):
        d = self.data
        require(
            d["infrastructure"] == self.original["infrastructure"],
            "infrastructure_changed",
        )
        require(d["history"] == list(STEPS[: len(d["history"])]), "world_order")
        require(
            d["scheduler"] == self.original["scheduler"],
            "scheduler_replaced_or_recreated",
        )
        require(
            all(
                v["count"] in (0, 1) and v["running"] == (v["count"] == 1)
                for v in d["apps"].values()
            ),
            "duplicate_app",
        )
        require(d["migration_count"] <= 1, "migration_repeated")
        require(
            d["live_enabled"] is False and d["deployable"] is False, "live_disabled"
        )

    def _internal_acceptance(self):
        from acceptance import verify

        d = self.data
        require(
            d["schema_verified"]
            and all(
                d["apps"][r]["version"] == self.spec.data["source_commit"]
                and d["apps"][r]["running"]
                for r in ("api", "web")
            ),
            "new_apps_missing",
        )
        # Explicit synthetic elapsed interval, no wall-clock claim or actual role probe.
        d["tick"] = max(d["tick"], d["migration_tick"] + 300)
        projection = {
            "mode": "synthetic",
            "head": d["schema"],
            "commit": d["apps"]["api"]["version"],
            "frontend_commit": d["apps"]["web"]["version"],
            "backend_image": d["images"]["backend"],
            "frontend_sha256": "c" * 64,
            "db_id": "1" * 64,
            "redis_id": "2" * 64,
            "network_ids": {"back": "3" * 64},
            "public_ports": [80, 443],
            "ready": {"status": "ready", "database": "ok", "redis": "ok"},
            "origin": self.spec.data["target"]["origin"],
            "tls_valid": True,
            "tls_remaining_seconds": 172800,
            "app_superuser": False,
            "app_bypassrls": False,
            "app_rls_checked": True,
            "role_access": {"pr_manager": True, "fixture_reader": False},
            "cache_ttl_seconds": 300,
            "migration_tick": d["migration_tick"],
            "observed_tick": d["tick"],
            "stable_ticks": [d["tick"] - 30, d["tick"]],
            "worker_count": d["apps"]["worker"]["count"],
            "beat_count": d["apps"]["beat"]["count"],
            "gate_closed": d["gate"] == "closed",
            "k7_errors": 0,
            "k8_amount_delta_minor": 0,
            "new_retro_samples": 0,
            "r33_samples": 0,
            "unknown_fields": [],
        }
        d["business_pending"] = verify(projection, projection)[
            "pending_business_samples"
        ]

    def apply(self, action):
        require(
            type(action) is ActionSpec and action.input_sha256 == self.spec.sha256,
            "action_binding",
        )
        d = self.data
        self._invariants()
        require(
            len(d["history"]) < len(STEPS) and action.name == STEPS[len(d["history"])],
            "world_action_order",
        )
        name = action.name
        before = deepcopy(d)
        d["tick"] += 30
        if name == "PREPARE":
            self._write_new(
                "prepared.synthetic.json",
                canonical({"source": d["source_commit"], "live_enabled": False}),
            )
            d["prepared"] = True
        elif name in {"BUILD_BACKEND", "BUILD_FRONTEND"}:
            require(d["prepared"], "package_missing")
            kind = name.split("_")[1].lower()
            d["images"][kind] = "sha256:" + digest(
                {"kind": kind, "source": d["source_commit"]}
            )
        elif name == "PREFLIGHT":
            require(
                set(d["images"]) == {"backend", "frontend"} and d["schema"] == BEFORE,
                "preflight",
            )
            d["preflight"] = True
        elif name == "FREEZE_TIMERS":
            require(
                d["preflight"] and all(t["jobs"] == 0 for t in d["timers"].values()),
                "timer_running",
            )
            for timer in d["timers"].values():
                timer.update(active=False, enabled=False)
        elif name == "DISABLE_RESTART":
            require(
                all(not t["active"] and not t["enabled"] for t in d["timers"].values()),
                "timers_unfrozen",
            )
            for app in d["apps"].values():
                app["restart"] = "no"
        elif name == "CLOSE_GATE":
            require(
                all(a["restart"] == "no" for a in d["apps"].values()),
                "restart_not_disabled",
            )
            self._write_new("old-gate.synthetic.conf", b"fixture-old-open\n")
            self._write_new(
                "closed-gate.synthetic.conf", b"fixture-all-routes-503-acme-static\n"
            )
            d["gate"] = "closed"
        elif name == "STOP_BEAT":
            require(d["gate"] == "closed", "gate_open")
            d["apps"]["beat"].update(running=False, count=0)
        elif name == "FREEZE_PRODUCERS":
            require(
                not d["apps"]["beat"]["running"] and d["gate"] == "closed",
                "beat_or_gate",
            )
            d["producers"] = []
        elif name == "DRAIN":
            require(
                not d["producers"]
                and not d["apps"]["beat"]["running"]
                and d["apps"]["worker"]["running"],
                "drain_producers",
            )
            d["database"]["old_write_count"] += sum(d["queue"].values()) + d["requests"]
            d["queue"] = dict.fromkeys(d["queue"], 0)
            d["requests"] = 0
            require(d["sessions"] == d["unacked"] == 0, "drain_unknown")
            d["drain_ticks"] = [d["tick"], d["tick"] + 30]
            d["tick"] += 30
        elif name == "STOP_WORKER":
            require(
                not any(d["queue"].values()) and d["requests"] == d["unacked"] == 0,
                "not_drained",
            )
            d["apps"]["worker"].update(running=False, count=0)
        elif name == "FREEZE_VERIFY":
            require(
                self._frozen() and d["apps"]["api"]["running"], "writers_not_frozen"
            )
            d["freeze_tick"] = d["tick"]
        elif name == "BACKUP":
            require(
                self._frozen()
                and d["apps"]["api"]["running"]
                and d["freeze_tick"] is not None,
                "old_api_backup_dependency",
            )
            raw = canonical(d["database"])
            sha = self._write_new("database.synthetic.dump", raw)
            self._write_new(
                "database.synthetic.sidecar.json",
                canonical({"sha256": sha, "bytes": len(raw)}),
            )
            d["backup"] = {
                "sha256": sha,
                "tick": d["tick"],
                "database_sha256": digest(d["database"]),
                "api_id": d["apps"]["api"]["id"],
            }
        elif name == "RESTORE":
            require(
                self._frozen()
                and d["apps"]["api"]["running"]
                and d["backup"] is not None,
                "restore_prerequisite",
            )
            dump = self.root / "database.synthetic.dump"
            regular(dump)
            raw = dump.read_bytes()
            require(
                hashlib.sha256(raw).hexdigest() == d["backup"]["sha256"],
                "backup_corruption",
            )
            restored = parse_json(raw)
            require(restored == d["database"], "restore_dataset")
            self._write_new("restored.synthetic.json", canonical(restored))
            d["restore"] = {
                "dump_sha256": d["backup"]["sha256"],
                "network": "none",
                "schema": BEFORE,
                "tick": d["tick"],
            }
        elif name == "STOP_API":
            require(
                d["restore"] is not None and self._frozen(),
                "api_stopped_before_restore",
            )
            d["apps"]["api"].update(running=False, count=0)
        elif name == "MIGRATE":
            require(
                self._frozen()
                and not d["apps"]["api"]["running"]
                and d["schema"] == BEFORE
                and d["restore"]["dump_sha256"] == d["backup"]["sha256"]
                and digest(d["database"]) == d["backup"]["database_sha256"],
                "migration_prerequisite",
            )
            for row in d["database"]["rows"]:
                if row["kind"] in {"draft", "published"}:
                    row["retro"] = "待复盘"
                if row["kind"] in {"normal", "recall"}:
                    row["completed"] = d["tick"]
            d["database"]["scope_roles"] = ["pr_manager"]
            d.update(
                schema=AFTER,
                migration_tick=d["tick"],
                migration_count=d["migration_count"] + 1,
            )
        elif name == "VERIFY_SCHEMA":
            require(
                d["schema"] == AFTER
                and d["database"]["legacy_confirmed"] is None
                and d["database"]["scope_roles"] == ["pr_manager"]
                and d["database"]["events"] == [],
                "migration_effects",
            )
            require(
                {r["id"] for r in d["database"]["rows"] if r["completed"] is not None}
                == {"fixture-normal", "fixture-recall"},
                "migration_backfill_set",
            )
            require(
                all(
                    r["retro"] == "待复盘"
                    for r in d["database"]["rows"]
                    if r["kind"] in {"draft", "published"}
                ),
                "mapping_set",
            )
            d["schema_verified"] = True
        elif name in {"REPLACE_API", "REPLACE_WEB"}:
            require(
                d["schema_verified"]
                and d["gate"] == "closed"
                and d["apps"]["worker"]["count"] == d["apps"]["beat"]["count"] == 0,
                "switch_boundary",
            )
            role = name.split("_")[1].lower()
            d["apps"][role].update(
                id="fixture-new-" + role,
                version=d["source_commit"],
                running=True,
                count=1,
            )
        elif name == "ACCEPT_INTERNAL":
            self._internal_acceptance()
            d["internal_accepted"] = True
        elif name == "START_WORKER":
            require(
                d["internal_accepted"] and d["gate"] == "closed",
                "worker_before_acceptance",
            )
            d["apps"]["worker"].update(
                id="fixture-new-worker",
                version=d["source_commit"],
                running=True,
                count=1,
            )
            d["possible_writes"] = True
            d["database"]["new_write_count"] += 1
        elif name == "VERIFY_WORKER":
            require(
                d["apps"]["worker"]["count"] == 1
                and d["apps"]["worker"]["version"] == d["source_commit"]
                and set(d["queue"]) == {"default", "backup", "crawler", "report"},
                "worker_unverified",
            )
            d["worker_verified"] = True
        elif name == "START_BEAT":
            require(
                d["worker_verified"]
                and d["scheduler"]["readable"]
                and d["scheduler"]["format"] in {"dbm.gnu", "dbm.dumb"},
                "beat_unverified",
            )
            d["apps"]["beat"].update(
                id="fixture-new-beat", version=d["source_commit"], running=True, count=1
            )
            d["possible_writes"] = True
        elif name == "VERIFY_JOBS":
            require(
                d["apps"]["worker"]["count"] == d["apps"]["beat"]["count"] == 1,
                "jobs_duplicate",
            )
            d["jobs_verified"] = True
        elif name == "OPEN_GATE":
            require(
                d["internal_accepted"] and d["jobs_verified"] and d["gate"] == "closed",
                "open_before_acceptance",
            )
            self._write_new("open-gate.synthetic.conf", b"fixture-new-release-open\n")
            d["gate"] = "open-new"
            d["possible_writes"] = True
        elif name == "VERIFY_OPEN":
            require(
                d["gate"] == "open-new"
                and d["apps"]["web"]["version"] == d["source_commit"],
                "open_not_verified",
            )
            d["open_interval"] = [d["tick"], d["tick"] + 30]
            d["tick"] += 30
            d["open_verified"] = True
        elif name == "RESTORE_RESTART":
            require(d["open_verified"], "restart_before_open_verified")
            for role, app in d["apps"].items():
                app["restart"] = self.original["apps"][role]["restart"]
        elif name == "RESTORE_TIMERS":
            require(
                d["ops_dependencies"] == self.original["ops_dependencies"]
                and all(a["restart"] == "unless-stopped" for a in d["apps"].values()),
                "operation_dependency_changed",
            )
            d["timers"] = deepcopy(self.original["timers"])
        elif name == "VERIFY_COMPLETE":
            require(
                d["open_verified"]
                and d["timers"] == self.original["timers"]
                and d["possible_writes"],
                "incomplete",
            )
        d["history"].append(name)
        self._invariants()
        self._persist()
        self.last = {
            "action_id": action.action_id,
            "before_sha256": digest(before),
            "after_sha256": digest(d),
            "sequence": len(d["history"]),
            "mode": "synthetic",
        }

    def observe(self, action):
        require(
            self.last is not None and self.last["action_id"] == action.action_id,
            "world_observation",
        )
        return deepcopy(self.last)

    def validate(self, name, facts, state):
        exact(facts, ("action_id", "before_sha256", "after_sha256", "sequence", "mode"))
        require(
            facts == self.last
            and self.data["history"] == list(state.completed) + [name],
            "world_receipt_binding",
        )
        regular(self.path)
        require(
            parse_json(self.path.read_bytes()) == self.data
            and digest(self.data) == facts["after_sha256"],
            "world_persistence",
        )
        self._invariants()


class WorldAdapter(SyntheticAdapter):
    def __init__(self, root, spec):
        super().__init__(root, spec, {})
        self.world = World(root, spec)

    def execute(self, action):
        self.world.apply(action)
        super().execute(action)

    def observe(self, action):
        self.events.append(action.name + ":observe")
        return fixture_receipt(self.spec, action, self.world.observe(action))
