"""Hand-built inert opcodes for the exact live Celery UTC ZoneInfo shape."""

import hashlib
import pickle
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [
    str(Path(__file__).resolve().parents[2] / "phase1"),
    str(Path(__file__).resolve().parents[1]),
]
import schedule_pickle_guard as guard
from runtime_guard import GuardError


def text(value):
    raw = value.encode()
    assert len(raw) < 256
    return b"\x8c" + bytes([len(raw)]) + raw


def symbol(module, name):
    return text(module) + text(name) + b"\x93"


def zone(*, attribute="_unpickle", key="UTC", cache=b"K\x01", target=None):
    target = symbol("zoneinfo", "ZoneInfo") if target is None else target
    return (
        symbol("builtins", "getattr")
        + target
        + text(attribute)
        + b"\x86R"
        + text(key)
        + cache
        + b"\x86R"
    )


def entries(zone_raw, *, stamp=b"\x07\xea\x0a\x19\x09\x00\x00\x00\x00\x00"):
    expected = {"business-" + str(i): "task" + str(i) for i in range(7)}
    when = symbol("datetime", "datetime") + b"C\x0a" + stamp + zone_raw + b"\x86R"
    interval = symbol("datetime", "timedelta") + b"K\x00K\x3cK\x00\x87R"
    schedule = symbol("celery.schedules", "schedule") + interval + b"\x89N\x87R"
    raw = b"\x80\x04}"
    for name, task in expected.items():
        raw += (
            text(name)
            + symbol("celery.beat", "ScheduleEntry")
            + b"("
            + text(name)
            + text(task)
            + when
            + b"K\x01"
            + schedule
            + b")}"
            + b"}"
            + text("queue")
            + text("default")
            + b"stRs"
        )
    return raw + b".", expected


class ZoneInfoShapeTests(unittest.TestCase):
    def validate(self, raw, expected):
        with (
            patch.object(pickle, "loads", side_effect=AssertionError("no loads")),
            patch.object(pickle, "load", side_effect=AssertionError("no load")),
            patch.object(
                pickle, "Unpickler", side_effect=AssertionError("no Unpickler")
            ),
            patch.dict(sys.modules, {"zoneinfo": None}),
        ):
            guard.validate_value(b"entries", raw, expected)

    def test_exact_chain_seven_entries_without_loading(self):
        self.validate(*entries(zone()))

    def test_other_zone_property_cache_and_target_rejected(self):
        for raw in (
            zone(key="Europe/London"),
            zone(key="Etc/UTC"),
            zone(attribute="clear_cache"),
            zone(attribute="__new__"),
            zone(cache=b"K\x00"),
            zone(cache=b"K\x02"),
            zone(cache=b"\x88"),
            zone(target=symbol("datetime", "datetime")),
            zone(target=text("zoneinfo.ZoneInfo")),
        ):
            with self.subTest(raw=raw.hex()), self.assertRaises(GuardError):
                self.validate(*entries(raw))

    def test_constructor_and_unknown_global_rejected(self):
        for raw in (
            symbol("zoneinfo", "ZoneInfo") + text("UTC") + b"\x85R",
            symbol("builtins", "eval") + text("UTC") + b"\x85R",
            symbol("zoneinfo", "reset_tzpath") + b")R",
            symbol("builtins", "getattr") + b")R",
        ):
            with self.subTest(raw=raw.hex()), self.assertRaises(GuardError):
                self.validate(*entries(raw))

    def test_incomplete_chain_extra_args_and_state_rejected(self):
        callable_raw = (
            symbol("builtins", "getattr")
            + symbol("zoneinfo", "ZoneInfo")
            + text("_unpickle")
            + b"\x86R"
        )
        for raw in (
            callable_raw,
            callable_raw + text("UTC") + b"\x85R",
            callable_raw + text("UTC") + b"K\x01N\x87R",
            zone() + b"}b",
        ):
            with self.subTest(raw=raw.hex()), self.assertRaises(GuardError):
                self.validate(*entries(raw))

    def test_token_not_accepted_as_metadata_or_datetime_fold(self):
        with self.assertRaises(GuardError):
            guard.validate_value(b"tz", b"\x80\x04" + zone() + b".", {})
        with self.assertRaises(GuardError):
            self.validate(
                *entries(zone(), stamp=b"\x07\xea\x8a\x19\x09\x00\x00\x00\x00\x00")
            )

    def test_current_dependency_is_only_the_corrected_exact_bytes(self):
        import phase1_package_binding as binding
        from beat_health_candidate import _frozen_start

        path = Path(guard.__file__)
        raw = path.read_bytes()
        pin = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.assertEqual(pin, binding.SCHEDULE_GUARD_AFTER)
        self.assertNotEqual(pin, binding.SCHEDULE_GUARD_BEFORE)
        self.assertEqual(
            binding.tool_manifest()["frozen_phase1/schedule_pickle_guard.py"], pin
        )
        self.assertTrue(callable(_frozen_start().validate_existing_schedule))
        with self.assertRaises(binding.PackageRejected):
            binding.schedule_guard_pin("schedule_pickle_guard.py", pin)
        read = Path.read_bytes
        with (
            patch.object(
                Path, "read_bytes", lambda p: raw + b"\n" if p == path else read(p)
            ),
            self.assertRaisesRegex(binding.PackageRejected, "phase1_control_drift"),
        ):
            _frozen_start()


if __name__ == "__main__":
    unittest.main()
