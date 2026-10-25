"""Finite replacement/open boundary tests; subprocesses remain mocked locally."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from contracts import Rejected, canonical, digest
from host_app_control import host_compose, internal_api, replace_app
from host_open_control import restore_restart, restore_timers
from tests.test_host_backup_control import Directory
from tests.test_migration_process import NoSocketAsyncCase


class AppTests(NoSocketAsyncCase):
    def test_actual_original_compose_generates_apps_and_all_existing_networks_external(
        self,
    ):
        from sql_policy import parse

        raw = (
            Path(__file__).resolve().parents[1] / "templates/original-compose.json"
        ).read_bytes()
        base = parse(raw)
        spec = SimpleNamespace(
            _tools=(("templates/original-compose.json", raw),),
            data={"source_commit": "a" * 40},
        )

        def facts(spec, journal, step):
            if step == "PREFLIGHT":
                return {
                    "containers": {
                        r: {"image": base["services"]["prod-" + r]["image"]}
                        for r in ("api", "web", "worker", "beat", "db", "redis")
                    }
                }
            if step == "STOP_BEAT":
                return {"schedule": {"format": "dbm.gnu"}}
            return {
                "image": {
                    "id": "sha256:" + ("b" if step == "BUILD_BACKEND" else "c") * 64
                }
            }

        with (
            patch("host_app_control._directory", return_value=123),
            patch("host_app_control._read", return_value=raw),
            patch("host_app_control.os.close"),
            patch("host_app_control.recorded_facts", side_effect=facts),
        ):
            result = host_compose(spec, None)
        self.assertEqual(result["services"]["prod-db"], base["services"]["prod-db"])
        self.assertEqual(
            result["services"]["prod-redis"], base["services"]["prod-redis"]
        )
        self.assertNotIn("healthcheck", result["services"]["prod-api"])
        self.assertTrue(all(v["external"] is True for v in result["networks"].values()))
        self.assertEqual(len(result["services"]["prod-web"]["networks"]), 3)

    async def test_real_readiness_shape_required_no_fixture_shape_fallback(self):
        good = {
            "/health": {"status": "ok"},
            "/ready": {"status": "ok", "checks": {"db": "ok", "redis": "ok"}},
        }
        with patch(
            "host_app_control._capture", AsyncMock(return_value=canonical(good))
        ):
            self.assertEqual(await internal_api("a" * 64), good)
        with patch(
            "host_app_control._capture",
            AsyncMock(return_value=canonical({"/ready": {"status": "ready"}})),
        ):
            with self.assertRaises(Rejected):
                await internal_api("a" * 64)

    async def test_unknown_start_records_owned_new_id_and_never_reports_result(self):
        files = Directory()
        old = {
            "id": "a" * 64,
            "image": "sha256:" + "a" * 64,
            "restart_policy": "no",
            "running": False,
        }
        new = {
            "id": "b" * 64,
            "image": "sha256:" + "b" * 64,
            "restart_policy": "no",
            "running": False,
            "networks": __import__("dashboard_contract").ERP_NETWORKS,
        }
        spec = SimpleNamespace(
            data={"source_commit": "a" * 40, "release_id": "flow78-" + "a" * 12}
        )
        calls = []

        async def capture(argv, **kwargs):
            calls.append(argv)
            self.assertIn("replace-api-intent.json", files.files)
            if "start" in argv:
                self.assertIn("new-api-container.json", files.files)
                raise TimeoutError("fixture")
            return b""

        with (
            patch("host_app_control.require_step"),
            patch("host_app_control.created_app", AsyncMock(return_value=new)),
            patch("host_app_control.recorded_facts"),
            patch("host_app_control.infrastructure", AsyncMock(return_value={})),
            patch(
                "host_app_control.collect",
                AsyncMock(side_effect=[{"facts": old}, {"facts": new}]),
            ),
            patch(
                "host_app_control.stage",
                return_value={"services": {"prod-api": {"image": new["image"]}}},
            ),
            patch("host_app_control._capture", side_effect=capture),
        ):
            with self.assertRaises(TimeoutError):
                await replace_app(
                    spec, SimpleNamespace(directory=files), {"api": old["id"]}, "api"
                )
        self.assertIn("--no-start", calls[0])
        self.assertIn("--no-deps", calls[0])
        self.assertEqual(calls[1][-1], new["id"])
        self.assertNotIn("replace-api-observed.json", files.files)

    async def test_original_restart_hash_tamper_prevents_update(self):
        directory = SimpleNamespace(
            read=lambda name: canonical({"api": {"restart_policy": "always"}})
        )
        with (
            patch("host_open_control.require_step"),
            patch(
                "host_open_control.recorded_facts",
                return_value={"original_sha256": "a" * 64},
            ),
            patch("host_open_control._capture", AsyncMock()) as capture,
        ):
            with self.assertRaises(Rejected):
                await restore_restart(None, SimpleNamespace(directory=directory), {})
        capture.assert_not_awaited()

    async def test_timer_restores_exact_original_inactive_enabled_state(self):
        original = {
            r: {"timer": {"active": False, "enabled": True, "masked": False}}
            for r in ("backup", "renew")
        }
        stopped = {"timer": {"active": False, "enabled": False, "masked": False}}
        enabled = {"timer": {"active": False, "enabled": True, "masked": False}}
        with (
            patch("host_open_control.require_step"),
            patch(
                "host_open_control.recorded_facts",
                return_value={"original_sha256": digest(original)},
            ),
            patch(
                "host_open_control.observe_timer",
                AsyncMock(side_effect=[stopped, enabled, stopped, enabled]),
            ),
            patch("host_open_control._capture", AsyncMock()) as capture,
        ):
            await restore_timers(
                None,
                SimpleNamespace(
                    directory=SimpleNamespace(read=lambda n: canonical(original))
                ),
                {},
            )
        self.assertEqual(
            [c.args[0][1] for c in capture.call_args_list], ["enable", "enable"]
        )
