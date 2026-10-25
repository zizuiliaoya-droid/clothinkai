"""Synthetic/static dashboard release boundaries. Never runs Docker or HTTP."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compose_candidate as compose
import dashboard_contract as board
import maintenance
from contracts import Rejected
from fixed_host_transport import command, validate_readonly
from journal import Journal
from public_gate_observer import observe_dashboard, observe_gate


def old_active():
    raw = (
        Path(board.__file__)
        .with_name("templates")
        .joinpath("nginx-release-open.conf")
        .read_bytes()
    )
    # The successor adds HTTPS ACME; reconstruct the separately pinned old
    # active file, retaining its original HTTP ACME route and exact hash.
    acme = b"    location ^~ /.well-known/acme-challenge/ { root /var/www/acme; default_type text/plain; try_files $uri =404; }\n"
    assert raw.count(acme) == 2
    before, after = raw.split(b"    listen 443 ssl;", 1)
    raw = before + b"    listen 443 ssl;" + after.replace(acme, b"", 1)
    routes = board.route_bytes()
    return raw.replace(routes, b"").replace(
        b"    location /api/ {", routes + b"    location /api/ {"
    )


def base_compose():
    # Independent synthetic fixture; avoid the synthetic-suite global process ban.
    services = {}
    for index, role in enumerate(("db", "redis", "api", "web", "worker", "beat"), 1):
        volumes = [compose.R + "/build/control:/opt/production:ro"]
        if role != "web":
            volumes += [compose.R + "/backups:/run/backups:ro"]
        services["prod-" + role] = {
            "image": "sha256:" + str(index) * 64,
            "restart": "unless-stopped",
            "volumes": volumes,
            "networks": {"back": {"aliases": ["prod-" + role]}},
            "healthcheck": {"disable": True},
        }
    services["prod-web"]["networks"]["egress"] = {}
    return {
        "name": "clothinkai-production-20261018",
        "services": services,
        "networks": {
            "back": {"name": "clothinkai-prod-internal-20261018", "internal": True},
            "egress": {"name": "clothinkai-prod-egress-20261018"},
        },
    }


def projections(web_id=board.OLD_WEB_ID):
    network = {
        "id": board.NETWORK_ID,
        "name": board.NETWORK,
        "internal": True,
        "driver": "bridge",
        "scope": "local",
        "members": {board.BOARD_ID: board.BOARD, web_id: board.WEB},
    }
    container = {
        "id": board.BOARD_ID,
        "image": board.BOARD_IMAGE,
        "networks": {board.NETWORK: board.NETWORK_ID},
        "aliases": [board.BOARD, "progress-dashboard"],
    }
    return network, container


class DashboardTests(unittest.TestCase):
    def test_host_atomic_switch_pins_exact_templates(self):
        from host_web_control import TEMPLATE_PINS

        for name, expected in TEMPLATE_PINS.items():
            raw = (
                Path(board.__file__).with_name("templates").joinpath(name).read_bytes()
            )
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)
            board.validate_routes(raw)

    def test_all_templates_use_exact_captured_active_tmp_paths(self):
        original = board.original_active(old_active())
        expected = [
            b"  client_body_temp_path /tmp/client;",
            b"  proxy_temp_path /tmp/proxy;",
            b"  fastcgi_temp_path /tmp/fastcgi;",
            b"  uwsgi_temp_path /tmp/uwsgi;",
            b"  scgi_temp_path /tmp/scgi;",
        ]
        self.assertEqual(
            [line for line in original.splitlines() if b"_temp_path " in line], expected
        )
        for name in ("maintenance", "release-closed", "release-open"):
            raw = (
                Path(board.__file__)
                .with_name("templates")
                .joinpath("nginx-" + name + ".conf")
                .read_bytes()
            )
            self.assertEqual(
                [line for line in raw.splitlines() if b"_temp_path " in line], expected
            )
            self.assertIn(b"pid /tmp/nginx.pid;", raw)
            self.assertNotIn(b"/var/cache/nginx", raw)

    def test_three_templates_keep_exact_routes_and_no_startup_board_dns(self):
        for name in ("maintenance", "release-closed", "release-open"):
            raw = (
                Path(board.__file__)
                .with_name("templates")
                .joinpath("nginx-" + name + ".conf")
                .read_bytes()
            )
            board.validate_routes(raw)
            self.assertNotIn(b"proxy_pass http://progress-dashboard", raw)
            self.assertEqual(raw.count(b"resolver 127.0.0.11 valid=30s ipv6=off;"), 2)
            self.assertEqual(
                raw.count(b"proxy_pass http://$progress_backend$request_uri;"), 2
            )
            if name != "release-open":
                self.assertEqual(raw.count(b'return 503 "Maintenance'), 2)
        # This is a syntax/contract assertion, not evidence of a running nginx.

    def test_close_open_restore_preserves_current_active_and_rejects_old_1694(self):
        original = old_active()
        self.assertEqual(hashlib.sha256(original).hexdigest(), board.ACTIVE_SHA)
        closed = (
            Path(board.__file__)
            .with_name("templates")
            .joinpath("nginx-maintenance.conf")
            .read_bytes()
        )
        with tempfile.TemporaryDirectory(prefix="flow78-phase2-fixture-") as folder:
            root = Path(folder)
            (root / "nginx-active.conf").write_bytes(original)
            maintenance.close_dashboard_fixture(Journal(root), original, closed)
            self.assertEqual((root / "old-active.conf").read_bytes(), original)
            self.assertEqual(maintenance.restore_dashboard_bytes(original), original)
            board.validate_routes(closed)
        with self.assertRaises(Rejected):
            maintenance.restore_dashboard_bytes(
                original.replace(board.route_bytes(), b"")
            )
        with self.assertRaises(Rejected):
            board.validate_routes(original + board.route_bytes())

    def test_new_and_old_web_only_use_existing_external_network(self):
        base = base_compose()
        new = compose.generate(
            base, "a" * 40, "sha256:" + "a" * 64, "sha256:" + "b" * 64, "dbm.dumb"
        )
        restored = compose.recovery_compose(base)
        for result in (new, restored):
            self.assertEqual(result["networks"][board.NETWORK], board.EXTERNAL)
            self.assertEqual(
                set(result["services"]["prod-web"]["networks"]),
                {"back", "egress", board.NETWORK},
            )
            for role in ("api", "worker", "beat", "db", "redis"):
                self.assertEqual(
                    result["services"]["prod-" + role]["networks"],
                    base["services"]["prod-" + role]["networks"],
                )
        self.assertEqual(
            restored["services"]["prod-web"]["image"],
            base["services"]["prod-web"]["image"],
        )
        self.assertIn(
            "web-control:/opt/production:ro",
            " ".join(new["services"]["prod-web"]["volumes"]),
        )
        self.assertNotIn(board.NETWORK, base["networks"])

    def test_compose_rejects_managed_network_and_other_member_and_lost_mount(self):
        for mutate in (
            lambda b: b["networks"].update({board.NETWORK: {"name": board.NETWORK}}),
            lambda b: b["services"]["prod-api"]["networks"].update({board.NETWORK: {}}),
            lambda b: b["services"]["prod-web"].update(volumes=[]),
        ):
            base = base_compose()
            mutate(base)
            with self.assertRaises(Rejected):
                compose.generate(
                    base,
                    "a" * 40,
                    "sha256:" + "a" * 64,
                    "sha256:" + "b" * 64,
                    "dbm.dumb",
                )

    def test_rebuild_and_restore_require_exact_network_id_and_members(self):
        for web_id in (board.OLD_WEB_ID, "a" * 64):
            network, container = projections(web_id)
            self.assertTrue(board.verify_members(network, container, web_id))
            for mutated in (
                dict(network, id="b" * 64),
                dict(network, members={**network["members"], "c" * 64: "unexpected"}),
            ):
                with self.assertRaises(Rejected):
                    board.verify_members(mutated, container, web_id)
            container["networks"]["clothinkai-prod-internal-20261018"] = "d" * 64
            with self.assertRaises(Rejected):
                board.verify_members(network, container, web_id)

    def test_connect_reply_unknown_never_replays_or_releases_lock(self):
        network, container = projections()
        result = board.reconcile_connect(
            network, container, board.OLD_WEB_ID, intent_persisted=True
        )
        self.assertFalse(result["repeat_connect"])
        self.assertFalse(result["release_lock"])
        del network["members"][board.OLD_WEB_ID]
        with self.assertRaises(Rejected):
            board.reconcile_connect(
                network, container, board.OLD_WEB_ID, intent_persisted=True
            )

    def test_finite_network_collectors_no_environment_or_mutation(self):
        for op in ("dashboard_network", "dashboard_board"):
            validate_readonly(op, "web")
            argv = command(op, "web")
            self.assertNotIn(".Config.Env", " ".join(argv))
            self.assertIn(argv[-1], (board.NETWORK_ID, board.BOARD_ID))
        with self.assertRaises(Rejected):
            command("dashboard_network", "api")
        with self.assertRaises(Rejected):
            command("network_connect", "web")

    def test_board_paths_are_not_erp_503_probes(self):
        self.assertTrue(
            all(not path.startswith("/progress") for path in maintenance.PROBES)
        )
        for path in ("/progress", "/progress/health", "/%70rogress/"):
            with self.assertRaisesRegex(Rejected, "dashboard_separate_probe"):
                maintenance.expected_status(path)
        self.assertEqual(maintenance.expected_status("/progression"), 503)

    def test_fake_http_health_json_redirect_and_board_failure_are_separate(self):
        class Socket:
            def getpeercert(self):
                return {"notAfter": "Oct 23 00:00:00 2030 GMT"}

        class Connection:
            mode = "up"

            def __init__(self, host, **kwargs):
                assert host == "47.110.76.9" and kwargs["context"].check_hostname
                self.sock = Socket()

            def connect(self):
                pass

            def close(self):
                pass

            def request(self, method, path, headers):
                self.path = path

            def getresponse(self):
                return self

            @property
            def status(self):
                if not self.path.startswith("/progress"):
                    return 503
                if self.mode == "down":
                    return 502
                return 200 if self.path.endswith("health") else 303

            def getheader(self, name, default=None):
                if name == "Content-Type":
                    return "text/html" if self.mode == "spa" else "application/json"
                return "/progress/" if self.path == "/progress" else "/progress/login"

            def read(self, limit):
                return json.dumps(
                    {
                        "service": "progress-dashboard",
                        "ok": True,
                        "snapshotAvailable": True,
                    }
                ).encode()

        with patch("public_gate_observer.http.client.HTTPSConnection", Connection):
            self.assertTrue(observe_dashboard()["dashboard_routes_verified"])
            for mode in ("down", "spa"):
                Connection.mode = mode
                with self.assertRaises(Rejected):
                    observe_dashboard()
                self.assertEqual(set(observe_gate()["public_status"].values()), {503})


if __name__ == "__main__":
    unittest.main()
