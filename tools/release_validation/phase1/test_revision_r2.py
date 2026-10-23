"""Static schedule regressions; synthetic bytes only, no object loading."""

import dbm.dumb
from datetime import timedelta, timezone
import pickle
import pickletools
import unittest
from unittest.mock import patch

import runtime_guard as guard
import schedule_pickle_guard as static
from schedule_test_fixture import entries_for, values_for
import test_revision_r1 as fixtures


class CorruptPickleRegression(unittest.TestCase):
    def rejected_before_celery(self, raw):
        fixture = fixtures.BeatRegression()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write_database()
        with dbm.dumb.open(str(fixture.base), "w") as database:
            database[b"entries"] = raw
        before = fixture.file_digests()
        with patch.object(pickle, "loads", side_effect=AssertionError("must not unpickle")):
            with self.assertRaises(guard.GuardError):
                fixture.dispatch(beat_db_format="dbm.dumb")
        fixture.celery.start.assert_not_called()
        self.assertEqual(before, fixture.file_digests())

    def test_empty_stack_stop(self):
        self.rejected_before_celery(b".")

    def test_undefined_memo(self):
        self.rejected_before_celery(b"g999\n.")

    def test_frame_overrun(self):
        self.rejected_before_celery(b"\x80\x04\x95" + (1000).to_bytes(8, "little") + b"N.")

    def test_illegal_protocol(self):
        self.rejected_before_celery(b"\x80\xffN.")

    def test_supported_protocol_stack_and_memo_errors(self):
        for tail in (
            b".",
            b"h\x00.",
            b"j\xe7\x03\x00\x00.",
            b"\x94.",
            b"NN.",
            b"\x85.",
            b"(N.",
            b"Nt.",
            b"u.",
        ):
            with self.subTest(tail=tail.hex()):
                self.rejected_before_celery(b"\x80\x04" + tail)

    def test_typed_stack_and_constructor_argument_errors(self):
        for tail in (
            b"NK\x01K\x02s.",
            b"}K\x01Nu.",
            b"}(\x8c\x01xu.",
            b"(NN\x93.",
            b"N)R.",
            b"NNb.",
        ):
            with self.subTest(tail=tail.hex()):
                self.rejected_before_celery(b"\x80\x04" + tail)

    def test_frame_mid_opcode_nested_truncated_and_trailing(self):
        def frame(size):
            return b"\x95" + size.to_bytes(8, "little")

        for tail in (
            frame(1) + b"\x8c\x01x.",
            frame(11) + frame(2) + b"N.",
            frame(1) + b"N.",
            frame(0) + b"N.",
            b"N.x",
        ):
            with self.subTest(tail=tail.hex()):
                self.rejected_before_celery(b"\x80\x04" + tail)

    def test_protocol_changes_and_unsupported_versions(self):
        for raw in (b"\x80\x03N.", b"\x80\x05N.", b"\x80\x04\x80\x04N."):
            with self.subTest(raw=raw.hex()):
                self.rejected_before_celery(raw)

    def test_unknown_global_never_executes(self):
        self.rejected_before_celery(b"\x80\x04\x8c\x02os\x8c\x06system\x93)R.")

    def test_memo_cycles_rejected(self):
        self.rejected_before_celery(b"\x80\x04}\x94\x8c\x01xh\x00s.")

    def test_oversize_integer_and_string(self):
        for value in (2**100, "x" * 1025):
            self.rejected_before_celery(pickle.dumps(value, protocol=4))


class ScheduleShapeRegression(unittest.TestCase):
    def setUp(self):
        self.expected = {"business-" + str(i): "task" + str(i) for i in range(7)}
        self.entries = entries_for(self.expected)

    def reject_entries(self):
        raw = pickle.dumps(self.entries, protocol=4)
        with patch.object(pickle, "loads", side_effect=AssertionError("no loads")):
            with self.assertRaises((guard.GuardError, ValueError)):
                static.validate_value(b"entries", raw, self.expected)

    def test_actual_celery_interval_and_crontab_shape_without_unpickling(self):
        values = values_for(self.expected)
        names = {op.name for op, arg, pos in pickletools.genops(values["entries"])}
        self.assertTrue(
            {"PROTO", "FRAME", "MEMOIZE", "BINGET", "STACK_GLOBAL", "REDUCE", "BUILD"} <= names
        )
        with patch.object(pickle, "loads", side_effect=AssertionError("no loads")), patch.object(
            pickle, "load", side_effect=AssertionError("no load")
        ), patch.object(pickle, "Unpickler", side_effect=AssertionError("no Unpickler")):
            for key, raw in values.items():
                static.validate_value(key.encode(), raw, self.expected)

    def test_full_synthetic_database_is_read_only_and_starts_once(self):
        fixture = fixtures.BeatRegression()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write_database()
        before = fixture.file_digests()
        with patch.object(pickle, "loads", side_effect=AssertionError("no loads")):
            fixture.dispatch(beat_db_format="dbm.dumb")
        self.assertEqual(before, fixture.file_digests())
        fixture.celery.start.assert_called_once()

    def test_entries_missing_extra_and_wrong_identity(self):
        original = dict(self.entries)
        self.entries.pop("business-0")
        self.reject_entries()
        self.entries = {**original, "unexpected": original["business-0"]}
        self.reject_entries()
        self.entries = original
        self.entries["business-0"].task = "wrong-task"
        self.reject_entries()

    def test_empty_entries_rejected(self):
        self.entries = {}
        self.reject_entries()

    def test_nonempty_task_args_kwargs_and_unknown_queue(self):
        entry = self.entries["business-0"]
        entry.args = ("injected",)
        self.reject_entries()
        entry.args, entry.kwargs = (), {"new_argument": True}
        self.reject_entries()
        entry.kwargs, entry.options = {}, {"queue": "unexpected"}
        self.reject_entries()

    def test_invalid_constructor_arity_is_rejected(self):
        class InertFixture:
            def __reduce__(self):
                return type(self.entries["business-0"]), ("missing-seven-args",)

        item = InertFixture()
        item.entries = self.entries
        self.entries["business-1"] = item
        self.reject_entries()

    def test_negative_count_wrong_type_and_naive_date_rejected(self):
        entry = self.entries["business-0"]
        entry.total_run_count = -1
        self.reject_entries()
        entry.total_run_count = True
        self.reject_entries()
        entry.total_run_count = 0
        entry.last_run_at = entry.last_run_at.replace(tzinfo=None)
        self.reject_entries()

    def test_non_utc_timezone_rejected(self):
        entry = self.entries["business-0"]
        entry.last_run_at = entry.last_run_at.replace(tzinfo=timezone(timedelta(hours=8)))
        self.reject_entries()

    def test_invalid_interval_options_and_duration(self):
        interval = self.entries["business-1"].schedule
        interval.relative = True
        self.reject_entries()
        interval.relative = False
        interval.run_every = timedelta(0)
        self.reject_entries()

    def test_crontab_unsupported_range_and_state(self):
        cron = self.entries["business-0"].schedule
        original = cron._orig_minute
        for value in (60, "*/0", "*/100", "bad", "1-4"):
            cron._orig_minute = value
            self.reject_entries()
        cron._orig_minute = original
        cron._orig_kwargs = {"nowfun": "unknown"}
        self.reject_entries()

    def test_wrong_metadata_type_or_value(self):
        for key, value in (
            (b"tz", "Asia/Shanghai"),
            (b"tz", None),
            (b"utc_enabled", False),
            (b"utc_enabled", 1),
            (b"__version__", ""),
            (b"__version__", "6.0.0"),
            (b"unexpected", {}),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(guard.GuardError):
                static.validate_value(key, pickle.dumps(value, protocol=4), self.expected)

    def test_exact_metadata_keys_in_database(self):
        fixture = fixtures.BeatRegression()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write_database()
        with dbm.dumb.open(str(fixture.base), "w") as database:
            database[b"unexpected"] = pickle.dumps(None, protocol=4)
        with self.assertRaisesRegex(guard.GuardError, "schedule_metadata"):
            fixture.dispatch(beat_db_format="dbm.dumb")
        fixture.celery.start.assert_not_called()

    def test_parser_budget_and_depth(self):
        with patch.object(static, "MAX_OPS", 2), self.assertRaises(guard.GuardError):
            static.parse(pickle.dumps("UTC", protocol=4))
        root = static.Node("atom", None)
        for _ in range(static.MAX_DEPTH + 1):
            root = static.Node("tuple", (root,))
        with self.assertRaisesRegex(guard.GuardError, "schedule_shape_budget"):
            static.check_graph(root)

    def test_multiple_complete_frames_and_unframed_atoms_are_supported(self):
        # This protocol-4 tuple is split at whole-opcode boundaries, not inside an operand.
        raw = b"\x80\x04\x95" + (1).to_bytes(8, "little") + b"N"
        raw += b"\x95" + (2).to_bytes(8, "little") + b"\x85."
        root = static.parse(raw)
        self.assertEqual(root.kind, "tuple")
        self.assertTrue(static.is_atom(root.value[0], None))
        static.validate_value(b"utc_enabled", b"\x80\x04\x88.", self.expected)

    def test_invalid_datetime_bytes_rejected(self):
        class BadDate:
            def __reduce__(self):
                from datetime import datetime

                return datetime, (b"\x07\xea\x02\x1e\x00\x00\x00\x00\x00\x00", timezone.utc)

        self.entries["business-0"].last_run_at = BadDate()
        self.reject_entries()


if __name__ == "__main__":
    unittest.main(verbosity=2)
