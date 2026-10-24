"""Pure contract checks; these do not claim Linux, PostgreSQL or Docker execution."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class LinuxContractTests(unittest.TestCase):
    def test_frozen_dependency_in_renamed_ci_sibling_layout(self):
        import importlib.util
        import tempfile

        from phase1_package_binding import PACKAGE_PINS_FILE, frozen_contract
        from sql_policy import parse

        original = frozen_contract().HERE
        with tempfile.TemporaryDirectory(prefix="flow78-layout-fixture-") as temporary:
            root = Path(temporary)
            phase1, phase2 = root / "phase1", root / "phase2"
            phase1.mkdir()
            phase2.mkdir()
            raw = (original / PACKAGE_PINS_FILE).read_bytes()
            for name in parse(raw)["files"]:
                (phase1 / name).write_bytes((original / name).read_bytes())
            (phase1 / PACKAGE_PINS_FILE).write_bytes(raw)
            (phase1 / "implementation-pins.json").write_bytes(
                (original / "implementation-pins.json").read_bytes()
            )
            target = phase2 / "phase1_package_binding.py"
            target.write_bytes(
                (Path(__file__).resolve().parents[1] / target.name).read_bytes()
            )
            spec = importlib.util.spec_from_file_location("ci_layout_binding", target)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.frozen_contract().HERE, phase1)
            finally:
                sys.modules.pop(spec.name, None)

    def test_only_fixed_ci_target_and_nonce(self):
        from sql_policy import SqlRejected, ci_config

        self.assertIn("@prod-db:5432/", ci_config()["DATABASE_URL_BYPASS"])
        from linux_transport import ci_root

        self.assertEqual(
            ci_root("a" * 32).as_posix(), "/tmp/flow78-linux-ci-" + "a" * 32
        )
        for value in ("", "../production", "a" * 31, "g" * 32):
            with self.assertRaises(SqlRejected):
                ci_root(value)

    def test_closed_tool_bundle_includes_transitive_imports_and_templates(self):
        from phase1_package_binding import tool_bundle

        bundle = tool_bundle()
        for name in (
            "linux_alembic_runtime.py",
            "linux_transport.py",
            "sql_policy.py",
            "sql_migration_driver.py",
            "migration_process.py",
        ):
            self.assertIn(name, bundle)
        self.assertTrue(any(name.startswith("templates/") for name in bundle))
        self.assertFalse(any(name.startswith("tests/") for name in bundle))

    def test_docker_commands_cannot_be_argv_or_mutable_tag(self):
        from linux_transport import docker_command
        from sql_policy import SqlRejected

        command = docker_command("inspect_old_api")
        self.assertIn("clothinkai-prod-api-20261018", command)
        for name in ("rm -rf /", "exec", ["sh", "-c", "true"]):
            with self.assertRaises(SqlRejected):
                docker_command(name)

    def test_live_factory_still_refuses(self):
        from contracts import Rejected
        from linux_adapter import live_factory

        with self.assertRaises(Rejected):
            live_factory({"live_enabled": True})

    def test_backend_projection_excludes_query_and_credentials(self):
        from linux_collectors import backend_projection

        row = {
            "pid": 42,
            "backend_start": "1700000000.123456",
            "application_name": "flow78-" + "a" * 32,
            "state": "idle",
            "query": "CUSTOMER SECRET",
        }
        self.assertNotIn("CUSTOMER", str(backend_projection(row)))
        self.assertEqual(backend_projection(row)["pid"], 42)

    def test_unknown_collection_is_pending_never_success(self):
        from linux_collectors import pending

        p = pending("external_producers")
        self.assertIs(p["verified"], False)
        self.assertEqual(p["status"], "pending")

    def test_actual_engine_listener_removal_on_failure(self):
        from sql_migration_driver import candidate_events

        calls = []
        events = SimpleNamespace(
            listen=lambda target, name, fn: calls.append(("add", name)),
            remove=lambda target, name, fn: calls.append(("remove", name)),
        )
        driver = SimpleNamespace(
            **{
                n: lambda: None
                for n in (
                    "do_connect",
                    "connect",
                    "before_cursor_execute",
                    "checkin",
                    "close",
                )
            }
        )
        with self.assertRaises(RuntimeError):
            with candidate_events(driver, events, object()):
                raise RuntimeError("fixture")
        self.assertEqual(len(calls), 10)
        self.assertEqual(
            [n for op, n in calls if op == "remove"],
            list(reversed([n for op, n in calls if op == "add"])),
        )

    def test_beat_requires_unique_exact_original_wrapper(self):
        from beat_health_candidate import verify_processes

        commit = "a" * 40
        argv = [
            "/usr/local/bin/python",
            "/opt/release/start_067.py",
            "beat",
            "--expected-commit",
            commit,
            "--beat-db-format",
            "dbm.gnu",
        ]
        records = [{"pid": 42, "start_ticks": 300, "state": "S", "argv": argv}]
        self.assertEqual(verify_processes(records, 42, commit, "dbm.gnu")["pid"], 42)
        from sql_policy import SqlRejected

        for bad in (
            records * 2,
            [{**records[0], "pid": 43}],
            [{**records[0], "argv": argv + ["--unknown"]}],
            [{**records[0], "state": "Z"}],
        ):
            with self.assertRaises(SqlRejected):
                verify_processes(bad, 42, commit, "dbm.gnu")
