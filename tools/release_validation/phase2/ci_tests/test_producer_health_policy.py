"""Portable rejection contracts; actual process overlap is a separate Docker job."""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contracts import Rejected
from host_producer_control import (
    HEALTH,
    PROBE,
    SHELL_PROBE,
    _health_pair,
    original_healthcheck,
    validate_probe_pair,
)


def fixture():
    main = {
        "pid": 100,
        "ppid": 9,
        "start_ticks": 100,
        "exe": [1, 10],
        "namespaces": {k: k + ":[1]" for k in ("pid", "mnt", "net", "user")},
        "uids": [0] * 4,
        "cgroup": "0::/fixture\n",
    }
    shell = dict(
        copy.deepcopy(main),
        pid=200,
        start_ticks=900,
        argv=["/bin/sh", "-c", SHELL_PROBE],
        exe=[1, 20],
        tasks=[200],
        children=[201],
    )
    python = dict(
        copy.deepcopy(main),
        pid=201,
        ppid=200,
        start_ticks=901,
        argv=["python", "-c", PROBE],
        tasks=[201],
        children=[],
    )
    return main, [shell, python]


class ProbePolicy(unittest.TestCase):
    def test_complete_original_pair(self):
        main, rows = fixture()
        self.assertEqual(validate_probe_pair(main, rows, [1, 20], 1000, 100), rows)

    def test_matching_argv_is_insufficient(self):
        cases = [
            (0, "ppid", 100),
            (0, "ppid", 8),
            (1, "ppid", 100),
            (0, "exe", [1, 99]),
            (1, "exe", [1, 99]),
            (0, "uids", [1] * 4),
            (1, "cgroup", "0::/other\n"),
            (1, "namespaces", {"pid": "other"}),
            (0, "tasks", [200, 202]),
            (1, "children", [202]),
            (0, "children", []),
            (0, "start_ticks", 99),
            (1, "start_ticks", 499),
            (1, "start_ticks", 1001),
            (1, "argv", ["python", "-c", PROBE.replace("timeout=3", "timeout=4")]),
        ]
        for index, key, value in cases:
            with self.subTest(index=index, key=key):
                main, rows = fixture()
                rows[index][key] = value
                with self.assertRaises(Rejected):
                    validate_probe_pair(main, rows, [1, 20], 1000, 100)

    def test_partial_and_extra_processes_hold(self):
        main, rows = fixture()
        for changed in ([], rows[:1], rows[1:], rows + [copy.deepcopy(rows[1])]):
            with (
                self.subTest(count=len(changed)),
                self.assertRaisesRegex(Rejected, "incomplete_pair"),
            ):
                validate_probe_pair(main, changed, [1, 20], 1000, 100)


class ProbeRace(unittest.TestCase):
    def test_stable_pair_and_disappearing_or_replaced_probe(self):
        main, rows = fixture()
        identities = [
            {k: v for k, v in r.items() if k not in ("tasks", "children")} for r in rows
        ]
        changed = copy.deepcopy(identities[0])
        changed["start_ticks"] += 1
        for later in (identities[0], changed, FileNotFoundError()):
            sequence = [main, *identities, later, identities[1], main]
            with (
                self.subTest(later=later),
                patch("host_producer_control._health_identity", side_effect=sequence),
                patch(
                    "host_producer_control.os.listdir",
                    side_effect=lambda p: [p.split("/")[-2]],
                ),
                patch(
                    "host_producer_control.os.stat",
                    return_value=SimpleNamespace(st_dev=1, st_ino=20),
                ),
                patch(
                    "host_producer_control.os.sysconf",
                    return_value=100,
                    create=True,
                ),
                patch("host_producer_control.time.CLOCK_BOOTTIME", 7, create=True),
                patch(
                    "host_producer_control.time.clock_gettime",
                    return_value=10,
                    create=True,
                ),
                patch(
                    "host_producer_control._read",
                    side_effect=lambda p: b"201" if "/200/" in p else b"",
                ),
            ):
                if later == identities[0]:
                    self.assertEqual(_health_pair(100, {100, 200, 201}), rows)
                else:
                    with self.assertRaisesRegex(Rejected, "sample_changed"):
                        _health_pair(100, {100, 200, 201})


def inspect_now(identity, pid):
    # _capture is an immediate AsyncMock: do not create an event loop or import
    # the process-denying synthetic suite into the real-Git source contracts.
    operation = original_healthcheck(identity, pid)
    try:
        operation.send(None)
    except StopIteration as completed:
        return completed.value
    finally:
        operation.close()
    raise AssertionError("unexpected asynchronous I/O in mocked inspect")


class NarrowInspect(unittest.TestCase):
    def test_exact_bound_projection(self):
        identity = "a" * 64
        raw = b"|".join(json.dumps(v).encode() for v in (identity, 123, HEALTH))
        with patch(
            "host_producer_control._capture", AsyncMock(return_value=raw)
        ) as capture:
            self.assertEqual(inspect_now(identity, 123), HEALTH)
        argv = capture.call_args.args[0]
        self.assertEqual(
            argv[-4:],
            (
                "inspect",
                "--format",
                "{{json .Id}}|{{json .State.Pid}}|{{json .Config.Healthcheck}}",
                identity,
            ),
        )
        self.assertNotIn("Env", " ".join(argv))

    def test_wrong_identity_pid_or_any_health_option_holds(self):
        identity = "a" * 64
        cases = [("b" * 64, 123, HEALTH), (identity, 124, HEALTH)]
        for key in HEALTH:
            health = copy.deepcopy(HEALTH)
            health[key] = 0
            cases.append((identity, 123, health))
        for values in cases:
            raw = b"|".join(json.dumps(v).encode() for v in values)
            with (
                self.subTest(values=values),
                patch("host_producer_control._capture", AsyncMock(return_value=raw)),
                self.assertRaises(Rejected),
            ):
                inspect_now(identity, 123)


class CompletePairFixture(unittest.TestCase):
    def test_half_pair_then_main_only_requires_later_stable_pair(self):
        import run_producer_healthcheck as fixture

        pair = {
            "healthcheck_processes": [
                {"pid": 2, "start_ticks": 20},
                {"pid": 3, "start_ticks": 21},
            ]
        }
        holds = {"count": 0, "samples": []}
        sequence = [
            Rejected("producer_uncovered_process"),
            Rejected("producer_healthcheck_incomplete_pair"),
            {},
            Rejected("producer_uncovered_process"),
            pair,
            copy.deepcopy(pair),
        ]
        with (
            patch.object(fixture, "process_group", side_effect=sequence) as sample,
            patch.object(fixture, "same_group"),
            patch.object(fixture, "process_diagnostic", return_value={}),
            patch.object(fixture.time, "monotonic", return_value=0),
            patch.object(fixture.time, "sleep"),
        ):
            result = fixture.observe_complete_pair(1, {}, HEALTH, holds)
        self.assertTrue(result["stable_pair_reobserved"])
        self.assertEqual(sample.call_count, 6)
        self.assertEqual(holds["count"], 1)
        self.assertEqual(
            holds["samples"][0]["release_gate"],
            "HOLD_producer_healthcheck_incomplete_pair",
        )

    def test_probe_disappearing_is_hold_and_never_positive_success(self):
        import run_producer_healthcheck as fixture

        holds = {"count": 0, "samples": []}
        with (
            patch.object(
                fixture,
                "process_group",
                side_effect=[
                    Rejected("producer_uncovered_process"),
                    {"healthcheck_processes": []},
                ],
            ),
            patch.object(fixture, "same_group"),
            patch.object(fixture, "process_diagnostic", return_value={}),
            patch.object(fixture.time, "monotonic", side_effect=[0, 0, 26]),
            patch.object(fixture.time, "sleep"),
            self.assertRaisesRegex(Rejected, "complete_pair_not_observed"),
        ):
            fixture.observe_complete_pair(1, {}, HEALTH, holds)
        self.assertEqual(holds["count"], 1)

    def test_unsafe_probe_is_not_retried(self):
        import run_producer_healthcheck as fixture

        holds = {"count": 0, "samples": []}
        with (
            patch.object(
                fixture,
                "process_group",
                side_effect=[
                    Rejected("producer_uncovered_process"),
                    Rejected("producer_healthcheck_argv"),
                ],
            ) as sample,
            patch.object(fixture.time, "monotonic", return_value=0),
            patch.object(fixture.time, "sleep") as sleep,
            self.assertRaisesRegex(Rejected, "healthcheck_argv"),
        ):
            fixture.observe_complete_pair(1, {}, HEALTH, holds)
        self.assertEqual(sample.call_count, 2)
        sleep.assert_not_called()
        self.assertEqual(holds["count"], 0)


if __name__ == "__main__":
    unittest.main()
